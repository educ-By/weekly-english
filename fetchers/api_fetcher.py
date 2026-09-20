"""
公开新闻 API 抓取器 — NewsAPI / GNews 等
需要环境变量 NEWS_API_KEY(或显式传入)。
这些 API 返回的是标题+摘要+部分全文,严格保留原文措辞。

依赖:requests
"""
from __future__ import annotations
import os
import hashlib
import logging
from datetime import datetime, timedelta, timezone
import requests

log = logging.getLogger(__name__)


def _hash_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def fetch_newsapi(query: str,
                  source_name: str,
                  api_key: str | None = None,
                  days: int = 7,
                  language: str = "en",
                  limit: int = 6) -> list[dict]:
    """
    NewsAPI: https://newsapi.org/docs/endpoints/everything
    注意:免费 plan 仅返回前 ~50 篇文章,且 24h 之前的全文受限制。
    """
    api_key = api_key or os.environ.get("NEWS_API_KEY")
    if not api_key:
        log.warning("[newsapi] no API key, skip")
        return []

    from_ = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    params = {
        "q": query,
        "from": from_,
        "language": language,
        "pageSize": min(limit, 20),
        "sortBy": "publishedAt",
        "apiKey": api_key,
    }
    url = "https://newsapi.org/v2/everything"
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("[newsapi] request failed: %s", e)
        return []

    out = []
    for art in data.get("articles", [])[:limit]:
        link = art.get("url") or ""
        title = (art.get("title") or "").strip()
        if not link or not title or title == "[Removed]":
            continue
        body = (art.get("content") or art.get("description") or "").strip()
        # NewsAPI 的 content 字段常带 "... [+xxx chars]" — 保留原文
        out.append({
            "id": _hash_id(link),
            "source": source_name,
            "url": link,
            "title": title,
            "title_zh": "",
            "published": art.get("publishedAt", ""),
            "body": body or title,
            "summary": art.get("description", ""),
        })
    return out