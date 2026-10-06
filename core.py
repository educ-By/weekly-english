"""
core — 后端 / 脚本共用的业务逻辑入口

把 run_weekly.py 里的"采集 → 分级 → 渲染"抽离成纯函数,供:
  - run_weekly.py(本地手动渲染)
  - server/main.py(FastAPI 后端 + 定时调度)
共享同一份实现,避免重复代码。
"""
from __future__ import annotations
import datetime as dt
import json
import logging
import os
import re
import shutil
from collections import Counter
from html import unescape as unescape_html
from pathlib import Path

import jinja2

from fetchers.rss_fetcher import fetch_many
from fetchers.api_fetcher import fetch_newsapi
from fetchers.guardian_fetcher import fetch_guardian
from fetchers.html_fetcher import fetch_index_pages, fetch_page
from pipeline.difficulty import evaluate
from pipeline.highlight import (
    find_out_of_scope_words,
    is_truncated_contraction,
    text_tokens,
)
from pipeline.summary import offline_summary

log = logging.getLogger(__name__)

# 渲染产物备份:就地改写老期页面之前先存一份,保留最近 N 份
BACKUP_DIR_NAME = "backup"
BACKUP_KEEP = 3

# 渲染产物的就地迁移版本 —— 改一次迁移逻辑就 +1,老卷上会重新跑一遍
RENDER_MIGRATE_VERSION = 1

# 静态资源版本号 —— static/ 下哪个文件改了就把对应数字 +1。
# 页面是预渲染到磁盘的死 HTML,模板改了不会自动影响已经落盘的成品,
# 所以启动时会用 refresh_asset_versions() 就地重写这些引用;否则老用户
# 会一直命中浏览器缓存里的旧 JS/CSS,改动根本到不了他们那里。
ASSET_VERSIONS: dict[str, int] = {
    "app.js": 50,
    "style.css": 41,
    "me.js": 5,
    "auth.js": 1,
    "ielts.js": 5,
    "ielts.css": 3,
}

# /static/app.js?v=41 这种形式;模板里统一用 asset('app.js') 生成。
# 前面允许跟引号(或没有),是为了同时认下面两种写法:
#   href="/static/style.css?v=33"   —— 改造后的绝对路径
#   href="static/style.css?v=25"    —— 改造前遗留页面的相对路径
# 用 lookbehind 卡住"引号紧跟其后",免得把 /issue/static/... 的 `/issue` 前缀吃掉。
_ASSET_RE = re.compile(
    r"(?<=[\"'])(/?static/)(app\.js|style\.css|me\.js|auth\.js|ielts\.js|ielts\.css)(?:\?v=[0-9]+)?"
)


def asset(name: str) -> str:
    """模板里生成带版本号的静态资源 URL —— 版本号只有 ASSET_VERSIONS 一处定义。"""
    ver = ASSET_VERSIONS.get(name)
    return f"/static/{name}" + (f"?v={ver}" if ver is not None else "")


def _sync_static_copies(out_dir: Path) -> int:
    """把每期的 static/ 副本刷成当前 static/ 的内容。

    副本是渲染那一刻拷的(render_issue_to_dir),老期不会重渲染,副本只会越来越旧;
    相对路径引用资源的遗留页面、file:// 本地预览和导 PDF 都吃这份副本。
    """
    if not STATIC_DIR.is_dir():
        return 0
    synced = 0
    for issue_dir in out_dir.iterdir():
        if not (issue_dir.is_dir() and re.match(r"\d{4}-W\d{2}$", issue_dir.name)):
            continue
        dest = issue_dir / "static"
        if not dest.is_dir():
            continue
        for src in sorted(STATIC_DIR.iterdir()):
            if not src.is_file():
                continue
            try:
                tgt = dest / src.name
                if tgt.exists():
                    a, b = src.stat(), tgt.stat()
                    if a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime):
                        continue
                shutil.copy2(src, tgt)
                synced += 1
            except Exception as e:
                log.warning("Static copy sync failed for %s: %s", src, e)
    return synced


def refresh_asset_versions(out_dir: Path) -> int:
    """就地重写已渲染 HTML 里的静态资源版本号(幂等,不重渲染、不联网)。

    模板只影响"之后渲染出来的"期;磁盘上现成的成品页引用的是渲染那一刻的
    版本号,改 static/ 之后必须在这里补一刀。
    """
    if not out_dir.exists():
        return 0
    changed = 0
    for page in out_dir.rglob("*.html"):
        try:
            txt = page.read_text(encoding="utf-8")
        except Exception:
            continue
        new = _ASSET_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}" + (
                f"?v={ASSET_VERSIONS[m.group(2)]}"
                if m.group(2) in ASSET_VERSIONS else ""),
            txt,
        )
        if new == txt:
            continue
        try:
            page.write_text(new, encoding="utf-8")
            changed += 1
        except Exception as e:
            log.warning("Asset version rewrite failed for %s: %s", page, e)
    synced = _sync_static_copies(out_dir)
    if changed or synced:
        log.info("Refreshed asset versions: %d page(s), %d static copy file(s)",
                 changed, synced)
    return changed


# 导航新增栏目时,已落盘的成品页是渲染那一刻的旧导航(没有这个链接),而 landing
# 只在 catalog 缺失/历史期迁移时才重建 —— 线上卷上这两条都不成立,首页就永远
# 看不到新入口。这里按 refresh_asset_versions 的同一套路就地补一刀:不重渲染、
# 不联网,只往缺链接的页里插一段与模板完全一致的锚点。
_NAV_LEVELS_MARK = '<span class="nav-levels"'
_IELTS_NAV_ANCHOR = ('<a class="nav-item" href="/ielts"\n'
                     '>IELTS</a>\n\n      ')
_IELTS_NAV_HREF = 'href="/ielts"'


def refresh_nav_links(out_dir: Path) -> int:
    """给已渲染的页面补上导航里的雅思入口(幂等;已有链接的页面跳过)。"""
    if not out_dir.exists():
        return 0
    changed = 0
    for page in out_dir.rglob("*.html"):
        try:
            txt = page.read_text(encoding="utf-8")
        except Exception:
            continue
        if _IELTS_NAV_HREF in txt or _NAV_LEVELS_MARK not in txt:
            continue
        new = txt.replace(_NAV_LEVELS_MARK,
                          _IELTS_NAV_ANCHOR + _NAV_LEVELS_MARK, 1)
        if new == txt:
            continue
        try:
            page.write_text(new, encoding="utf-8")
            changed += 1
        except Exception as e:
            log.warning("Nav link rewrite failed for %s: %s", page, e)
    if changed:
        log.info("Refreshed nav links: %d page(s)", changed)
    return changed


# 生词块("Out-of-scope vocabulary")—— 模板 article.html.j2 生成,就地重算时整块重写
_RARE_BLOCK_RE = re.compile(r'<details class="rare-words">.*?</details>', re.S)
_RARE_WORD_RE = re.compile(r'data-word="([^"]*)"')

