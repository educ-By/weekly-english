"""
RSS 抓取器 — 适用于提供公开 RSS 的来源,例如:
- The Economist: https://www.economist.com/finance-and-economics/rss.xml
- Reader's Digest: https://www.rd.com/feed/
- China Daily: https://www.chinadaily.com.cn/rss/world.xml

依赖:feedparser, requests, beautifulsoup4
"""
from __future__ import annotations
import re
import hashlib
import logging
from datetime import datetime, timezone
from typing import Iterable

import feedparser
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


def _hash_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _fetch_full_text(url: str, timeout: int = 15) -> str | None:
    """
    抓取全文并清洗,严格保留原文措辞,只移除导航/广告/脚本/样式。
    返回清洗后的正文;失败返回 None。
    """
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        log.warning("fetch full text failed: %s (%s)", url, e)
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # 移除噪音
    for tag in soup(["script", "style", "noscript", "iframe",
                     "header", "footer", "nav", "aside", "form"]):
        tag.decompose()

    # 一些站点用 article 作为正文容器
    article = (
        soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find("main")
        or soup.body
    )
    if article is None:
        return None

    paragraphs = []
    for p in article.find_all(["p", "li"]):
        text = p.get_text(" ", strip=True)
        # 过滤明显的导航/版权/极短文本
        if len(text) < 30:
            continue
        if re.search(r"(subscribe|sign up|cookie|privacy policy)", text, re.I):
            continue
        paragraphs.append(text)

    return "\n\n".join(paragraphs).strip()


def fetch_rss(source_name: str,
              feed_url: str,
              limit: int = 6,
              require_full_text: bool = True) -> list[dict]:
    """
    抓取一个 RSS 源,返回统一结构的记录列表。
    require_full_text=True 时,会尝试抓原文网页拿到完整正文。
    """
    log.info("[rss] %s: %s", source_name, feed_url)
    parsed = feedparser.parse(feed_url)
    if parsed.bozo and not parsed.entries:
        log.warning("[rss] parse failed for %s: %s", source_name, parsed.bozo_exception)
        return []

    out: list[dict] = []
    for entry in parsed.entries[:limit]:
        url = getattr(entry, "link", None)
        title = (getattr(entry, "title", "") or "").strip()
        if not url or not title:
            continue

        # RSS 通常只给摘要
        summary = getattr(entry, "summary", "") or ""
        # 去 HTML 标签得到纯文本摘要(不修改原文,只剥离标签)
        if summary and "<" in summary:
            summary = BeautifulSoup(summary, "lxml").get_text(" ", strip=True)

        body = None
        if require_full_text:
            body = _fetch_full_text(url)
        if not body:
            # 退而求其次用 summary 当正文(仍不动原文)
            body = summary or title

        published = getattr(entry, "published", None) or getattr(entry, "updated", None)
        if published:
            try:
                # feedparser 会给出 *_parsed
                ts = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed")
                published = datetime(*ts[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                published = str(published)

        out.append({
            "id": _hash_id(url),
            "source": source_name,
            "url": url,
            "title": title,
            "title_zh": "",
            "published": published or "",
            "body": body,
            "summary": summary,
        })

    return out


def fetch_many(sources: Iterable[dict]) -> list[dict]:
    """
    sources: [{"name": "Economist", "feed": "...", "limit": 6}, ...]
    """
    all_records: list[dict] = []
    for s in sources:
        records = fetch_rss(
            source_name=s["name"],
            feed_url=s["feed"],
            limit=s.get("limit", 6),
            require_full_text=s.get("full_text", True),
        )
        all_records.extend(records)
    return all_records