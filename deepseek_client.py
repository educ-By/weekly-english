"""
DeepSeek 客户端 — 用于后端 AI 服务。

模型:deepseek-flash (官方 model id, 指向 DeepSeek-V4.1-Flash)
协议:OpenAI ChatCompletions 兼容
默认 base_url:https://api.deepseek.com

⚠️ 严格回答范围 — 所有调用必须带上 system guard,把模型限制在
   "英语学习 + 本周文章"范围内,其他话题一律拒答。
"""
from __future__ import annotations
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
- Plain, short sentences. No emoji. No marketing tone.
- For vocabulary: Chinese gloss in parentheses after the English definition.
- Cite the article source if you draw from a specific passage."""


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
        max_tokens: int = 900,
        temperature: float = 0.3,
        focus_article: dict | None = None) -> dict:
    """
    调用 DeepSeek-V4.1-Flash 回答用户问题,自动带严格 system guard。
    context_articles: 本期文章清单(标题/来源/URL) — 让模型知道这周有什么。
    focus_article: 用户正在读的那一篇,带正文摘录(前 ~4000 字符) —
                   没有它,模型只看得到标题,问正文内容会被误判"超范围"。
    返回:{ok, content, refused, model}

    DeepSeek 是思考型模型:答案写在 content,推理写在 reasoning_content,而思考
    本身也吃 max_tokens。问题短/开放时偶发"思考把配额耗光"或"答案全写进思考"
    → content 为空,前端就显示 "(no answer)"。兜底:content 空则从
    reasoning_content 提取结论,仍空则放宽 max_tokens 重问。
    """
    cfg = _cfg("ASK", "DEEPSEEK", "LLM")
    if not cfg["api_key"]:
        return {"ok": False, "refused": False,
                "content": "LLM is not configured. Set ASK_API_KEY / LLM_API_KEY in .env.",
                "model": DEFAULT_MODEL}

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if focus_article and focus_article.get("body"):
        messages.append({"role": "system", "content": "\n".join([
            "The user is currently reading this article (full text, read-only context):",
            f"Title: {focus_article.get('title', '')}",
            f"Source: {focus_article.get('source', '')}",
            f"URL: {focus_article.get('url', '')}",
            "--- article body ---",
            focus_article["body"],
        ])})
    if context_articles:
        ctx_lines = ["Other articles in this week's issue (titles only):"]
        for a in context_articles[:6]:
            ctx_lines.append(
                f"- [{a.get('source','?')}] {a.get('title','')} — "
                f"{a.get('published','')[:10]} · {a.get('url','')}"
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
        for attempt, budget in enumerate((max_tokens, 1200)):
            resp = client.chat.completions.create(
                model=model or cfg["model"],
                messages=messages,
                max_tokens=budget,
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
            log.warning("deepseek ask returned empty content (attempt %d), "
                        "retrying with max_tokens=%d", attempt + 1, budget)
        # 两次都空:给句能读的提示,别让前端落到 "(no answer)"
        last["content"] = "AI 没能生成回答，请换个问法再试一次。"
        return last
    except Exception as e:
        log.warning("deepseek ask failed: %s", e)
        return {"ok": False, "refused": False,
                "content": "AI service is temporarily unavailable.", "model": DEFAULT_MODEL}


_zh_summary_cache: dict[str, str] = {}

def summarize_zh(title: str, body: str, model: str | None = None) -> str:
    """生成一句话中文简介(卡片用)。失败返回空串,调用方回退到英文摘要。"""
    ck = title
    if ck in _zh_summary_cache:
        return _zh_summary_cache[ck]
    cfg = _cfg("LLM", "DEEPSEEK", "ASK")
    if not cfg["api_key"]:
        return ""
    body_snip = " ".join((body or "").split())[:600]
    prompt = ('文章标题: ' + title + '\n正文开头: ' + body_snip + '\n'
              '用一句不超过 40 字的简体中文概括这篇文章讲什么。只输出这句话本身。')
    try:
        extra = _thinking_extra(cfg)
        client = _client(cfg)
        out = ""
        for attempt in range(3):   # 失败/无中文输出时重试;最后一次仅用标题
            use_prompt = prompt if attempt < 2 else (
                '文章标题: ' + title + '\n用一句不超过 40 字的简体中文概括这篇文章讲什么。只输出这句话本身。')
            resp = client.chat.completions.create(
                model=model or cfg["model"],
                messages=[{"role": "user", "content": use_prompt}],
                max_tokens=1200, temperature=0.2, **extra)
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