_RARE_BLOCK_TPL = """<details class="rare-words">
      <summary>Out-of-scope vocabulary · {n} words</summary>
      <p class="rare-help">
        Words below are not in the 3,500-word gaokao list (or its common inflections).
        Hover a highlighted word for a quick gloss — or <strong>select any word or phrase</strong> in the text to translate it and save it to your words.
      </p>
      <div class="vocab-list">
{buttons}      </div>
    </details>"""


def _render_rare_block(words: list[str]) -> str:
    buttons = "".join(
        f'        <button class="vocab-word vocab-rare" data-word="{w}">{w}</button>\n'
        for w in words
    )
    return _RARE_BLOCK_TPL.format(n=len(words), buttons=buttons)


_PAGE_BODY_RE = re.compile(r'<div class="entry-body"[^>]*>(.*?)</div>', re.S)
_PAGE_P_RE = re.compile(r"<p>(.*?)</p>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def page_body_text(html: str) -> str:
    """抠出页面正文(保持原大小写)。页面只印前 12 段,这是"可见片段"。"""
    m = _PAGE_BODY_RE.search(html)
    if not m:
        return ""
    paras = [unescape_html(_TAG_RE.sub("", p)).strip()
             for p in _PAGE_P_RE.findall(m.group(1))]
    return "\n".join(p for p in paras if p)


def _issue_search_json(issue_dir: Path) -> dict:
    """读一期的全文语料 search.json(小写正文);缺失或损坏返回空 dict。"""
    path = issue_dir / "search.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("Bad search.json for %s: %s", issue_dir, e)
        return {}


def page_corpus_text(page: Path, cache: dict | None = None) -> str:
    """该篇的全文语料(同期 search.json,小写)。

    页面正文只印前 12 段,而"坏出现"(URL 里的 chinadaily、被截断的 doesn’t)常常
    落在截断之外 —— 只拿可见片段判会漏掉。传同一个 cache dict 可避免每页重读。
    """
    m = re.search(r"article-([0-9A-Za-z]+)\.html$", page.name)
    if not m:
        return ""
    if cache is None:
        cache = {}
    issue_dir = page.parent
    if issue_dir not in cache:
        cache[issue_dir] = _issue_search_json(issue_dir)
    return cache[issue_dir].get(m.group(1), "")


def classify_page_rare_words(words: list[str], body_text: str,
                             corpus_text: str = "") -> tuple[list[str], list[str], list[str]]:
    """把一页已烤入的生词列表按当前白名单 + 当前分词重筛。

    返回 (仍该保留, 已加白, 非词残留):
      - 已加白:当前白名单已覆盖,出局;
      - 非词残留:正文里只以"非词"形态出现 —— URL 片段(https/chinadaily)或被
        截断的撇号(doesn’t 在旧代码下切成 doesn)。现在的分词根本不会切出它,出局;
      - 正文里压根没出现的词:保留(判据来自可见正文 + search.json 全文,
        两处都没有才说明只是没进这次渲染,不能当残留删掉)。
    """
    tokens = text_tokens(body_text) | text_tokens(corpus_text)
    raw = (body_text + "\n" + corpus_text).lower()
    keep: list[str] = []
    whitelisted: list[str] = []
    nontoken: list[str] = []
    for w in words:
        if not find_out_of_scope_words(w, limit=1):
            whitelisted.append(w)
            continue
        low = w.lower()
        if is_truncated_contraction(low):
            nontoken.append(w)
            continue
        if low not in tokens and re.search(rf"(?<![a-z]){re.escape(low)}(?![a-z])", raw):
            nontoken.append(w)
            continue
        keep.append(w)
    return keep, whitelisted, nontoken


def refresh_rare_words(out_dir: Path, dry_run: bool = False) -> int:
    """就地按当前白名单重筛已渲染页面的生词块(不重渲染正文、不联网、幂等)。

    页面上的生词表是"渲染那一刻"的白名单算出来的。改了 data/cefr_vocab/* 之后,
    整站重抓只会覆盖当期(core.full_refresh 只渲染 issue_key()),启动时的
    refresh_asset_versions 又只改资源版本号 —— 老期页面里那些早已加白的词
    (don't/children/fully…) 就永远留在页面上。

    这里读页面里既有的 data-word 列表,连同这一页自己的正文一起重筛(见
    classify_page_rare_words),把不该留的摘掉,计数与按钮一起重写。前端
    highlightRare() 照着按钮画线,下划线自然跟着收敛。

    只在确有页面需要改动时先备份再落盘;没得改就直接返回,不碰磁盘。
    """
    if not out_dir.exists():
        return 0
    pending: list[tuple[Path, str]] = []
    corpus_cache: dict = {}
    for page in out_dir.rglob("article-*.html"):
        try:
            txt = page.read_text(encoding="utf-8")
        except Exception:
            continue
        block = _RARE_BLOCK_RE.search(txt)
        if not block:
            continue
        words = [unescape_html(w) for w in _RARE_WORD_RE.findall(block.group(0))]
        kept, _, _ = classify_page_rare_words(
            words, page_body_text(txt), page_corpus_text(page, corpus_cache))
        if len(kept) == len(words):
            continue                       # 没有词该退出,这一页不动
        new = txt[:block.start()] + _render_rare_block(kept) + txt[block.end():]
        if "</html>" not in new:
            log.warning("Skip rare refresh (no </html>): %s", page)
            continue
        pending.append((page, new))

    if not pending:
        return 0
    if dry_run:
        log.info("Rare-word refresh (dry run): %d page(s) would change", len(pending))
        return len(pending)
    if backup_rendered(out_dir, "pre-rare-refresh") is None:
        log.warning("Rare-word refresh skipped: backup could not be created")
        return 0

    changed = 0
    for page, new in pending:
        try:
            page.write_text(new, encoding="utf-8")
            changed += 1
        except Exception as e:
            log.warning("Rare refresh write failed for %s: %s", page, e)
    log.info("Rare-word refresh: %d/%d page(s) rewritten", changed, len(pending))
    return changed


LEVEL_LABEL = {"B1": "入门", "B2": "进阶", "C1": "高阶"}

DEFAULT_RSS_SOURCES = [
    # ---- 国际主流刊物(部分在国内网络需代理,超时会自动跳过) ----
    {"name": "The Economist",
     "feed": "https://www.economist.com/finance-and-economics/rss.xml",
     "limit": 3, "full_text": True},
    {"name": "The Guardian",
     "feed": "https://www.theguardian.com/international/rss",
     "limit": 3, "full_text": True},
    {"name": "BBC Learning English",
     "feed": "https://www.bbc.co.uk/learningenglish/english/features/news-report/rss.xml",
     "limit": 3, "full_text": True},
    {"name": "Scientific American",
     "feed": "https://rss.sciam.com/ScientificAmerican-Global",
     "limit": 3, "full_text": True},

    # ---- 新闻(国内直连可达) ----
    {"name": "NPR News",
     "feed": "https://feeds.npr.org/1001/rss.xml",
     "limit": 2, "full_text": True},
    {"name": "NPR Topics: Education",
     "feed": "https://feeds.npr.org/1032/rss.xml",
     "limit": 2, "full_text": True},
    {"name": "NPR Science",
     "feed": "https://feeds.npr.org/1007/rss.xml",
     "limit": 2, "full_text": True},
    {"name": "NPR Health",
     "feed": "https://feeds.npr.org/1008/rss.xml",
     "limit": 2, "full_text": True},
    {"name": "NPR Politics",
     "feed": "https://feeds.npr.org/1015/rss.xml",
     "limit": 2, "full_text": True},
    {"name": "CBS News",
     "feed": "https://www.cbsnews.com/latest/rss/main",
     "limit": 2, "full_text": True},
    {"name": "CBS Tech",
     "feed": "https://www.cbsnews.com/latest/rss/tech",
     "limit": 2, "full_text": True},
    {"name": "CBS Politics",
     "feed": "https://www.cbsnews.com/latest/rss/politics",
     "limit": 2, "full_text": True},
    {"name": "CNBC Top News",
     "feed": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
     "limit": 2, "full_text": True},
    {"name": "Sky News",
     "feed": "https://feeds.skynews.com/feeds/rss/world.xml",
     "limit": 2, "full_text": True},
    {"name": "Sky News UK",
     "feed": "https://feeds.skynews.com/feeds/rss/uk.xml",
     "limit": 2, "full_text": True},
    {"name": "Sky Business",
     "feed": "https://feeds.skynews.com/feeds/rss/business.xml",
     "limit": 2, "full_text": True},
    {"name": "Sky Technology",
     "feed": "https://feeds.skynews.com/feeds/rss/technology.xml",
     "limit": 2, "full_text": True},
    {"name": "France 24",
     "feed": "https://www.france24.com/en/rss",
     "limit": 2, "full_text": True},
    {"name": "France 24 Environment",
     "feed": "https://www.france24.com/en/environment/rss",
     "limit": 2, "full_text": True},
    {"name": "Global Times",
     "feed": "https://www.globaltimes.cn/rss/outbrain.xml",
     "limit": 2, "full_text": True},

    # ---- 科学 / 科技 ----
    {"name": "MIT Technology Review",
     "feed": "https://www.technologyreview.com/feed/",
     "limit": 3, "full_text": True},
    {"name": "Nature News",
     "feed": "https://www.nature.com/nature.rss",
     "limit": 3, "full_text": True},
    {"name": "ScienceDaily",
     "feed": "https://www.sciencedaily.com/rss/all.xml",
     "limit": 2, "full_text": True},
    {"name": "Phys.org",
     "feed": "https://phys.org/rss-feed/",
     "limit": 2, "full_text": True},
    {"name": "Ars Technica",
     "feed": "https://feeds.arstechnica.com/arstechnica/index",
     "limit": 2, "full_text": True},
    {"name": "Ars Technica Science",
     "feed": "https://feeds.arstechnica.com/arstechnica/science",
     "limit": 2, "full_text": True},
    {"name": "TechCrunch",
     "feed": "https://techcrunch.com/feed/",
     "limit": 2, "full_text": True},
    {"name": "Nautilus",
     "feed": "https://nautil.us/feed/",
     "limit": 2, "full_text": True},

    # ---- 文化 / 长文 ----
    {"name": "Smithsonian Magazine",
     "feed": "https://www.smithsonianmag.com/rss/articles/",
     "limit": 2, "full_text": True},
    {"name": "Smithsonian History",
     "feed": "https://www.smithsonianmag.com/rss/history/",
     "limit": 2, "full_text": True},
    {"name": "Aeon",
     "feed": "https://aeon.co/feed.rss",
     "limit": 2, "full_text": True},

    # ---- B1 入门级(专为英语学习者写的分级新闻) ----
    {"name": "Breaking News English",
     "feed": "https://breakingnewsenglish.com/rss.xml",
     "limit": 4, "full_text": True},
    {"name": "Simple English News",
     "feed": "https://www.simpleenglishnews.com/feed",
     "limit": 2, "full_text": True},
]

# 无 RSS 的免费源 — 走 HTML 爬取(RSS 已下线的 China Daily 等)
# limit 是索引链接个数(首页链接有重复),不是篇数;真正入库还要过正文长度/同题去重
DEFAULT_HTML_SOURCES = [
    {"name": "China Daily",
     "index_url": "https://www.chinadaily.com.cn/world",
     "link_sel": "a[href*='/a/2']",
     "base_url": "https://www.chinadaily.com.cn",
     "limit": 16,
     "title_sel": "h1",
     "body_sel": "#Content"},
    {"name": "China Daily (China)",
     "index_url": "https://www.chinadaily.com.cn/china",
     "link_sel": "a[href*='/a/2']",
     "base_url": "https://www.chinadaily.com.cn",
     "limit": 16,
     "title_sel": "h1",
     "body_sel": "#Content"},
    {"name": "China Daily (Business)",
     "index_url": "https://www.chinadaily.com.cn/business",
     "link_sel": "a[href*='/a/2']",
     "base_url": "https://www.chinadaily.com.cn",
     "limit": 16,
     "title_sel": "h1",
     "body_sel": "#Content"},
]

# The Guardian 官方 API — 设置 GUARDIAN_API_KEY 即启用(免费开发 key,
# open-platform.theguardian.com 注册;API 域名国内直连可达)
DEFAULT_GUARDIAN_SECTIONS = ["world", "technology"]


def week_label(today: dt.date | None = None) -> str:
    today = today or _today()
    iso = today.isocalendar()
    return f"{iso.year} · W{iso.week:02d}"


def issue_key(today: dt.date | None = None) -> str:
    today = today or _today()
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# 期号锚定时区 — 固定 Asia/Shanghai,与定时调度(scheduler 的 timezone)一致。
# 之前用服务器本地日期,容器 TZ=UTC 时周一 0~7 点渲染的期号会归到上一周。
try:
    from zoneinfo import ZoneInfo
    _SCHED_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    _SCHED_TZ = None


def _today() -> dt.date:
    if _SCHED_TZ is not None:
        return dt.datetime.now(_SCHED_TZ).date()
    return dt.date.today()


def safe_paragraphs(text: str, max_paragraphs: int = 12) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()][:max_paragraphs]


