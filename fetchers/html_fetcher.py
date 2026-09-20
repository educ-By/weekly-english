"""
通用 HTML 抓取器 — 用于没有 RSS/没有 API 的页面(例如 China Daily 子栏目)。

提供基于 CSS 选择器的抽取规则,严格保留原文措辞,只剥离导航/广告/版权。
"""
from __future__ import annotations
import hashlib
import logging
from datetime import datetime, timezone

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


def _clean(article_html: str) -> str:
    soup = BeautifulSoup(article_html, "lxml")
    for tag in soup(["script", "style", "noscript", "iframe", "figure",
                     "header", "footer", "nav", "aside", "form"]):
        tag.decompose()

    paragraphs = []
    for p in soup.find_all(["p", "li"]):
        text = p.get_text(" ", strip=True)
        if len(text) < 25:
            continue
        paragraphs.append(text)
    return "\n\n".join(paragraphs).strip()


def fetch_page(source_name: str,
               url: str,
               title_sel: str = "h1",
               body_sel: str = "article",
               published_sel: str | None = None) -> list[dict]:
    """单个 URL 的整页正文抽取。"""
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning("[html] fetch failed %s: %s", url, e)
        return []

    soup = BeautifulSoup(r.text, "lxml")
    title = soup.select_one(title_sel)
    title_text = title.get_text(strip=True) if title else ""

    body_el = soup.select_one(body_sel)
    if body_el is None:
        body_el = soup.find("article") or soup.find("main") or soup.body
    body = _clean(str(body_el)) if body_el else ""

    published = ""
    if published_sel:
        el = soup.select_one(published_sel)
        if el and el.get("datetime"):
            try:
                published = datetime.fromisoformat(
                    el["datetime"].replace("Z", "+00:00")
                ).isoformat()
            except Exception:
                published = el.get_text(strip=True)
        elif el:
            published = el.get_text(strip=True)

    if not title_text or not body:
        return []

    return [{
        "id": _hash_id(url),
        "source": source_name,
        "url": url,
        "title": title_text,
        "title_zh": "",
        "published": published or datetime.now(timezone.utc).isoformat(),
        "body": body,
        "summary": "",
    }]


def fetch_index_pages(source_name: str,
                      index_url: str,
                      link_sel: str,
                      base_url: str | None = None,
                      limit: int = 6,
                      **kwargs) -> list[dict]:
    """
    先抓索引页拿到文章链接列表,再逐篇抽取正文。
    link_sel: 选择每个文章链接的 CSS 选择器。
    base_url: 用于把相对链接补全(默认使用 index_url 的 host)。
    """
    try:
        r = requests.get(index_url, headers=DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning("[html] index failed %s: %s", index_url, e)
        return []

    soup = BeautifulSoup(r.text, "lxml")
    anchors = soup.select(link_sel)[:limit]
    seen: set[str] = set()
    records: list[dict] = []
    for a in anchors:
        href = a.get("href") or ""
        if not href:
            continue
        if base_url and href.startswith("/"):
            href = base_url.rstrip("/") + href
        if href in seen:
            continue
        seen.add(href)
        recs = fetch_page(source_name, href, **kwargs)
        records.extend(recs)
    return records