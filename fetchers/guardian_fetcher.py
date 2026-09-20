"""
The Guardian Open Platform — 官方全文 API(替代被墙的 theguardian.com RSS)
https://open-platform.theguardian.com/ — 免费开发 key,每日有限额,非商用足够。

国内网络实测:content.guardianapis.com 直连可达(与 theguardian.com 不同域)。
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "weekly-english/1.0"}
API_URL = "https://content.guardianapis.com/search"


def fetch_guardian(section: str = "world",
                   source_name: str = "The Guardian",
                   api_key: str | None = None,
                   days: int = 4,
                   limit: int = 3) -> list[dict]:
    """
    拉取某版块最近 days 天的文章,返回与 rss_fetcher 相同结构的记录。
    bodyText 为纯文本,段落以 \\n\\n 分隔,与 safe_paragraphs 兼容。
    """
    api_key = api_key or os.environ.get("GUARDIAN_API_KEY")
    if not api_key:
        log.warning("[guardian] no GUARDIAN_API_KEY, skip")
        return []

    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {
        "api-key": api_key,
        "section": section,
        "from-date": from_date,
        "page-size": limit,
        "order-by": "newest",
        "show-fields": "bodyText,thumbnail",
    }
    try:
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("[guardian] fetch failed (%s): %s", section, e)
        return []

    out: list[dict] = []
    for item in (data.get("response", {}).get("results") or []):
        body = (item.get("fields") or {}).get("bodyText") or ""
        url = item.get("webUrl", "")
        if len(body) < 400 or not url:
            continue
        out.append({
            "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
            "source": source_name,
            "url": url,
            "title": item.get("webTitle", ""),
            "title_zh": "",
            "published": (item.get("webPublicationDate") or "")[:10],
            "body": body,
            "summary": body[:400],
        })
    log.info("[guardian] %s: %d articles", section, len(out))
    return out