def make_deck(body: str, max_len: int = 220) -> str:
    first = safe_paragraphs(body, 1)
    if not first:
        return ""
    text = first[0]
    if len(text) <= max_len:
        return text
    cut = text.rfind(" ", 0, max_len)
    if cut <= 0:
        cut = max_len
    return text[:cut].rstrip(",.;: ") + "…"


def issue_title(articles: list[dict]) -> str:
    if not articles:
        return "A quieter week of reading."
    sources = sorted({a["source"] for a in articles})
    if {"The Economist", "China Daily"}.issubset(set(sources)):
        return "From Beijing's green corridors to the quiet of central banks."
    if "The Economist" in sources:
        return "A measured week, with markets and policy in mind."
    if "Reader's Digest" in sources:
        return "Small habits, longer essays."
    return "A week of reading, hand-picked."


def build_article(rec: dict) -> dict:
    body = rec.get("body") or ""
    title = rec.get("title") or ""
    eval_result = evaluate(body)
    level = eval_result["level"]
    stats = eval_result["stats"]
    rare = find_out_of_scope_words(body, limit=30)
    summary = offline_summary(body)

    published = rec.get("published") or ""
    try:
        pub_dt = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
        published_display = pub_dt.strftime("%b %d, %Y")
    except Exception:
        published_display = published[:10] if published else "—"

    return {
        "id": rec["id"],
        "source": rec["source"],
        "url": rec["url"],
        "title": title,
        "published": published,
        "published_display": published_display,
        "level": level,
        "level_label": LEVEL_LABEL[level],
        "stats": stats,
        "rare_words": rare,
        "deck": make_deck(body),
        "summary_paragraphs": safe_paragraphs(summary, max_paragraphs=3),
        "body_paragraphs": safe_paragraphs(body, max_paragraphs=12),
        "body_search": re.sub(r"<[^>]+>", " ", body).lower(),
    }


