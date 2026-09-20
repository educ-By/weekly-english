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
import logging
from typing import Iterable, Optional

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
        lookup_word() 用 _cfg("LLM", "DEEPSEEK") — 单词优先 GLM 等便宜模型。"""
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


def is_configured() -> bool:
    return bool(_cfg("LLM", "ASK", "DEEPSEEK")["api_key"])


def _client(cfg: dict):
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError("openai package missing; pip install openai>=1.0") from e
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])


def ask(question: str,
        context_articles: Iterable[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 600,
        temperature: float = 0.3) -> dict:
    """
    调用 DeepSeek-V4.1-Flash 回答用户问题,自动带严格 system guard。
    context_articles: 用来限定范围 — 模型只允许用这些文章作为上下文。
    返回:{ok, content, refused, model}
    """
    cfg = _cfg("ASK", "DEEPSEEK", "LLM")
    if not cfg["api_key"]:
        return {"ok": False, "refused": False,
                "content": "LLM is not configured. Set ASK_API_KEY / LLM_API_KEY in .env.",
                "model": DEFAULT_MODEL}

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context_articles:
        ctx_lines = ["This week's articles (read-only context):"]
        for a in context_articles[:6]:
            ctx_lines.append(
                f"- [{a.get('source','?')}] {a.get('title','')} — "
                f"{a.get('published','')[:10]} · {a.get('url','')}"
            )
        messages.append({"role": "system", "content": "\n".join(ctx_lines)})

    messages.append({"role": "user", "content": question.strip()})

    try:
        client = _client(cfg)
        resp = client.chat.completions.create(
            model=model or cfg["model"],
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = resp.choices[0].message.content or ""
        refused = "outside the scope" in content.lower()
        return {"ok": True, "refused": refused, "content": content.strip(),
                "model": model or cfg["model"]}
    except Exception as e:
        log.warning("deepseek ask failed: %s", e)
        return {"ok": False, "refused": False,
                "content": "AI service is temporarily unavailable.", "model": DEFAULT_MODEL}


_lookup_cache: dict[str, dict] = {}   # (word|ctx) → 释义,进程内缓存省 token

def lookup_word(word: str,
                sentence_context: str | None = None,
                model: str | None = None) -> dict:
    """
    单词速查:GLM 等便宜模型,只回短中文释义(取本句意)。
    prompt/输出都极短,配合进程内缓存,token 消耗最小化。
    用于 /api/dict?word=x
    """
    ctx = (sentence_context or "").strip()[:160]   # 只保留本句,截断省 token
    ck = f"{word.lower()}|{ctx}"
    if ck in _lookup_cache:
        return _lookup_cache[ck]

    cfg = _cfg("LLM", "DEEPSEEK", "ASK")
    if not cfg["api_key"]:
        return {"ok": False, "word": word,
                "translation": "Configure LLM_API_KEY (or DEEPSEEK_API_KEY) to enable the inline dictionary."}

    prompt = (
        'Word: "' + word + '"\n'
        + ('Sentence: "' + ctx + '"\n' if ctx else "")
        + '给出该词在本句中的简洁中文释义。'
        '只输出一行 JSON:{"phonetic":"英式音标","zh":"本句义,不超过15字","pos":"词性"}'
    )
    try:
        client = _client(cfg)
        # 智谱 GLM 等思考型模型:可配 LLM_THINKING_LEVEL=low 控制思考档位省 token
        extra = {}
        lvl = _env("LLM_THINKING_LEVEL")
        if lvl:
            extra["extra_body"] = {"thinking": {"level": lvl}}
        resp = client.chat.completions.create(
            model=model or cfg["model"],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400, temperature=0.1,
            **extra,
        )
        text = (resp.choices[0].message.content or "").strip()
        # 健壮解析:剥掉 markdown 围栏,截取第一个 {...}
        import json as _json, re as _re
        m = _re.search(r"\{.*\}", text, _re.S)
        data = {}
        if m:
            try:
                data = _json.loads(m.group(0))
            except Exception:
                data = {}
        info = {"ok": True, "word": word,
                "phonetic": (data.get("phonetic") or "") if isinstance(data, dict) else "",
                "definition_en": "",
                "translation": (data.get("zh") or data.get("translation") or text[:60]),
                "examples": [],
                "cefr_level": "",
                "model": cfg["model"]}
        if len(_lookup_cache) > 800:
            _lookup_cache.clear()
        _lookup_cache[ck] = info
        return info
    except Exception as e:
        log.warning("lookup_word failed: %s", e)
        # 把真实原因带出去,便于在页面上直接定位(网络/变量/key 问题一眼可见)
        return {"ok": False, "word": word,
                "translation": "LLM error: " + str(e)[:120]}