"""
摘要与中文译名生成 — 完全可选、可拔插。
默认是离线启发式:
  - 摘要:取前 2 段 + 末 1 段,作为 safe 摘要(不动原文措辞)
  - 中文译名:留空(用户可手动填或调用外部 LLM)

如果用户配置了 NEWS_LLM_ENDPOINT,可以调用一个翻译/总结接口。
"""
from __future__ import annotations
import os
import logging
import requests

log = logging.getLogger(__name__)


def offline_summary(body: str, max_paragraphs: int = 3) -> str:
    """
    离线取首尾段落作为摘要 — 不改写原文,只是节选。
    """
    if not body:
        return ""
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    if len(paragraphs) <= max_paragraphs:
        return "\n\n".join(paragraphs)
    head = paragraphs[:2]
    tail = paragraphs[-1:]
    return "\n\n".join(head + ["…"] + tail)


def maybe_translate_title(title: str,
                          endpoint: str | None = None,
                          api_key: str | None = None) -> str:
    """
    占位 — 若用户配置了 LLM 端点,可以在这里调翻译。
    默认返回空串(由用户在周报页用浏览器内置 TTS 念英文标题即可)。
    """
    if not endpoint:
        return ""
    try:
        r = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key or os.environ.get('LLM_API_KEY', '')}"},
            json={"title": title, "target": "zh"},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("translation", "")
    except Exception as e:
        log.warning("translate failed: %s", e)
        return ""