def collect(config: dict) -> list[dict]:
    records: list[dict] = []
    rss = config.get("rss") or DEFAULT_RSS_SOURCES
    log.info("RSS sources: %d", len(rss))
    records.extend(fetch_many(rss))

    guardian_cfg = config.get("guardian")
    if guardian_cfg is None and os.environ.get("GUARDIAN_API_KEY"):
        guardian_cfg = {"sections": DEFAULT_GUARDIAN_SECTIONS, "limit": 2}
    if guardian_cfg and os.environ.get("GUARDIAN_API_KEY"):
        for sec in guardian_cfg.get("sections", ["world"]):
            records.extend(fetch_guardian(
                section=sec, limit=guardian_cfg.get("limit", 2),
                api_key=guardian_cfg.get("api_key"),
            ))

    api_cfg = config.get("newsapi") or {}
    if api_cfg.get("enabled"):
        for q in api_cfg.get("queries", []):
            records.extend(fetch_newsapi(
                query=q["query"], source_name=q.get("name", "NewsAPI"),
                api_key=api_cfg.get("api_key"), limit=q.get("limit", 4),
            ))

    html_cfg = config.get("html")
    if html_cfg is None:
        html_cfg = DEFAULT_HTML_SOURCES
    for item in html_cfg:
        if "index_url" in item:
            records.extend(fetch_index_pages(
                source_name=item["name"], index_url=item["index_url"],
                link_sel=item["link_sel"], base_url=item.get("base_url"),
                limit=item.get("limit", 4),
                title_sel=item.get("title_sel", "h1"),
                body_sel=item.get("body_sel", "article"),
            ))
        elif "url" in item:
            records.extend(fetch_page(
                source_name=item["name"], url=item["url"],
                title_sel=item.get("title_sel", "h1"),
                body_sel=item.get("body_sel", "article"),
            ))

    seen, uniq = set(), []
    for r in records:
        if not r.get("body") or len(r["body"]) < 400:
            continue
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        uniq.append(r)
    return uniq


# ---------- 模板渲染 ----------
ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

# autoescape 显式开:模板后缀 .html.j2 不被 select_autoescape(["html"]) 认出,
# 之前等于没转义(标题/简介里的引号会截断属性值)。模板变量全是标量。
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
    trim_blocks=True, lstrip_blocks=True,
)
_env.globals["asset"] = asset


def rendered_dir(out_dir: Path) -> Path:
    """渲染目录的真实路径。

    本地 `data/output` 是指向 `data/preview` 的符号链接,遍历和备份都要取解析后的
    目录,否则会把链接本身当目录树。
    """
    try:
        return out_dir.resolve()
    except OSError:
        return out_dir


def _prune_backups(dest_root: Path, keep: int = BACKUP_KEEP) -> None:
    try:
        snaps = sorted((p for p in dest_root.iterdir() if p.is_dir()), reverse=True)
    except OSError:
        return
    for old in snaps[keep:]:
        try:
            shutil.rmtree(old)
            log.info("Pruned old backup %s", old)
        except Exception as e:
            log.warning("Prune failed for %s: %s", old, e)


def backup_rendered(out_dir: Path, reason: str) -> Optional[Path]:
    """把渲染产物整体备份一份,返回备份目录(失败返回 None)。

    就地改写老期 HTML 之前必须先备份 —— 那些期没有源数据,改坏就捞不回来。
    回滚:cp -r <备份目录>/. <out_dir>/
    """
    src = rendered_dir(out_dir)
    if not src.is_dir():
        return None
    dest_root = src.parent / BACKUP_DIR_NAME
    dest = dest_root / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{reason}"
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, symlinks=True,
                        ignore=shutil.ignore_patterns(BACKUP_DIR_NAME))
    except Exception as e:
        log.warning("Backup failed (%s): %s", dest, e)
        return None
    _prune_backups(dest_root)
    log.info("Backed up rendered data to %s (rollback: cp -r %s/. %s/)",
             dest, dest, out_dir)
    return dest


def extract_search_corpus(out_dir: Path) -> int:
    """把每期的全文搜索语料从 meta.json 搬到同目录的 search.json。

    语料(一期约 150KB)以前同时躺在三处:目录页 HTML 的 data-body、meta.json、
    catalog.json。抽成一份 sidecar 之后,目录页从 220KB+ 降到几十 KB,
    meta.json / catalog.json 也跟着瘦下来。幂等:已有 search.json 的期跳过。
    """
    if not out_dir.exists():
        return 0
    moved = 0
    for issue_dir in sorted(p for p in out_dir.iterdir() if p.is_dir()):
        meta_path = issue_dir / "meta.json"
        corpus_path = issue_dir / "search.json"
        if corpus_path.exists() or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Skipping corpus extraction for %s: %s", issue_dir.name, e)
            continue
        articles = meta.get("articles") or []
        corpus = {a["id"]: a.get("search", "") for a in articles if a.get("id")}
        if not any(corpus.values()):
            continue
        try:
            corpus_path.write_text(json.dumps(corpus, ensure_ascii=False),
                                   encoding="utf-8")
            for a in articles:
                a.pop("search", None)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False),
                                 encoding="utf-8")
            moved += 1
            log.info("Extracted search corpus for %s (%d articles, %.0f KB)",
                     issue_dir.name, len(corpus), len(json.dumps(corpus)) / 1024)
        except Exception as e:
            log.warning("Corpus extraction failed for %s: %s", issue_dir.name, e)
    return moved


# 就地改写老期页面时的文案修正(模板早已改好,卷上那些是更早的渲染产物)
_COPY_FIXES = (
    ("Words below sit outside the standard high-school / CET-4+6 lists.",
     "Words below sit outside the 3,500-word gaokao list."),
)

