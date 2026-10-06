"""
DeepSeek 客户端 — 用于后端 AI 服务。

模型:deepseek-flash (官方 model id, 指向 DeepSeek-V4.1-Flash)
协议:OpenAI ChatCompletions 兼容
默认 base_url:https://api.deepseek.com

⚠️ 严格回答范围 — 所有调用必须带上 system guard,把模型限制在
   "英语学习 + 本周文章"范围内,其他话题一律拒答。
"""
from __future__ import annotations
import json
import os
import re
import logging
from typing import Iterable, Optional

_re_cjk = re.compile(r"[\u4e00-\u9fff]")

log = logging.getLogger(__name__)

# 官方 OpenAI 兼容端点
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"        # DeepSeek-V4.1-Flash
# 兼容旧名字:如果你有 V4-Flash 的 key,临时也能用 deepseek-v4-flash

# 单次请求不再设人为的小预算 —— 之前写作批改被截断(正文只剩一句残片、
# 分数行整个丢失)就是因为 max_tokens 给得比模型的思考量还小。
# 这个值只是"高到永远不会成为约束"的安全上限(实测接口接受 65536),
# 不是成本杠杆:成本由每月总配额兜底(server.main 的
# AI_MONTHLY_TOKEN_LIMIT,默认 20 万 tokens/月),长度交给 system prompt
# 要求模型自己精简。
MAX_OUTPUT_TOKENS = 32768

SYSTEM_PROMPT = """You are a calm, precise English-language tutor for high-school students.

STRICT SCOPE — refuse to answer anything outside it.
Allowed topics:
1. The current week's articles loaded in this conversation (vocabulary, sentence meaning, background, comprehension).
2. English-learning questions (grammar, usage, idioms, study methods, vocabulary strategy).
3. Brief cultural or factual context necessary to understand this week's articles.

Refuse (reply: "This question is outside the scope of this weekly English tutor.") for:
- Personal chat, roleplay, jokes, opinions on politics / religion / philosophy.
- Homework cheating, exam answers, or "write my essay for me" style requests.
- Anything unrelated to this week's articles or English learning.

Style:
- Be economical. Give the shortest reply that still fully helps: no restating the
  question, no preamble, no summary of what you are about to say, no filler, no
  closing pleasantries.
- Plain, short sentences. No emoji. No marketing tone.
- For vocabulary: Chinese gloss in parentheses after the English definition.
- Cite the article source if you draw from a specific passage.

Links and browsing — hard rules:
- NEVER output URLs, links, or "visit this page" suggestions.
- You cannot browse the web. Never claim to have looked something up online,
  and never promise to fetch or check anything. Answer only from the context
  and your own general knowledge."""


def _env(name: str) -> Optional[str]:
    v = os.environ.get(name)
    return v.strip() if v else None


def _cfg(*groups: str) -> dict:
    """按顺序取第一个配置了 API_KEY 的组;每组形如 {G}_API_KEY / {G}_BASE_URL / {G}_MODEL。
    任何 OpenAI ChatCompletions 兼容端点都可用(DeepSeek / 智谱 BigModel / Kimi / 通义 等)。
    例:ask() 用 _cfg("ASK", "DEEPSEEK", "LLM") — 交流优先 DeepSeek;
        查词已去 AI(见 dict_client),不再有单词 lookup 的模型调用。"""
    for g in groups:
        key = _env(f"{g}_API_KEY")
        if key:
            return {
                "api_key": key,
                "base_url": _env(f"{g}_BASE_URL") or DEFAULT_BASE_URL,
                "model": _env(f"{g}_MODEL") or DEFAULT_MODEL,
                "group": g,
            }
    return {"api_key": None, "base_url": DEFAULT_BASE_URL,
            "model": DEFAULT_MODEL, "group": None}


def _thinking_extra(cfg: dict) -> dict:
    """仅智谱端点才注入 thinking 参数 — DeepSeek 等端点发送未知参数会报错。
    当前全部接口走 DeepSeek:LLM_THINKING_LEVEL 不设(或端点非智谱)即不发送。"""
    lvl = _env("LLM_THINKING_LEVEL")
    if lvl and "bigmodel" in (cfg.get("base_url") or ""):
        return {"extra_body": {"thinking": {"level": lvl}}}
    return {}


