"""
core — 后端 / 脚本共用的业务逻辑入口

把 run_weekly.py 里的"采集 → 分级 → 渲染"抽离成纯函数,供:
  - run_weekly.py(本地手动渲染)
  - server/main.py(FastAPI 后端 + 定时调度)
共享同一份实现,避免重复代码。
"""
from __future__ import annotations
import datetime as dt
import logging
import os
import re
import shutil
from pathlib import Path

import jinja2

from fetchers.rss_fetcher import fetch_many
from fetchers.api_fetcher import fetch_newsapi
from fetchers.guardian_fetcher import fetch_guardian
from fetchers.html_fetcher import fetch_index_pages, fetch_page
from pipeline.difficulty import evaluate
from pipeline.highlight import find_out_of_scope_words
from pipeline.summary import offline_summary

log = logging.getLogger(__name__)

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
    {"name": "New Scientist",
     "feed": "https://www.newscientist.com/feed/home",
     "limit": 2, "full_text": True},
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
    {"name": "Engadget",
     "feed": "https://www.engadget.com/rss.xml",
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
    {"name": "News in Levels",
     "feed": "https://newsinlevels.com/feed/",
     "limit": 4, "full_text": True},
    {"name": "Breaking News English",
     "feed": "https://breakingnewsenglish.com/rss.xml",
     "limit": 4, "full_text": True},
    {"name": "Simple English News",
     "feed": "https://www.simpleenglishnews.com/feed",
     "limit": 2, "full_text": True},
]

# 无 RSS 的免费源 — 走 HTML 爬取(RSS 已下线的 China Daily 等)
DEFAULT_HTML_SOURCES = [
    {"name": "China Daily",
     "index_url": "https://www.chinadaily.com.cn/world",
     "link_sel": "a[href*='/a/2']",
     "base_url": "https://www.chinadaily.com.cn",
     "limit": 8,
     "title_sel": "h1",
     "body_sel": "#Content"},
]

# The Guardian 官方 API — 设置 GUARDIAN_API_KEY 即启用(免费开发 key,
# open-platform.theguardian.com 注册;API 域名国内直连可达)
DEFAULT_GUARDIAN_SECTIONS = ["world", "technology"]


def week_label(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    iso = today.isocalendar()
    return f"{iso.year} · W{iso.week:02d}"


def issue_key(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


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

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
    trim_blocks=True, lstrip_blocks=True,
)


def render_issue_to_dir(articles: list[dict],
                        out_dir: Path,
                        week: str,
                        title: str,
                        number: int) -> Path:
    """渲染一期到指定目录:index.html + article-<id>.html + static/"""
    out_dir.mkdir(parents=True, exist_ok=True)

    static_dest = out_dir / "static"
    if static_dest.exists():
        shutil.rmtree(static_dest)
    shutil.copytree(STATIC_DIR, static_dest)

    sources = sorted({a["source"] for a in articles})
    counts = {"B1": 0, "B2": 0, "C1": 0}
    for a in articles:
        counts[a["level"]] += 1
    issue_ctx = {
        "issue": {"week_label": week, "title": title, "number": number},
        "articles": articles,
        "sources": sources,
        "counts": counts,
    }

    issue_html = _env.get_template("issue.html.j2").render(page="index", **issue_ctx)
    (out_dir / "index.html").write_text(issue_html, encoding="utf-8")

    art_tpl = _env.get_template("article.html.j2")
    for a in articles:
        art_html = art_tpl.render(page="article", article=a, **issue_ctx)
        (out_dir / f"article-{a['id']}.html").write_text(art_html, encoding="utf-8")

    log.info("Rendered issue: %s (%d articles)", out_dir, len(articles))
    return out_dir


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
        title = ""
        try:
            txt = idx.read_text(encoding="utf-8")
            m = re.search(r'<h1 class="cover-title">([^<]+)</h1>', txt)
            if m:
                title = m.group(1).strip()
        except Exception:
            pass
        articles_count = len(list(p.glob("article-*.html")))
        iso = p.name.split("-W")
        number = int(iso[1]) if len(iso) == 2 else 0
        issues.append({
            "key": p.name,
            "path": f"{p.name}/index.html",
            "week_label": f"{iso[0]} · W{iso[1]}",
            "title": title,
            "count": articles_count,
            "number": number,
        })
    issues.sort(key=lambda x: x["key"], reverse=True)
    return issues


def write_index_landing(out_dir: Path, issues: list[dict]) -> None:
    """把根 index.html 设为最新一期,archive.html 列出全部。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    static_dest = out_dir / "static"
    if static_dest.exists():
        shutil.rmtree(static_dest)
    shutil.copytree(STATIC_DIR, static_dest)

    if issues:
        latest = issues[0]
        latest_path = out_dir / latest["path"]
        target = out_dir / "index.html"
        shutil.copyfile(latest_path, target)
        html = target.read_text(encoding="utf-8")
        html = html.replace(
            'href="article-', 'href="' + latest["key"] + '/article-'
        ).replace(
            'href="index.html"', 'href="' + latest["key"] + '/index.html"'
        )
        target.write_text(html, encoding="utf-8")

    archive_html = _env.get_template("archive.html.j2").render(
        page="archive", issues=issues, issues_count=len(issues),
    )
    (out_dir / "archive.html").write_text(archive_html, encoding="utf-8")


_JUNK_PATTERNS = [
    # News in Levels 每篇文章都带的站点推广样板文 — 不是正文
    re.compile(r"do you want to learn english", re.I),
    re.compile(r"we have a special book for you", re.I),
    # 嵌入播放器残留
    re.compile(r"embed embed\b", re.I),
]

def _filter_junk(records: list[dict]) -> list[dict]:
    """删掉没有阅读价值的文章:站点推广样板文、嵌入播放器乱码简介。"""
    out = []
    for r in records:
        text = (r.get("deck") or "") + " " + (r.get("body") or "")[:500]
        if any(p.search(text) for p in _JUNK_PATTERNS):
            log.info("junk dropped: %s (%s)", r.get("title", "")[:50], r.get("source", ""))
            continue
        out.append(r)
    return out


def _dedupe_same_story(records: list[dict]) -> list[dict]:
    """同一故事的分级重复(如 News in Levels level 1/2/3)只留正文最长的一篇。"""
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


def _apply_zh_summaries(articles: list[dict]) -> None:
    """卡片简介换成 GLM 中文总结(并发,失败回退英文摘要)。"""
    from concurrent.futures import ThreadPoolExecutor
    import deepseek_client
    if not deepseek_client.is_configured():
        return
    def one(a):
        try:
            zh = deepseek_client.summarize_zh(a.get("title", ""),
                                              a.get("body") or a.get("deck", ""))
            if zh:
                a["deck"] = zh
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, articles))
    log.info("zh summaries applied")


def full_refresh(out_dir: Path,
                 config: dict | None = None,
                 max_articles: int = 60) -> dict:
    """
    一次完整刷新:抓取 → 分级 → 渲染到 out_dir/<issue_key>/ → 更新主页与 archive。
    返回最新一期路径信息。
    """
    cfg = config or {}
    raw = collect(cfg)
    raw = _filter_junk(raw)
    raw = _dedupe_same_story(raw)
    raw = raw[:max_articles]
    log.info("Refresh collected %d articles", len(raw))
    if not raw:
        return {"ok": False, "reason": "no articles"}

    articles = [build_article(r) for r in raw]
    _apply_zh_summaries(articles)
    key = issue_key()
    week = week_label()
    number = dt.date.today().isocalendar()[1]
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