_SECTION_RE = {
    "toc": re.compile(r'\n?\s*<section class="toc">.*?</section>\s*', re.S),
    "vocab": re.compile(r'\n?\s*<section class="vocab-summary">.*?</section>\s*', re.S),
}
_DATA_BODY_RE = re.compile(r'\s*data-body="[^"]*"')
_CARD_COUNT_RE = re.compile(r'<a class="card ')


def migrate_rendered_pages(out_dir: Path) -> int:
    """就地更新已渲染的页面,让老期跟上当前模板:去掉内嵌全文、删掉编号目录与
    词汇汇总块、修正旧文案。

    写前逐文件校验(仍是完整 HTML、卡片数不变、没有残留 data-body),任一项不过就
    跳过该文件并告警 —— 宁可不改,也不要把老期改坏。幂等,可反复跑。
    """
    if not out_dir.exists():
        return 0
    changed = skipped = 0
    for page in out_dir.rglob("*.html"):
        try:
            txt = page.read_text(encoding="utf-8")
        except Exception:
            continue
        if "data-body=" not in txt and '<section class="toc">' not in txt \
                and '<section class="vocab-summary">' not in txt \
                and "standard high-school" not in txt:
            continue                       # 已经是新模样,不动

        new = _DATA_BODY_RE.sub("", txt)
        for rx in _SECTION_RE.values():
            new = rx.sub("\n", new)
        for old, repl in _COPY_FIXES:
            new = new.replace(old, repl)

        # 校验:结构没被改坏才落盘
        if "</html>" not in new:
            log.warning("Skip migrate (no </html>): %s", page); skipped += 1; continue
        if len(_CARD_COUNT_RE.findall(new)) != len(_CARD_COUNT_RE.findall(txt)):
            log.warning("Skip migrate (card count changed): %s", page); skipped += 1; continue
        if "data-body=" in new:
            log.warning("Skip migrate (data-body survived): %s", page); skipped += 1; continue

        if new == txt:
            continue
        try:
            page.write_text(new, encoding="utf-8")
            changed += 1
        except Exception as e:
            log.warning("Migrate write failed for %s: %s", page, e); skipped += 1
    if changed or skipped:
        log.info("Rendered-page migration: %d rewritten, %d skipped", changed, skipped)
    return changed


def migrate_rendered_data(out_dir: Path) -> dict:
    """启动时的一次性整理:先备份,再抽语料 + 就地更新老页面,最后重建 catalog。

    用标记文件保证只跑一次(幂等,重跑也不会重复备份)。
    """
    out = {"backed_up": None, "corpus": 0, "pages": 0}
    real = rendered_dir(out_dir)
    marker = real.parent / f".render-migrate-{RENDER_MIGRATE_VERSION}"
    if marker.exists():
        return out
    if not real.is_dir():
        return out

    snap = backup_rendered(out_dir, "pre-migrate")
    if snap is None:
        log.warning("Migration skipped: backup could not be created")
        return out
    out["backed_up"] = str(snap)

    out["corpus"] = extract_search_corpus(out_dir)
    out["pages"] = migrate_rendered_pages(out_dir)
    if out["corpus"]:
        try:
            build_catalog(out_dir)
        except Exception as e:
            log.warning("Catalog rebuild after corpus extraction failed: %s", e)

    try:
        marker.write_text(dt.datetime.now().isoformat(timespec="seconds"),
                          encoding="utf-8")
    except Exception as e:
        log.warning("Could not write migration marker: %s", e)
    log.info("Rendered-data migration done: %s", out)
    return out


def article_meta(a: dict) -> dict:
    """catalog / meta.json 里的单篇摘要 —— 只保留跨期页面需要的字段。

    注意这里**不含正文**(`body_search`)—— 全文语料单独放同期的 `search.json`,
    否则 meta.json 一期就 160KB+,catalog.json 直接 400KB+。
    """
    return {
        "id": a["id"],
        "title": a["title"],
        "level": a["level"],
        "level_label": a.get("level_label", ""),
        "words": a.get("stats", {}).get("total_words", 0),
        "deck": a.get("deck", ""),
        "url": a.get("url", ""),
        "source": a.get("source", ""),
    }


def vocab_summary(articles: list[dict], limit: int = 28) -> list[dict]:
    """本期超纲词汇总:按"出现在几篇里"降序,再按总出现次数降序。"""
    docs: Counter = Counter()
    total: Counter = Counter()
    for a in articles:
        words = set(a.get("rare_words") or [])
        body = (a.get("body_search") or "").lower()
        for w in words:
            docs[w] += 1
            total[w] += body.count(w)
    ranked = sorted(total, key=lambda w: (-docs[w], -total[w], w))
    return [{"word": w, "count": docs[w]} for w in ranked[:limit]]


def related_for(article: dict, articles: list[dict], limit: int = 4) -> list[dict]:
    """同期内挑相关阅读:同难度优先,其次按共享超纲词数量。"""
    mine = set(article.get("rare_words") or [])
    scored = []
    for other in articles:
        if other["id"] == article["id"]:
            continue
        shared = len(mine & set(other.get("rare_words") or []))
        other_level = 0 if other["level"] == article["level"] else 1
        scored.append((other_level, -shared, other["id"], other))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [
        {"id": o["id"], "title": o["title"], "level": o["level"], "shared": -neg}
        for _, neg, _, o in scored[:limit]
    ]


