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


def is_configured() -> bool:
    return bool(_env("DEEPSEEK_API_KEY"))


def _client():
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError("openai package missing; pip install openai>=1.0") from e
    base_url = _env("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
    return OpenAI(api_key=_env("DEEPSEEK_API_KEY"), base_url=base_url)


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
    if not is_configured():
        return {"ok": False, "refused": False,
                "content": "DeepSeek is not configured. Set DEEPSEEK_API_KEY in .env.",
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
        client = _client()
        resp = client.chat.completions.create(
            model=model or _env("DEEPSEEK_MODEL") or DEFAULT_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = resp.choices[0].message.content or ""
        refused = "outside the scope" in content.lower()
        return {"ok": True, "refused": refused, "content": content.strip(),
                "model": model or DEFAULT_MODEL}
    except Exception as e:
        log.warning("deepseek ask failed: %s", e)
        return {"ok": False, "refused": False,
                "content": "AI service is temporarily unavailable.", "model": DEFAULT_MODEL}


def lookup_word(word: str,
                sentence_context: str | None = None,
                model: str | None = None) -> dict:
    """
    用 DeepSeek 释义单个英文词(可选带句中上下文)。
    用于 /api/dict?word=x — 替代或补充有道词典。
    """
    prompt = (
        f"Define the English word: {word}\n"
        + (f"In this sentence: \"{sentence_context}\"\n" if sentence_context else "")
        + "Reply in this exact JSON format:\n"
        + '{"phonetic": "...", "definition_en": "...", "definition_zh": "...", '
        + '"examples": ["...", "..."], "cefr_level": "A1|A2|B1|B2|C1|C2"}'
    )

    if not is_configured():
        return {"ok": False, "word": word,
                "translation": "Configure DEEPSEEK_API_KEY to enable the inline dictionary."}

    try:
        client = _client()
        resp = client.chat.completions.create(
            model=model or _env("DEEPSEEK_MODEL") or DEFAULT_MODEL,
            messages=[
                {"role": "system",
                 "content": "You are a precise English-Chinese dictionary. "
                            "Output JSON only, no markdown, no commentary."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400, temperature=0.0,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or "{}"
        import json as _json
        try:
            data = _json.loads(text)
        except Exception:
            data = {"definition_en": text, "definition_zh": ""}
        return {"ok": True, "word": word,
                "phonetic": data.get("phonetic", ""),
                "definition_en": data.get("definition_en", ""),
                "translation": data.get("definition_zh")
                               or data.get("definition_en", ""),
                "examples": data.get("examples", []),
                "cefr_level": data.get("cefr_level", "")}
    except Exception as e:
        log.warning("deepseek lookup_word failed: %s", e)
        return {"ok": False, "word": word,
                "translation": "AI service is temporarily unavailable."}