def is_configured() -> bool:
    return bool(_cfg("LLM", "ASK", "DEEPSEEK")["api_key"])


def _client(cfg: dict, timeout: float = 30.0):
    """注意:这里创建的客户端是阻塞式的,调用方必须跑在线程里(同步路由),
    不能放在 async 路由里直接调 —— 会把事件循环占死。"""
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError("openai package missing; pip install openai>=1.0") from e
    # 必须带超时:上游挂住时没有超时就是无限等,调用方(线程池)会被一个个拖干,
    # 整站跟着变慢。重试交给 openai 自己的退避,只重试 1 次。
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"],
                  timeout=timeout, max_retries=1)


def ask(question: str,
        context_articles: Iterable[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        focus_article: dict | None = None) -> dict:
    """
    调用 DeepSeek-V4.1-Flash 回答用户问题,自动带严格 system guard。
    context_articles: 本期文章清单(标题/来源) — 让模型知道这周有什么。
    focus_article: 用户正在读的那一篇,带正文摘录(前 ~4000 字符) —
                   没有它,模型只看得到标题,问正文内容会被误判"超范围"。
    返回:{ok, content, refused, model}

    单次输出不设人为上限(见 MAX_OUTPUT_TOKENS):答案长度交给 system prompt
    要求精简,成本由每月总配额兜底。DeepSeek 是思考型模型,思考也吃 tokens,
    偶尔会把内容全写进 reasoning_content(→ content 为空),所以 content 空时
    从思考里取结论;再不行才重问一次。
    """
    cfg = _cfg("ASK", "DEEPSEEK", "LLM")
    if not cfg["api_key"]:
        return {"ok": False, "refused": False,
                "content": "LLM is not configured. Set ASK_API_KEY / LLM_API_KEY in .env.",
                "model": DEFAULT_MODEL}

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if focus_article and focus_article.get("body"):
        # 不给 URL —— 链接喂进去模型就可能原样吐给读者(system prompt 已禁,这里断供)
        messages.append({"role": "system", "content": "\n".join([
            "The user is currently reading this article (full text, read-only context):",
            f"Title: {focus_article.get('title', '')}",
            f"Source: {focus_article.get('source', '')}",
            "--- article body ---",
            focus_article["body"],
        ])})
    if context_articles:
        ctx_lines = ["Other articles in this week's issue (titles only):"]
        for a in context_articles[:6]:
            ctx_lines.append(
                f"- [{a.get('source','?')}] {a.get('title','')} — "
                f"{a.get('published','')[:10]}"
            )
        messages.append({"role": "system", "content": "\n".join(ctx_lines)})

    messages.append({"role": "user", "content": question.strip()})

    def _extract(msg) -> str:
        """content 优先;为空时取思考文本的最后一个非空段落(结论通常在末尾)。"""
        text = (msg.content or "").strip()
        if text:
            return text
        rc = getattr(msg, "reasoning_content", None) or ""
        paras = [p.strip() for p in rc.split("\n") if p.strip()]
        return paras[-1][:600] if paras else ""

    try:
        client = _client(cfg, timeout=60.0)   # 问答要生成一整段,给足时间
        last: dict = {}
        for attempt in range(2):
            resp = client.chat.completions.create(
                model=model or cfg["model"],
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=temperature,
            )
            msg = resp.choices[0].message
            content = _extract(msg).strip()
            usage = getattr(resp, "usage", None)
            last = {"ok": True, "refused": "outside the scope" in content.lower(),
                    "content": content,
                    "model": model or cfg["model"],
                    "usage_tokens": getattr(usage, "total_tokens", 0) or 0}
            if content:
                return last
            log.warning("deepseek ask returned empty content (attempt %d)", attempt + 1)
        # 两次都空:给句能读的提示,别让前端落到 "(no answer)"
        last["content"] = "AI 没能生成回答，请换个问法再试一次。"
        return last
    except Exception as e:
        log.warning("deepseek ask failed: %s", e)
        return {"ok": False, "refused": False,
                "content": "AI service is temporarily unavailable.", "model": DEFAULT_MODEL}


_zh_summary_cache: dict[str, str] = {}

_SUMMARY_INSTRUCTION = '用一句不超过 40 字的简体中文概括这篇文章讲什么。只输出这句话本身。'


def _summary_prompt(title: str, body: str) -> str:
    """简介 prompt(内联 summarize_zh 与批量接口共用,保证口径一致)。"""
    body_snip = " ".join((body or "").split())[:600]
    return ('文章标题: ' + title + '\n正文开头: ' + body_snip + '\n'
            + _SUMMARY_INSTRUCTION)


def summarize_zh(title: str, body: str, model: str | None = None) -> str:
    """生成一句话中文简介(卡片用)。失败返回空串,调用方回退到英文摘要。"""
    ck = title
    if ck in _zh_summary_cache:
        return _zh_summary_cache[ck]
    cfg = _cfg("LLM", "DEEPSEEK", "ASK")
    if not cfg["api_key"]:
        return ""
    prompt = _summary_prompt(title, body)
    try:
        extra = _thinking_extra(cfg)
        client = _client(cfg)
        out = ""
        for attempt in range(3):   # 失败/无中文输出时重试;最后一次仅用标题
            use_prompt = prompt if attempt < 2 else (
                '文章标题: ' + title + '\n' + _SUMMARY_INSTRUCTION)
            resp = client.chat.completions.create(
                model=model or cfg["model"],
                messages=[{"role": "user", "content": use_prompt}],
                max_tokens=MAX_OUTPUT_TOKENS, temperature=0.2, **extra)
            msg = resp.choices[0].message
            lines = (msg.content or "").strip().splitlines()
            out = lines[0].strip() if lines else ""
            if not (out and _re_cjk.search(out)):
                # 思考型模型正文为空/无中文 → 从 reasoning_content 里找中文句子
                rc = getattr(msg, "reasoning_content", None) or ""
                for piece in re.split(r"[\n。;；]", rc):
                    cand = piece.strip()
                    if cand and _re_cjk.search(cand) and 6 <= len(cand) <= 80:
                        out = cand
                        break
            if out and _re_cjk.search(out):
                break
            out = ""
        if out:
            if len(_zh_summary_cache) > 600:
                _zh_summary_cache.clear()
            _zh_summary_cache[ck] = out
        return out
    except Exception as e:
        log.warning("summarize_zh failed: %s", e)
        return ""


# ---------------- 雅思专区 ----------------

IELTS_SYSTEM_PROMPT = """You are a strict but encouraging IELTS examiner and tutor.

STRICT SCOPE — refuse to answer anything outside it.
Allowed topics: IELTS preparation only — Writing Task 1/2, Speaking Part 1/2/3,
Listening, Reading, vocabulary and grammar for IELTS, band descriptors, test strategy.

Refuse (reply: "This question is outside the scope of the IELTS tutor.") for:
- Personal chat, roleplay, politics, religion, philosophy.
- Homework cheating or exam answers for other tests.
- Anything unrelated to IELTS preparation.

Style:
- Be economical. Feedback must be as short as it can be while still being useful:
  no restating the essay or question back, no preamble, no commentary about your own
  process or how you are scoring, no closing pleasantries. Do not pad a section to
  look thorough.
- Feedback in simplified Chinese; quote the learner's English verbatim when pointing at it.
- Band scores use the official 0–9 scale (may use .5 steps).
- Plain text only. No emoji. No markdown tables.

SPEAKING — hard limit on what you may assess:
You only ever receive a written transcript. You cannot hear the candidate, so you
must never score, estimate or guess Pronunciation or Fluency. Assess only what the
text supports: grammatical accuracy, lexical range and appropriacy, sentence
structure, coherence and idea development, task response. State plainly that
pronunciation and fluency need a recording and are out of scope in this mode."""


def _ielts_chat(messages: list[dict], max_tokens: int,
                reasoning_fallback: bool = True) -> dict:
    """雅思共用的阻塞调用(调用方必须跑在线程池,同步 def 路由)。

    reasoning_fallback: content 为空时是否退回思考文本。批改/结构化输出必须传
    False —— 思考流里写的是 "Let's refine scoring..." 这类过程独白,当答案返回
    等于给用户看推理残渣。
    """
    cfg = _cfg("ASK", "DEEPSEEK", "LLM")
    if not cfg["api_key"]:
        return {"ok": False, "refused": False,
                "content": "LLM is not configured. Set ASK_API_KEY / LLM_API_KEY in .env.",
                "model": DEFAULT_MODEL}
    messages = [{"role": "system", "content": IELTS_SYSTEM_PROMPT}] + messages
    try:
        client = _client(cfg, timeout=120.0)   # 整篇作文批改要思考 + 长输出,给足时间
        resp = client.chat.completions.create(
            model=cfg["model"], messages=messages,
            max_tokens=max_tokens, temperature=0.3)
        msg = resp.choices[0].message
        content = (msg.content or "").strip()
        if not content and reasoning_fallback:
            rc = getattr(msg, "reasoning_content", None) or ""
            paras = [p.strip() for p in rc.split("\n") if p.strip()]
            content = paras[-1][:1200] if paras else ""
        usage = getattr(resp, "usage", None)
        return {"ok": True,
                "refused": "outside the scope" in content.lower(),
                "content": content, "model": cfg["model"],
                "usage_tokens": getattr(usage, "total_tokens", 0) or 0}
    except Exception as e:
        log.warning("ielts llm failed: %s", e)
        return {"ok": False, "refused": False,
                "content": "AI service is temporarily unavailable.",
                "model": DEFAULT_MODEL}


def ielts_writing_feedback(question: str, essay: str) -> dict:
    """Task 2 批改:按 TR/CC/LR/GRA 四项打分 + 总分 + 中文改进建议。

    分数单独一行输出,由 _split_scores 解析出来供前端渲染成分数卡。单次调用不设
    人为上限(见 MAX_OUTPUT_TOKENS):模型思考也要吃 tokens,预算给得比思考量还小
    时 content 会被截成一句残片、分数行整个丢失 —— 这是之前批改坏掉的根因。现在
    长度靠提示词要求精简,成本靠每月总配额兜底。

    只在"格式没解析出来"时重问一次:那是正确性问题(拿不到分数行前端就只能退回
    纯文本),不是预算问题。思考流不是答案,所以关掉 reasoning 兜底。
    """
    head = (
        "直接输出批改结果,不要复述下面的要求。\n"
        "**第一行必须严格是这个格式,不要加别的内容**:\n"
        "SCORES: TR=6.5|CC=6.0|LR=6.5|GRA=6.0|OA=6.0\n"
        "(按官方量表实事求是地打分,0-9 可用 .5;OA 取四项平均后按雅思规则到 .5;\n"
        " 不要照抄示例数字,那是格式示例)\n"
        "空一行后分三节,每节都尽量精简:\n"
        "【主要问题】最多 3 条,每条一两句话,引用作文原句指出问题\n"
        "【改进建议】最多 3 条,给出可直接替换的表达\n"
        "【提升到 7 分还差什么】一句话\n\n"
    )
    tail = (f"题目:\n{(question or 'Some people believe that... (题目未提供,按一般议论文评)').strip()}\n\n"
            f"作文:\n{essay.strip()[:6000]}")
    cfg = _cfg("ASK", "DEEPSEEK", "LLM")
    info: dict = {}
    best = ""
    spent = 0
    for attempt in range(2):
        note = "" if attempt == 0 else (
            "\n注意:上一次的第一行不是 SCORES 那一行。请让**第一行**就是 "
            "SCORES: TR=...|CC=...|LR=...|GRA=...|OA=... ,然后才是三节建议。")
        info = _ielts_chat(
            [{"role": "user", "content":
              "请按雅思官方评分标准批改下面这篇 Writing Task 2 作文。\n"
              + head + note + tail}],
            max_tokens=MAX_OUTPUT_TOKENS, reasoning_fallback=False)
        if not info.get("ok") or info.get("refused"):
            return info
        spent += info.get("usage_tokens") or 0
        raw = info.get("content") or ""
        scores, body = _split_scores(raw)
        if scores and len(body) >= 80:
            info["scores"] = scores
            info["content"] = body
            info["usage_tokens"] = spent
            return info
        if len(raw) > len(best):
            best = raw
        log.info("ielts writing: no parseable SCORES (attempt %d, %d chars)",
                 attempt + 1, len(raw))
    # 分数行始终没出来:有正文就退回纯文本展示(前端会按纯文本渲染),
    # 正文也空才如实报失败 —— 别把思考残渣当反馈给出去
    if best.strip():
        info["content"] = best
        info["usage_tokens"] = spent
        return info
    return {"ok": False, "refused": False,
            "content": "AI 这次没能生成批改,请再试一次。",
            "model": cfg.get("model") or DEFAULT_MODEL, "usage_tokens": spent}


# 模型被要求把分数写在第一行;容忍空格、逗号分隔与缺项顺序
_SCORES_RE = re.compile(
    r"^[^\n]*?SCORES:\s*"
    r"TR\s*=\s*(\d(?:\.\d)?)\s*(?:[|,]\s*)"
    r"CC\s*=\s*(\d(?:\.\d)?)\s*(?:[|,]\s*)"
    r"LR\s*=\s*(\d(?:\.\d)?)\s*(?:[|,]\s*)"
    r"GRA\s*=\s*(\d(?:\.\d)?)\s*(?:[|,]\s*)"
    r"OA\s*=\s*(\d(?:\.\d)?)",
    re.M | re.I)


def _split_scores(content: str) -> tuple[dict | None, str]:
    """从批改文本里抽出 SCORES 行;抽到就把它从正文里删掉(不重复展示)。"""
    m = _SCORES_RE.search(content or "")
    if not m:
        return None, content
    keys = ["TR", "CC", "LR", "GRA", "OA"]
    scores = {k: float(m.group(i + 1)) for i, k in enumerate(keys)}
    body = (content[:m.start()] + content[m.end():]).strip()
    return scores, body


_SPEAKING_PART_GUIDE = {
    1: "Part 1: ask short everyday questions one at a time (hometown, work/study, hobbies...). Keep each question to one sentence.",
    2: "Part 2: give the candidate ONE cue card (topic + 3-4 bullet points + 1 minute to think, speak up to 2 minutes). After their answer, ask one follow-up question.",
    3: "Part 3: ask abstract discussion questions related to Part 2 topics, one at a time, going deeper with follow-ups.",
}


def ielts_speaking_turn(history: list[dict], part: int, action: str) -> dict:
    """口语模拟一轮。history = [{role:'examiner'|'candidate', text}]。
    action: 'start' 开新题 | 'answer' 后接考生最新回答(已并入 history)。"""
    part = part if part in _SPEAKING_PART_GUIDE else 1
    transcript = "\n".join(
        f"{'Examiner' if h.get('role') == 'examiner' else 'Candidate'}: {h.get('text','')}"
        for h in history[-16:])
    instruction = (
        f"Simulate the IELTS Speaking test. {_SPEAKING_PART_GUIDE[part]}\n"
        "Respond ONLY as the examiner: ask the next question (do not answer it yourself).\n"
        "If the current request asks you to wrap up, instead output '===FEEDBACK===' then give "
        "feedback that respects the SPEAKING hard limit from your instructions:\n"
        "- Assess ONLY grammar accuracy, vocabulary range/appropriacy, sentence structure, "
        "coherence and idea development, and task response.\n"
        "- Do NOT give any band or comment for pronunciation or fluency — the candidate's "
        "audio was never available. Open the feedback by saying so in one sentence.\n"
        "- Give a band for '语言维度(语法/词汇/句式/连贯)' only, clearly labelled as such.\n"
        "- List 3 weaknesses quoting their original sentences, and 3 better expressions "
        "they could have used. All feedback in Chinese.\n"
        "Now output your next examiner turn (or the feedback if wrapping up).\n\n"
        f"Transcript so far:\n{transcript}\n\nCurrent request: {action.strip()}"
    )
    info = _ielts_chat([{"role": "user", "content": instruction}],
                       max_tokens=MAX_OUTPUT_TOKENS, reasoning_fallback=False)
    if info.get("ok") and not (info.get("content") or "").strip():
        # 思考吃光预算时 content 会空 —— 思考流是过程独白,不能当考官的话发给学生
        info["ok"] = False
        info["content"] = "AI 这次没能生成内容,请再点一次。"
    return info