def render_issue_to_dir(articles: list[dict],
                        out_dir: Path,
                        week: str,
                        title: str,
                        number: int,
                        key: str | None = None) -> Path:
    """渲染一期到指定目录:index.html + article-<id>.html + meta.json + static/"""
    out_dir.mkdir(parents=True, exist_ok=True)
    key = key or out_dir.name

    static_dest = out_dir / "static"
    if static_dest.exists():
        shutil.rmtree(static_dest)
    shutil.copytree(STATIC_DIR, static_dest)

    sources = sorted({a["source"] for a in articles})
    counts = {"B1": 0, "B2": 0, "C1": 0}
    for a in articles:
        counts[a["level"]] = counts.get(a["level"], 0) + 1
    issue_ctx = {
        "issue": {"key": key, "week_label": week, "title": title, "number": number},
        "articles": articles,
        "sources": sources,
        "counts": counts,
        "nav": "issue",
    }

    issue_html = _env.get_template("issue.html.j2").render(page="index", **issue_ctx)
    (out_dir / "index.html").write_text(issue_html, encoding="utf-8")

    art_tpl = _env.get_template("article.html.j2")
    for idx, a in enumerate(articles):
        art_html = art_tpl.render(
            page="article",
            article=a,
            prev_article=articles[idx - 1] if idx > 0 else None,
            next_article=articles[idx + 1] if idx + 1 < len(articles) else None,
            related=related_for(a, articles),
            **issue_ctx,
        )
        (out_dir / f"article-{a['id']}.html").write_text(art_html, encoding="utf-8")

    # 机器可读索引 —— 供 /level、/search 与首页复用,不必再抓 HTML
    meta = {
        "key": key,
        "week_label": week,
        "title": title,
        "number": number,
        "count": len(articles),
        "articles": [article_meta(a) for a in articles],
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    # 全文搜索语料单独一份:目录页、meta.json、catalog.json 都不该扛着它。
    # 一期正文约 150KB,内嵌会让目录页从几十 KB 涨到 220KB+。
    corpus = {a["id"]: a.get("body_search", "") for a in articles}
    (out_dir / "search.json").write_text(
        json.dumps(corpus, ensure_ascii=False), encoding="utf-8")

    log.info("Rendered issue: %s (%d articles)", out_dir, len(articles))
    return out_dir


def _issue_title(issue_dir: Path) -> str:
    """取一期的标题。优先读 meta.json —— index.html 一期就 180~220KB,
    只为抠一个 <h1> 就整篇读进来跑正则,是每次列期数(首页/健康检查/提问/
    "最新一期"跳转)都要付一遍的代价。没有 meta.json 的历史期才回退解析 HTML。
    """
    meta_path = issue_dir / "meta.json"
    if meta_path.exists():
        try:
            title = (json.loads(meta_path.read_text(encoding="utf-8"))
                     .get("title") or "").strip()
            if title:
                return title
        except Exception as e:
            log.warning("Bad meta.json for %s: %s", issue_dir.name, e)
    idx = issue_dir / "index.html"
    if not idx.exists():
        return ""
    try:
        m = re.search(r'<h1 class="cover-title">([^<]+)</h1>',
                      idx.read_text(encoding="utf-8"))
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def scan_issues(out_dir: Path) -> list[dict]:
    issues: list[dict] = []
    if not out_dir.exists():
        return issues
    for p in sorted(out_dir.iterdir(), reverse=True):
        if not (p.is_dir() and re.match(r"\d{4}-W\d{2}$", p.name)):
            continue
        idx = p / "index.html"
        if not idx.exists():
            continue
        title = _issue_title(p)
        articles_count = len(list(p.glob("article-*.html")))
        iso = p.name.split("-W")
        number = int(iso[1]) if len(iso) == 2 else 0
        issues.append({
            "key": p.name,
            "path": f"{p.name}/index.html",
            "year": iso[0] if iso else "",
            "week": f"W{iso[1]}" if len(iso) == 2 else "",
            "week_label": f"{iso[0]} · W{iso[1]}",
            "title": title,
            "count": articles_count,
            "number": number,
        })
    issues.sort(key=lambda x: x["key"], reverse=True)
    return issues


# 旧版(没有 meta.json 的期)回抓用的卡片解析。
# 卡片从 <a class="card card-b2"> 开始,到下一张卡片为止;字段逐个单独提取,
# 因为改造前的模板没有开转义,属性值里可能夹着 <i> 这类标签或裸引号。
_CARD_OPEN_RE = re.compile(r'<a class="card card-(?P<level>[a-z0-9]+)"', re.S)
# href 可能是相对的(article-x.html,改造前)也可能是根绝对路径
# (/issue/2026-W40/article-x.html,改造后)——两种都要认,否则 meta.json 一丢就漏整期
_ID_RE = re.compile(r'href="[^"]*article-([A-Za-z0-9_-]+)\.html"')


def _first_group(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.S)
    return m.group(1) if m else ""


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]*>", "", text or "")


def _scrape_issue_articles(index_html: str) -> list[dict]:
    """从旧版渲染的 index.html 抓回文章清单(用于没有 meta.json 的历史期)。"""
    out: list[dict] = []
    marks = list(_CARD_OPEN_RE.finditer(index_html))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(index_html)
        chunk = index_html[m.start():end]
        idm = _ID_RE.search(chunk)
        if not idm:
            continue
        level = (m.group("level") or "").upper()
        title = _strip_tags(_first_group(
            r'<h3 class="card-title">(.*?)</h3>', chunk))
        deck = _strip_tags(_first_group(
            r'<p class="card-deck">(.*?)</p>', chunk))
        words = _first_group(
            r'<div class="card-foot">\s*<span>(\d+) words</span>', chunk)
        out.append({
            "id": idm.group(1),
            "title": unescape_html(title).strip(),
            "level": level,
            "level_label": LEVEL_LABEL.get(level, ""),
            "words": int(words or 0),
            "deck": unescape_html(deck).strip(),
            "url": "",
            "source": unescape_html(_first_group(r'data-source="([^"]*)"', chunk)),
            "search": unescape_html(_first_group(r'data-body="([^"]*)"', chunk)),
        })
    return out


_STATS_RE = re.compile(
    r"([\d,]+)\s*words\s*·\s*avg\s*([\d.]+)\s*words/sentence\s*·\s*"
    r"([\d,]+)\s*sentences\s*·\s*([\d.]+)%\s*B2 coverage",
    re.S,
)


def _parse_legacy_article(html: str, card: dict) -> dict | None:
    """从改造前生成的精读页还原一篇文章的渲染数据。

    旧期没有原始抓取结果,也没有存原始出处 URL(旧模板没渲染),所以 url 只能是空字符 ——
    精读页会把来源退化成纯文本而不是外链。
    """
    title = _strip_tags(_first_group(r"<h1>(.*?)</h1>", html)).strip()
    if not title:
        return None

    stats_m = _STATS_RE.search(html)
    if stats_m:
        words = int(stats_m.group(1).replace(",", ""))
        avg_sent = float(stats_m.group(2))
        sentences = int(stats_m.group(3).replace(",", ""))
        coverage = float(stats_m.group(4)) / 100.0
    else:
        words, avg_sent, sentences, coverage = card.get("words", 0), 0.0, 0, 0.0

    body_block = _first_group(r'<div class="entry-body"[^>]*>(.*?)</div>', html)
    body_paragraphs = [unescape_html(_strip_tags(p)).strip()
                       for p in re.findall(r"<p>(.*?)</p>", body_block, re.S)]
    body_paragraphs = [p for p in body_paragraphs if p]

    summary_block = _first_group(r'<div class="entry-summary">(.*?)</div>', html)
    summary_text = unescape_html(_strip_tags(
        summary_block.replace("<strong>Excerpt.</strong>", ""))).strip()

    level = (card.get("level") or "B2").upper()
    return {
        "id": card["id"],
        "source": card.get("source", ""),
        "url": "",
        "title": unescape_html(title),
        "published": "",
        "published_display": _strip_tags(
            _first_group(r"<time>(.*?)</time>", html)).strip() or "—",
        "level": level,
        "level_label": LEVEL_LABEL.get(level, ""),
        "stats": {
            "total_words": words,
            "avg_sent_len": avg_sent,
            "sentences": sentences,
            "coverage_b2": coverage,
        },
        "rare_words": [unescape_html(w) for w in
                       re.findall(r'class="vocab-word[^"]*"\s+data-word="([^"]*)"', html)],
        "deck": card.get("deck", ""),
        "summary_paragraphs": [summary_text] if summary_text else [],
        "body_paragraphs": body_paragraphs,
        "body_search": re.sub(r"<[^>]+>", " ", "\n\n".join(body_paragraphs)).lower(),
    }


def rebuild_legacy_issues(out_dir: Path) -> int:
    """把改造前生成的期(没有 meta.json)就地重渲染成新模板。

    幂等:重渲染后该期就有了 meta.json,后续启动不再触碰。
    只有还原出的文章数达到卡片数的 80% 才落盘,避免把好页面换成残缺版本。
    """
    if not out_dir.exists():
        return 0
    rebuilt = 0
    for it in scan_issues(out_dir):
        key = it["key"]
        issue_dir = out_dir / key
        if (issue_dir / "meta.json").exists():
            continue

        idx = issue_dir / "index.html"
        if not idx.exists():
            continue
        cards = _scrape_issue_articles(idx.read_text(encoding="utf-8"))
        if not cards:
            continue

        articles: list[dict] = []
        for card in cards:
            page = issue_dir / f"article-{card['id']}.html"
            if not page.exists():
                continue
            try:
                parsed = _parse_legacy_article(page.read_text(encoding="utf-8"), card)
            except Exception as e:
                log.warning("Legacy parse failed for %s/%s: %s", key, card["id"], e)
                continue
            if parsed:
                articles.append(parsed)

        if len(articles) < max(1, int(len(cards) * 0.8)):
            log.warning("Legacy rebuild skipped for %s (%d/%d articles recovered)",
                        key, len(articles), len(cards))
            continue

        render_issue_to_dir(articles, issue_dir, it["week_label"], it["title"],
                            it["number"], key=key)
        rebuilt += 1
        log.info("Rebuilt legacy issue %s with the new templates (%d articles)",
                 key, len(articles))
    return rebuilt


def build_catalog(out_dir: Path) -> list[dict]:
    """汇总全部期的机器可读索引并写 catalog.json。

    优先读每期的 meta.json;对现网已有的、早于本次改造的历史期回退抓 index.html。
    不触发任何网络请求。
    """
    catalog: list[dict] = []
    for it in scan_issues(out_dir):
        key = it["key"]
        articles: list[dict] = []
        meta_path = out_dir / key / "meta.json"
        if meta_path.exists():
            try:
                articles = (json.loads(meta_path.read_text(encoding="utf-8"))
                            .get("articles") or [])
            except Exception as e:
                log.warning("Bad meta.json for %s: %s", key, e)
        if not articles:
            idx = out_dir / key / "index.html"
            if idx.exists():
                try:
                    articles = _scrape_issue_articles(idx.read_text(encoding="utf-8"))
                except Exception as e:
                    log.warning("Scrape failed for %s: %s", key, e)
        if not articles:
            log.warning("No article index for %s — skipped in catalog", key)
            continue
        catalog.append({
            "key": key,
            "week_label": it["week_label"],
            "year": it.get("year", ""),
            "title": it["title"],
            "number": it["number"],
            "count": len(articles),
            "articles": articles,
        })
    try:
        (out_dir / "catalog.json").write_text(
            json.dumps({"issues": catalog}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning("catalog.json write failed: %s", e)
    return catalog


def load_catalog(out_dir: Path) -> list[dict]:
    """读 catalog.json 缓存;缺失或为空时重建一次。"""
    path = out_dir / "catalog.json"
    if path.exists():
        try:
            issues = (json.loads(path.read_text(encoding="utf-8"))
                      .get("issues") or [])
            if issues:
                return issues
        except Exception:
            pass
    return build_catalog(out_dir)


def _issue_counts(issue: dict | None) -> dict[str, int]:
    counts = {"B1": 0, "B2": 0, "C1": 0}
    for a in (issue or {}).get("articles") or []:
        lv = (a.get("level") or "").upper()
        if lv in counts:
            counts[lv] += 1
    return counts


def featured_articles(issue: dict | None, per_level: int = 1) -> list[dict]:
    """首页「本期精选」:每层取前 N 篇。"""
    out: list[dict] = []
    for lv in ("B1", "B2", "C1"):
        out.extend([a for a in (issue or {}).get("articles") or []
                    if (a.get("level") or "").upper() == lv][:per_level])
    return out


def write_index_landing(out_dir: Path, issues: list[dict]) -> None:
    """渲染首页门户 + archive 列表,并刷新跨期 catalog。

    首页不再是"最新一期的副本":它有自己的 hero、精选、难度入口与往期入口。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    static_dest = out_dir / "static"
    if static_dest.exists():
        shutil.rmtree(static_dest)
    shutil.copytree(STATIC_DIR, static_dest)

    catalog = build_catalog(out_dir)
    latest = catalog[0] if catalog else None

    level_totals = {"B1": 0, "B2": 0, "C1": 0}
    for issue in catalog:
        for a in issue["articles"]:
            lv = (a.get("level") or "").upper()
            if lv in level_totals:
                level_totals[lv] += 1

    home_html = _env.get_template("home.html.j2").render(
        page="home",
        nav="home",
        issue=latest,
        featured=featured_articles(latest),
        counts=_issue_counts(latest),
        total=latest["count"] if latest else 0,
        recent=catalog[1:4],
        level_totals=level_totals,
    )
    (out_dir / "index.html").write_text(home_html, encoding="utf-8")

    # 期数卡片数一律以 catalog 为准 —— scan_issues 数的是磁盘上的 article-*.html,
    # 可能残留旧的孤儿文件,导致 archive 与首页/本期自相矛盾
    counted = {c["key"]: c["count"] for c in catalog}
    archive_issues = [{**it, "count": counted.get(it["key"], it.get("count", 0))}
                      for it in issues]
    archive_html = _env.get_template("archive.html.j2").render(
        page="archive",
        nav="archive",
        issues=archive_issues,
        pieces=sum(i["count"] for i in archive_issues),
    )
    (out_dir / "archive.html").write_text(archive_html, encoding="utf-8")


_JUNK_PATTERNS = [
    # 嵌入播放器残留
    re.compile(r"embed embed\b", re.I),
    re.compile(r"<iframe", re.I),
    # 整条 URL 的样板段(CBS/NPR 抓取会带一行 "https://…/#post-update-… link copied")
    re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.I),
    re.compile(r"\blink copied\b", re.I),
]

def _filter_junk(records: list[dict]) -> list[dict]:
    """删掉没有阅读价值的文章;可救的文章只剥掉样板段落。"""
    out = []
    for r in records:
        body = r.get("body") or ""
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        clean = [p for p in paras
                 if not any(pat.search(p) for pat in _JUNK_PATTERNS)]
        cleaned = "\n\n".join(clean).strip()
        # 正文过短的一律丢弃(新闻简报摘要/导语,不构成可精读的文章)
        if len(cleaned.split()) < 120:
            log.info("junk dropped: %s (%s)", r.get("title", "")[:50], r.get("source", ""))
            continue
        r["body"] = cleaned
        out.append(r)
    return out


def _dedupe_same_story(records: list[dict]) -> list[dict]:
    """同一故事的分级重复(如同一来源的 level 1/2/3 变体)只留正文最长的一篇。"""
    import hashlib
    best: dict[str, dict] = {}
    order: list[str] = []
    for r in records:
        norm = re.sub(r"[\s\-–—_]*level\s*\d+$", "", (r.get("title") or "").strip(),
                      flags=re.I).strip().lower()
        if not norm:
            norm = r.get("id", "")
        n = len(r.get("body") or "")
        if norm not in best:
            best[norm] = r
            order.append(norm)
        else:
            if n > len(best[norm].get("body") or ""):
                best[norm] = r
    return [best[k] for k in order]


_CAPTION_RE = re.compile(
    r"(photo by|getty images|reuters|ap hide caption|hide caption|illustration by)", re.I)

def _strip_caption_deck(articles: list[dict]) -> None:
    """图片版权/图注类英文 deck 直接清空,避免作为简介显示。"""
    for a in articles:
        d = (a.get("deck") or "").strip()
        if d and _CAPTION_RE.search(d):
            a["deck"] = ""


def _apply_zh_summaries(articles: list[dict]) -> None:
    """卡片简介换成中文总结:先查预生成缓存(周一晚批量接口的产物,秒填),
    缺的才内联调 DeepSeek(并发)。生成不出来就留空,不回退英文。"""
    from concurrent.futures import ThreadPoolExecutor
    import deepseek_client
    if not articles:
        return

    # 查库优先 —— 文章 id = sha1(url),跨天稳定,周一晚批量的结果在这里等
    try:
        import db
        cached = db.get_summaries([a["id"] for a in articles])
    except Exception as e:
        log.warning("summary cache read failed: %s", e)
        cached = {}
    for a in articles:
        zh = cached.get(a["id"])
        if zh:
            a["deck"] = zh
    hits = sum(1 for a in articles if cached.get(a["id"]))
    todo = [a for a in articles if not cached.get(a["id"])]
    log.info("zh summaries: %d from cache, %d to generate inline", hits, len(todo))
    if not todo or not deepseek_client.is_configured():
        return

    def one(a):
        try:
            zh = deepseek_client.summarize_zh(a.get("title", ""),
                                              a.get("body") or a.get("deck", ""))
            a["deck"] = zh or ""   # 生成不出来就留空,不回退英文
        except Exception as e:
            log.warning("zh summary failed for %s: %s", a.get("title", "")[:40], e)
            a["deck"] = ""
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, todo))
    log.info("zh summaries applied")


def prepare_summary_batch(config: dict | None = None,
                          max_articles: int = 60) -> dict:
    """周一晚:真实收集候选文章,把缺简介的用 8 线程预生成,直接写 article_summary。

    DeepSeek 官方没有 Batch API(其 /files 只服务聊天文件上传),所以"批量"
    实为出刊前的离线预生成 —— 好处不变:周二早上正式更新按 id 直取,不再等
    AI 往返。文章 id = sha1(url) 跨天稳定;周二实际收录与周一候选的差异由
    _apply_zh_summaries 的内联兜底补齐。返回 {generated, cached, failed}。
    """
    from concurrent.futures import ThreadPoolExecutor
    import deepseek_client
    out = {"generated": 0, "cached": 0, "failed": 0}
    if not deepseek_client.is_configured():
        log.info("summary prepare skipped: LLM not configured")
        return out
    try:
        candidates = collect_candidates(config, max_articles)
    except Exception as e:
        log.warning("summary prepare collect failed: %s", e)
        return out
    if not candidates:
        log.info("summary prepare: no candidates")
        return out
    try:
        import db
        have = db.summaries_existing([r["id"] for r in candidates])
    except Exception as e:
        log.warning("summary cache check failed: %s", e)
        have = set()
    todo = [{"id": r["id"], "title": r.get("title", ""),
             "body": r.get("body") or r.get("deck", "")}
            for r in candidates if r["id"] not in have and (r.get("body") or "").strip()]
    out["cached"] = len(candidates) - len(todo)
    if not todo:
        log.info("summary prepare: all %d candidates already cached", len(candidates))
        return out

    def one(item):
        try:
            zh = deepseek_client.summarize_zh(item["title"], item["body"])
            if zh:
                db.save_summary(item["id"], item["title"], zh,
                                model="pre-gen")
                out["generated"] += 1
            else:
                out["failed"] += 1
        except Exception as e:
            log.warning("summary pre-gen failed for %s: %s", item["id"], e)
            out["failed"] += 1
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, todo))
    log.info("summary prepare done: %s (candidates=%d)", out, len(candidates))
    return out


def _interleave_by_source(records: list[dict]) -> list[dict]:
    """按来源轮流穿插。截断到 max_articles 时,避免排在末尾的来源(如 HTML 源)被整体丢弃。"""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in records:
        s = r.get("source") or ""
        if s not in groups:
            groups[s] = []
            order.append(s)
        groups[s].append(r)
    out: list[dict] = []
    round_idx = 0
    while True:
        added = False
        for s in order:
            g = groups[s]
            if round_idx < len(g):
                out.append(g[round_idx])
                added = True
        if not added:
            break
        round_idx += 1
    return out


def collect_candidates(config: dict | None = None,
                       max_articles: int = 60) -> list[dict]:
    """收集管线:抓取 → 过滤 → 去重 → 按源穿插 → 截断。只收集,不分级不渲染。

    full_refresh(周二早上正式出刊)和周一晚的批量简介预生成共用这一条管线,
    保证"提前预生成的那批文章"和"最终进刊的这批"是同一批口径筛出来的。
    """
    cfg = config or {}
    raw = collect(cfg)
    raw = _filter_junk(raw)
    raw = _dedupe_same_story(raw)
    raw = _interleave_by_source(raw)
    return raw[:max_articles]


def full_refresh(out_dir: Path,
                 config: dict | None = None,
                 max_articles: int = 60) -> dict:
    """
    一次完整刷新:抓取 → 分级 → 渲染到 out_dir/<issue_key>/ → 更新主页与 archive。
    返回最新一期路径信息。
    """
    raw = collect_candidates(config, max_articles)
    log.info("Refresh collected %d articles", len(raw))
    if not raw:
        return {"ok": False, "reason": "no articles"}

    articles = [build_article(r) for r in raw]
    _strip_caption_deck(articles)
    _apply_zh_summaries(articles)
    key = issue_key()
    week = week_label()
    number = _today().isocalendar()[1]
    title = issue_title(articles)
    issue_dir = out_dir / key

    render_issue_to_dir(articles, issue_dir, week, title, number)

    # 同步 archive + 根 index.html
    issues = scan_issues(out_dir)
    write_index_landing(out_dir, issues)

    return {
        "ok": True,
        "issue_key": key,
        "week": week,
        "title": title,
        "count": len(articles),
        "issue_dir": str(issue_dir),
    }