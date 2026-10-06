"""
FastAPI 主程序 — Weekly English 后端服务
  - 主页 / 精读页 / archive:由 core 渲染
  - /api/dict?word=x:查词(离线 ECDICT + MyMemory 机翻,不调 AI)
  - /api/ask:对 DeepSeek 提问,自动限制到本周文章
  - /api/issues:列出全部期数(给前端 SPA 或小部件用)
  - /healthz:健康检查
  - APScheduler:每周一 07:00 自动刷新
"""
from __future__ import annotations
import json
import logging
import os
import threading
import time
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Request, Depends, Response
from fastapi.responses import (HTMLResponse, FileResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

import core
import deepseek_client
import dict_client
import db
import auth
import ielts

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "output"
STATIC_DIR = ROOT / "static"

# ---------------- 模板 ----------------
# autoescape 必须显式开:模板后缀是 .html.j2,select_autoescape(["html"]) 对它
# 返回 False,曾经造成用户输入(搜索词)不转义直出 —— 反射型 XSS。
# 模板变量全是标量,无 HTML 内容输出;既有的 |e 与 autoescape 不冲突。
from jinja2 import Environment, FileSystemLoader
_TPL_ENV = Environment(
    loader=FileSystemLoader(str(ROOT / "templates")),
    autoescape=True,
    trim_blocks=True, lstrip_blocks=True,
)
_TPL_ENV.globals["asset"] = core.asset

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("server")

app = FastAPI(title="Weekly English", version="1.0.0")

# gzip 压缩:HTML/CSS/JS 体积减 70%+,弱网/远程访问明显提速
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)

# 静态资源缓存:文件名带 ?v=<版本号>,版本号一变 URL 就变,所以可以放心长缓存。
# 版本号由 core.ASSET_VERSIONS 统一管理,模板用 asset() 生成,改 static/ 后
# 启动时 core.refresh_asset_versions() 会就地重写已渲染页面里的引用。
class CachedStatic(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


# 镜像内种子 —— 挂载持久化卷会把 /app/data 整个盖住,卷上永远看不到镜像里备的东西
_CEFR_SEED = Path("/opt/cefr_seed/cefr_vocab")
_ISSUES_SEED = Path("/opt/issues_seed")
_DICT_SEED = Path("/opt/dict_seed")

# 本轮启动从种子补进来的期数 —— 补过就必须重建首页/archive/catalog
_seeded_issue_count = 0


def _ensure_cefr_seed() -> None:
    """卷上可能为空、或缺少后来新增的词表文件 —— 逐个补齐缺失文件(已存在的不动)。"""
    import shutil
    dst = OUT_DIR.parent / "cefr_vocab"
    if not _CEFR_SEED.exists():
        # 非 Docker 环境没有镜像种子,属正常;卷上的词表以现状为准
        log.info("No cefr seed in image (normal outside Docker); keeping %s as-is", dst)
        return
    dst.mkdir(parents=True, exist_ok=True)
    copied = []
    for f in _CEFR_SEED.iterdir():
        if f.is_file() and not (dst / f.name).exists():
            shutil.copyfile(f, dst / f.name)
            copied.append(f.name)
    if copied:
        log.info("cefr_vocab seed restored: %s", ", ".join(copied))


def _ensure_issues_seed() -> int:
    """把镜像里的历史期补进 out_dir。

    周更只产当期、旧的又抓不回来,所以历史期只能随镜像发种子:卷被重建过
    (或从没拿到过历史期)时,archive 里就只剩当期。整目录补齐,已有的期不动
    —— 当期由每周刷新自己维护,复制的成品页也由启动时的版本重写/词表重筛跟上。
    """
    global _seeded_issue_count
    if not _ISSUES_SEED.is_dir():
        return 0
    import shutil
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    added: list[str] = []
    for week in sorted(_ISSUES_SEED.iterdir()):
        if not (week.is_dir() and re.match(r"\d{4}-W\d{2}$", week.name)):
            continue
        dst = OUT_DIR / week.name
        if dst.exists():
            continue
        try:
            shutil.copytree(week, dst)
            added.append(week.name)
        except Exception as e:
            # 半成品目录会把下次启动的补种挡住(dst.exists() 为真),必须清掉
            shutil.rmtree(dst, ignore_errors=True)
            log.warning("Issue seed copy failed for %s: %s", week.name, e)
    if added:
        _seeded_issue_count += len(added)
        log.info("Seeded %d historical issue(s): %s", len(added), ", ".join(added))
    return len(added)


def _ensure_dict_seed() -> None:
    """离线词典的压缩源(gz)也放卷里 —— 卷上缺就从镜像种子补一份。

    真正的 sqlite 索引不随镜像发(比 gz 大 3 倍),由 dict_client 从 gz 现建,
    建好落在卷上,重启复用。
    """
    import shutil
    dst = OUT_DIR.parent / "dict" / dict_client.GZ_PATH.name
    if dst.exists():
        return
    src = _DICT_SEED / dict_client.GZ_PATH.name
    if not src.exists():
        log.warning("ecdict gz missing and no seed found at %s", src)
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        log.info("ecdict seed restored: %s (%.1f MB)",
                 dst, dst.stat().st_size / 1048576)
    except Exception as e:
        log.warning("ecdict seed copy failed: %s", e)


def _ensure_data_seed():
    """挂载持久化卷后 /app/data 可能为空、或缺少后来新增的文件 —— 从镜像内种子补齐。"""
    _ensure_cefr_seed()
    _ensure_dict_seed()
    _ensure_issues_seed()


# ---------- lifespan:启动收尾 + 关停 ----------
# 取代三个 @app.on_event("startup")(新 FastAPI 已弃用 on_event)。
# lifespan 里引用的 scheduler / _background_refresh_once 定义在本文件后部 ——
# 函数体在服务真正启动时才执行,届时整个模块早已加载完。
@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ensure_data_seed()
    try:
        db.init_db()
        log.info("DB initialized: %s", db.get_database_url().split("://")[0])
    except Exception as e:
        log.warning("DB init failed: %s", e)
    if not scheduler.running:
        scheduler.start()
    # 初始刷新放后台线程,不阻塞启动;失败用户在其他页面才会触发
    threading.Thread(target=_background_refresh_once,
                     name="initial-refresh", daemon=True).start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Weekly English", version="1.0.0", lifespan=lifespan)

# /static 挂载必须在这个真正的 app 上 —— 之前挂在文件前部那个被 191 行覆盖的
# 旧 app 实例上,静态文件一直靠 catch-all 从 data/output/static 兜底,新增的
# static/ 文件(如 ielts.js)要等下一次全量渲染才会出现。
app.mount("/static", CachedStatic(directory=str(STATIC_DIR)), name="static")


# ---------- 主页 / 精读页 / archive ----------
def _latest_issue_key() -> Optional[str]:
    issues = core.scan_issues(OUT_DIR)
    return issues[0]["key"] if issues else None


# 预渲染成品页的读盘缓存:path → (mtime_ns, size, text)。
# 一期目录页 180~220KB,同一份文件被反复读盘纯属浪费;顺手带上 ETag,
# 浏览器后退/来回点文章时走 304,不用重下整篇文档。
_file_cache: dict[str, tuple[tuple[int, int], str]] = {}


def _cached_text(path: Path) -> Optional[tuple[str, str]]:
    """读盘缓存 + 弱 ETag。文件不存在返回 None。"""
    try:
        st = path.stat()
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    etag = f'W/"{stamp[0]:x}-{stamp[1]:x}"'
    hit = _file_cache.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1], etag
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # 每条 ~200KB 文本,不设上限的话期数累积后内存会一直涨
    if len(_file_cache) > 256:
        _file_cache.clear()
    _file_cache[str(path)] = (stamp, text)
    return text, etag


def _file_response(path: Path, request: Request, media: str) -> Response:
    got = _cached_text(path)
    if got is None:
        raise HTTPException(404, "Not Found")
    text, etag = got
    # no-cache = 每次都回来问一句,而不是不许缓存 —— 配合 ETag 才能命中 304
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=text, media_type=media, headers=headers)


def _html_response(path: Path, request: Request) -> Response:
    """把磁盘上的成品页返回给浏览器(内存缓存 + ETag/304)。"""
    return _file_response(path, request, "text/html; charset=utf-8")


# 首访兜底抓取的锁 —— 并发首访只触发一次,防重复全站抓取(烧 AI 摘要 token)
_refresh_lock = threading.Lock()


def _kick_full_refresh():
    if not _refresh_lock.acquire(blocking=False):
        return
    try:
        core.full_refresh(OUT_DIR)
    except Exception as e:
        log.warning("Background initial refresh failed: %s", e)
    finally:
        _refresh_lock.release()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """首页门户 —— 独立于最新一期的 /issue/{key}。"""
    path = OUT_DIR / "index.html"
    # catalog.json 缺失说明卷上还是改造前的旧版静态页 —— 就地重建,不必重新联网抓取
    if not path.exists() or not (OUT_DIR / "catalog.json").exists():
        issues = core.scan_issues(OUT_DIR)
        if issues:
            core.write_index_landing(OUT_DIR, issues)
    if not path.exists():
        # 一期都还没有 —— 后台补抓,立刻返回提示页。同步抓全程要几分钟,
        # 会把首访请求(以及它占住的线程池线程)挂住;并发首访由锁去重。
        threading.Thread(target=_kick_full_refresh, name="kick-refresh",
                         daemon=True).start()
        return HTMLResponse(
            "<h1>Weekly English</h1>"
            "<p>No articles collected yet. The first fetch is running in the "
            "background — please refresh in a minute.</p>",
            status_code=503,
        )
    return _html_response(path, request)


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request):
    path = OUT_DIR / "archive.html"
    if not path.exists():
        # 无 archive 时先空生成一份
        issues = core.scan_issues(OUT_DIR)
        core.write_index_landing(OUT_DIR, issues)
    return _html_response(path, request)


@app.get("/issue/latest")
def issue_latest():
    """/issue/latest → 重定向到当前最新一期。

    声明位置必须早于 /issue/{key},否则会被参数路由吃掉。
    """
    key = _latest_issue_key()
    if not key:
        raise HTTPException(404, "No issues rendered yet")
    return RedirectResponse(f"/issue/{key}", status_code=307)


@app.get("/issue/article-{article_id}.html", response_class=HTMLResponse)
def article_under_issue_prefix(article_id: str, request: Request):
    """/issue/2026-W38 页上的相对链接会解析成 /issue/article-x.html — 回落最新一期。"""
    issues = core.scan_issues(OUT_DIR)
    if not issues:
        raise HTTPException(404, "No issues rendered yet")
    path = OUT_DIR / issues[0]["key"] / f"article-{article_id}.html"
    if not path.exists():
        raise HTTPException(404, "Article not found")
    return _html_response(path, request)


@app.get("/issue/static/{file_path:path}")
def issue_prefix_static(file_path: str):
    """/issue/article-x.html 页的相对静态资源解析到 /issue/static/... — 用主静态目录伺服。"""
    candidate = (STATIC_DIR / file_path).resolve()
    if not candidate.is_relative_to(STATIC_DIR.resolve()) or not candidate.is_file():
        raise HTTPException(404, "Not Found")
    return FileResponse(candidate)


@app.get("/issue/{key}", response_class=HTMLResponse)
def issue_index(key: str, request: Request):
    path = OUT_DIR / key / "index.html"
    if not path.exists():
        raise HTTPException(404, f"Issue {key} not found")
    return _html_response(path, request)


@app.get("/issue/{key}/search.json")
def issue_search_corpus(key: str, request: Request):
    """本期的全文搜索语料(article_id → 正文)。

    以前这份语料内嵌在目录页每张卡片的 data-body 里,一期 150KB,把页面撑到 220KB+;
    现在按需取,和 HTML 一样走 ETag/304。
    """
    return _file_response(OUT_DIR / key / "search.json", request,
                          "application/json; charset=utf-8")


@app.get("/article/{issue_key}/{article_id}", response_class=HTMLResponse)
def article(issue_key: str, article_id: str, request: Request):
    path = OUT_DIR / issue_key / f"article-{article_id}.html"
    if not path.exists():
        raise HTTPException(404, "Article not found")
    return _html_response(path, request)


# ---------- 渲染页兜底路由 ----------
# 模板里的链接是相对静态文件名(article-<id>.html / <key>/index.html),
# 在 file:// 本地预览下可用;以下兜底路由让同样的链接在服务器 URL 下也能命中。

@app.get("/issue/{issue_key}/article-{article_id}.html", response_class=HTMLResponse)
def article_in_issue(issue_key: str, article_id: str, request: Request):
    path = OUT_DIR / issue_key / f"article-{article_id}.html"
    if not path.exists():
        raise HTTPException(404, "Article not found")
    return _html_response(path, request)


@app.get("/article-{article_id}.html", response_class=HTMLResponse)
def article_latest(article_id: str, request: Request):
    issues = core.scan_issues(OUT_DIR)
    if not issues:
        raise HTTPException(404, "No issues rendered yet")
    path = OUT_DIR / issues[0]["key"] / f"article-{article_id}.html"
    if not path.exists():
        raise HTTPException(404, "Article not found")
    return _html_response(path, request)


# ---------- 跨期浏览:/level 与 /search ----------
LEVEL_META = {
    "B1": ("Starter", "Short sentences and everyday vocabulary — a comfortable place to begin."),
    "B2": ("Intermediate", "Longer pieces with a wider vocabulary; the bulk of each issue."),
    "C1": ("Advanced", "Dense prose and demanding vocabulary, close to native newsroom writing."),
}

# catalog.json 的 mtime 缓存 —— 每周刷新后自动失效重载,平时不重复读盘
_CATALOG_CACHE: dict = {"mtime": None, "issues": [], "index": {}}


def _catalog_index() -> dict:
    """(期号, 文章 id) → {title, level} —— 给阅读进度补标题用。

    进度表本身只存 issue_key/article_id/时长,没有标题,所以六行会一模一样,
    看不出读的是哪一篇;这里从 catalog 补上。
    """
    _catalog()
    return _CATALOG_CACHE["index"]


def _catalog() -> list[dict]:
    path = OUT_DIR / "catalog.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if mtime is None or _CATALOG_CACHE["mtime"] != mtime:
        _CATALOG_CACHE["issues"] = core.load_catalog(OUT_DIR)
        _CATALOG_CACHE["index"] = {
            (issue["key"], a.get("id")): {
                "title": a.get("title", ""),
                "level": a.get("level", ""),
            }
            for issue in _CATALOG_CACHE["issues"]
            for a in issue.get("articles") or []
        }
        try:
            _CATALOG_CACHE["mtime"] = path.stat().st_mtime
        except OSError:
            _CATALOG_CACHE["mtime"] = None
    return _CATALOG_CACHE["issues"]


@app.get("/level/{level}", response_class=HTMLResponse)
def level_page(level: str):
    lv = level.upper()
    if lv not in LEVEL_META:
        raise HTTPException(404, f"Unknown level: {level}")
    label, blurb = LEVEL_META[lv]

    groups, total = [], 0
    for issue in _catalog():
        picks = [a for a in issue["articles"]
                 if (a.get("level") or "").upper() == lv]
        if not picks:
            continue
        groups.append({
            "issue_key": issue["key"],
            "week_label": issue["week_label"],
            "title": issue["title"],
            "articles": picks,
        })
        total += len(picks)

    html = _TPL_ENV.get_template("level.html.j2").render(
        page="level", nav="level-" + lv, level=lv,
        level_label=label, level_blurb=blurb,
        groups=groups, total=total,
    )
    return HTMLResponse(html)


# 各期全文语料的 mtime 缓存 —— 语料在 search.json 里,不再走 catalog
_CORPUS_CACHE: dict[str, tuple[float, dict]] = {}


def _issue_corpus(issue_key: str) -> dict:
    """某一期的全文搜索语料(article_id → 正文)。按 mtime 缓存,不重复读盘。"""
    path = OUT_DIR / issue_key / "search.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    hit = _CORPUS_CACHE.get(issue_key)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception as e:
        log.warning("Bad search.json for %s: %s", issue_key, e)
        data = {}
    # 一期语料 ~150KB;上限防期数累积后内存一直涨
    if len(_CORPUS_CACHE) > 64:
        _CORPUS_CACHE.clear()
    _CORPUS_CACHE[issue_key] = (mtime, data)
    return data


@app.get("/search", response_class=HTMLResponse)
def search_page(q: str = ""):
    """全站搜索 —— 结果有自己的 URL,可分享、可回退。"""
    query = (q or "").strip()
    results: list[dict] = []
    if query:
        needle = query.lower()
        for issue in _catalog():
            corpus = _issue_corpus(issue["key"])
            for a in issue["articles"]:
                haystack = f"{a.get('title', '')}\n{corpus.get(a['id'], '')}".lower()
                if needle in haystack:
                    results.append({
                        "id": a["id"],
                        "title": a.get("title", ""),
                        "level": a.get("level", ""),
                        "deck": a.get("deck", ""),
                        "issue_key": issue["key"],
                        "week_label": issue["week_label"],
                    })
    html = _TPL_ENV.get_template("search.html.j2").render(
        page="search", nav="search", q=query, results=results,
        total=len(results),
    )
    return HTMLResponse(html)


# ---------- 雅思学习专区 ----------
@app.get("/ielts", response_class=HTMLResponse)
def ielts_home():
    total = len(ielts.ensure_words())
    html = _TPL_ENV.get_template("ielts.html.j2").render(
        page="ielts", nav="ielts", total_words=total)
    return HTMLResponse(html)


@app.get("/ielts/vocab", response_class=HTMLResponse)
def ielts_vocab_page(q: str = "", page: int = 1):
    result = ielts.query(q=q, page=page)
    pageno = result.pop("pageno")
    html = _TPL_ENV.get_template("ielts_vocab.html.j2").render(
        page="ielts-vocab", nav="ielts", pageno=pageno, **result)
    return HTMLResponse(html)


@app.get("/ielts/flashcards", response_class=HTMLResponse)
def ielts_flashcards_page():
    html = _TPL_ENV.get_template("ielts_flashcards.html.j2").render(
        page="ielts-flashcards", nav="ielts")
    return HTMLResponse(html)


@app.get("/ielts/practice", response_class=HTMLResponse)
def ielts_practice_page():
    html = _TPL_ENV.get_template("ielts_practice.html.j2").render(
        page="ielts-practice", nav="ielts",
        ai_ready=deepseek_client.is_configured())
    return HTMLResponse(html)


@app.get("/api/ielts/words")
def api_ielts_words(q: str = "", page: int = 1):
    """词表搜索 + 分页(无需登录)。"""
    return ielts.query(q=q, page=page)


@app.get("/api/ielts/drill")
def api_ielts_drill(limit: int = 20, request: Request = None):
    """闪卡抽词:登录用户优先抽未掌握的(box<3),其余按词表顺序补足;
    未登录就按词表顺序给。返回 items 带 box/known。"""
    limit = max(1, min(limit, 60))
    all_words = ielts.ensure_words()
    picked: list[dict] = []
    user_id = None
    try:
        authz = (request.headers.get("authorization") or "")
        if authz.lower().startswith("bearer "):
            user_id = int(auth.decode_token(authz[7:].strip()) or 0) or None
    except Exception:
        user_id = None
    progress: dict[str, dict] = {}
    if user_id:
        try:
            progress = db.ielts_progress_get(
                user_id, [w["word"] for w in all_words])
        except Exception as e:
            log.warning("ielts progress read failed: %s", e)
    def _with_p(w):
        p = progress.get(w["word"]) or {}
        return {**w, "box": p.get("box", 0), "known": p.get("known", False)}
    if user_id:
        unmastered = [w for w in all_words
                      if (progress.get(w["word"]) or {}).get("box", 0) < 3]
        mastered = [w for w in all_words
                    if (progress.get(w["word"]) or {}).get("box", 0) >= 3]
        picked = [_with_p(w) for w in (unmastered + mastered)[:limit]]
    else:
        picked = [_with_p(w) for w in all_words[:limit]]
    return {"items": picked, "total": len(all_words), "logged_in": bool(user_id)}


class IeltsProgressIn(BaseModel):
    word: str
    known: bool


@app.post("/api/ielts/progress")
def api_ielts_progress(payload: IeltsProgressIn,
                       user: db.User = Depends(auth.require_user)):
    word = (payload.word or "").strip().lower()
    if not word or len(word) > 64:
        raise HTTPException(400, "word is required")
    return db.ielts_progress_set(user.id, word, bool(payload.known))


@app.get("/api/ielts/progress")
def api_ielts_progress_list(user: db.User = Depends(auth.require_user)):
    with db.get_session_local()() as ses:
        rows = (ses.query(db.IeltsCardProgress)
                .filter(db.IeltsCardProgress.user_id == user.id).all())
        items = [{"word": r.word, "box": r.box or 0, "known": bool(r.known)}
                 for r in rows]
    return {"items": items,
            "mastered": sum(1 for i in items if i["box"] >= 3)}


class IeltsWritingIn(BaseModel):
    question: str = ""
    essay: str


@app.post("/api/ielts/writing")
def api_ielts_writing(payload: IeltsWritingIn, request: Request,
                      user: db.User = Depends(auth.require_user)):
    """Task 2 批改(需登录,走全局月度 AI 配额)。同步 def:阻塞调用进线程池。"""
    essay = (payload.essay or "").strip()
    if len(essay) < 50:
        raise HTTPException(400, "essay too short (min 50 chars)")
    if _quota_exceeded("deepseek", request):
        return {"ok": False, "refused": False,
                "content": "本月 AI 额度已用完,下月自动恢复。"}
    info = deepseek_client.ielts_writing_feedback(payload.question, essay)
    _record_usage("deepseek", info.get("usage_tokens") or 0, request)
    return info


class IeltsSpeakingIn(BaseModel):
    part: int = 1
    action: str = "start"          # start | answer | feedback
    history: list[dict] = []


@app.post("/api/ielts/speaking")
def api_ielts_speaking(payload: IeltsSpeakingIn, request: Request,
                       user: db.User = Depends(auth.require_user)):
    """口语模拟一轮(需登录,走同一配额)。同步 def,理由同上。"""
    action = (payload.action or "start").strip()
    if action not in ("start", "answer", "feedback"):
        raise HTTPException(400, "bad action")
    history = payload.history[-24:]
    for h in history:
        if not isinstance(h, dict) or not isinstance(h.get("text", ""), str):
            raise HTTPException(400, "bad history item")
        h["text"] = h["text"].strip()[:2000]
    if _quota_exceeded("deepseek", request):
        return {"ok": False, "refused": False,
                "content": "本月 AI 额度已用完,下月自动恢复。"}
    info = deepseek_client.ielts_speaking_turn(history, payload.part, action)
    _record_usage("deepseek", info.get("usage_tokens") or 0, request)
    return info


# ---------- API ----------
@app.get("/api/issues")
def api_issues():
    return {"issues": core.scan_issues(OUT_DIR)}


# ---------- AI 月度配额(每模型 20 万 tokens;开发者账户不限) ----------
AI_MONTHLY_TOKEN_LIMIT = int(os.environ.get("AI_MONTHLY_TOKEN_LIMIT", "200000"))
DEVELOPER_EMAIL = os.environ.get("DEVELOPER_EMAIL", "1607045457@qq.com")


def _request_email(request: Request) -> str | None:
    """从请求的 Bearer token 解出登录邮箱;未登录返回 None。"""
    authz = request.headers.get("authorization") or ""
    if not authz.lower().startswith("bearer "):
        return None
    try:
        uid = auth.decode_token(authz[7:].strip())
        if not uid:
            return None
        with db.get_session_local()() as ses:
            u = ses.get(db.User, int(uid))
            return (u.email if u else None)
    except Exception:
        return None


def _quota_exceeded(provider: str, request: Request | None = None) -> bool:
    if request is not None and _request_email(request) == DEVELOPER_EMAIL:
        return False   # 开发者账户无视限制
    try:
        return db.get_month_usage(provider) >= AI_MONTHLY_TOKEN_LIMIT
    except Exception as e:
        # 失败关闭:查不到用量就拒绝。放行等于配额失效,烧的是全局共享池。
        log.error("quota check failed for %s — refusing AI call: %s", provider, e)
        return True


def _record_usage(provider: str, tokens: int, request: Request | None = None):
    if request is not None and _request_email(request) == DEVELOPER_EMAIL:
        return
    try:
        db.add_usage(provider, tokens)
    except Exception as e:
        log.warning("usage record failed: %s", e)


# 查词负缓存 —— 查不到的词短 TTL 内不重查:MyMemory 兜底一次超时 6s,
# 生僻词被反复点开时不能每次都慢一遍。只缓存失败,成功的走共享库。
_NEG_GLOSS_TTL = 600.0
_neg_gloss: dict[str, float] = {}


@app.get("/api/dict")
def api_dict(word: str = Query(..., min_length=1, max_length=64),
             sentence: str | None = Query(None)):
    """单词速查 —— 全链路不调 AI:共享缓存 → 离线 ECDICT → MyMemory 免费机翻。

    手机浏览器没有内置翻译引擎,这条就是它们的主力路径,所以既不能烧 token 也不能慢。
    `sentence` 只用来分缓存键(同一个词在不同上下文里算不同条目),不参与取词。
    """
    word = word.strip().lower()
    if not word:
        raise HTTPException(400, "word is required")
    ctx = (sentence or "").strip()[:160]
    ck = f"{word}|{ctx}"
    now = time.time()
    if _neg_gloss.get(ck, 0) > now:
        return {"ok": False, "word": word, "translation": ""}
    try:
        shared = db.get_shared_gloss(ck)
        if shared:
            # 旧时代的缓存行格式不一(音标带斜杠、甚至有把提示词吐回来的脏行),
            # 统一清洗;洗不出有效释义就当作没命中,落到词典重新查
            shared = dict_client.normalize_gloss(shared)
        if shared:
            shared["lemma"] = dict_client.lemma_of(word)
            return shared
    except Exception as e:
        log.warning("shared gloss read failed: %s", e)

    info = dict_client.lookup(word)
    if info.get("ok") and info.get("translation"):
        _neg_gloss.pop(ck, None)
        try:
            db.save_shared_gloss(ck, word, info, model=info.get("model") or "")
        except Exception as e:
            log.warning("shared gloss save failed: %s", e)
    else:
        if len(_neg_gloss) > 2000:
            _neg_gloss.clear()
        _neg_gloss[ck] = now + _NEG_GLOSS_TTL
    return info


class AskIn(BaseModel):
    question: str = ""
    issue_key: str | None = None
    article_id: str | None = None


@app.post("/api/ask")
def api_ask(payload: AskIn, request: Request,
            user: db.User = Depends(auth.require_user)):
    """用户提问(需登录;严格限制到本周文章 + 英语学习)。

    注意这里必须是同步 def。里面的 deepseek_client.ask 是阻塞式的 HTTP 调用,
    写在 async def 里会把事件循环占住 —— 单 worker 部署下,只要有一个人在提问,
    全站所有请求(包括 HTML/CSS/JS)都得排队等它返回。同步 def 会被 FastAPI
    丢进线程池执行,顺带也不再卡住同一路由里的同步 sqlite 调用。
    """
    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    if _quota_exceeded("deepseek", request):
        return {"ok": False, "refused": False, "content": "本月 AI 额度已用完,下月自动恢复。"}
    issue_key = payload.issue_key or _latest_issue_key()
    focus = (_load_focus_article(payload.article_id, issue_key)
             if (issue_key and payload.article_id) else None)
    articles = _load_articles_context(issue_key) if issue_key else []
    info = deepseek_client.ask(question, context_articles=articles,
                               focus_article=focus)
    _record_usage("deepseek", info.get("usage_tokens") or 0, request)
    return info


@app.get("/healthz")
def health():
    return {
        "ok": True,
        "deepseek_configured": deepseek_client.is_configured(),
        "latest_issue": _latest_issue_key(),
        "db": db.get_database_url().split("://")[0],
        "jwt_secret_configured": auth.JWT_SECRET != "dev-only-secret-change-in-prod",
    }


# ---------- 鉴权路由 ----------
@app.post("/auth/register", response_model=auth.TokenOut)
def register(payload: auth.RegisterIn, sess: Session = Depends(db.get_db)):
    email = payload.email.lower().strip()
    if len(payload.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    existing = sess.query(db.User).filter_by(email=email).first()
    if existing:
        raise HTTPException(409, "Email already registered.")
    user = db.User(email=email, password_hash=auth.hash_password(payload.password))
    sess.add(user)
    sess.commit()
    sess.refresh(user)
    token = auth.create_access_token(user.id)
    return auth.TokenOut(access_token=token, user=auth.UserOut.model_validate(user))


@app.post("/auth/login", response_model=auth.TokenOut)
def login(payload: auth.LoginIn, sess: Session = Depends(db.get_db)):
    email = payload.email.lower().strip()
    user = sess.query(db.User).filter_by(email=email).first()
    if not user or not auth.verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password.")
    token = auth.create_access_token(user.id)
    return auth.TokenOut(access_token=token, user=auth.UserOut.model_validate(user))


@app.get("/auth/me", response_model=auth.UserOut)
def me(user: db.User = Depends(auth.require_user)):
    return auth.UserOut.model_validate(user)


# ---------- 登录 / 注册页 ----------
@app.get("/auth/login", response_class=HTMLResponse)
def auth_login_page():
    html = _TPL_ENV.get_template("auth.html.j2").render(
        page="auth", nav="", title="Sign in", mode="login")
    return HTMLResponse(html)


@app.get("/auth/register", response_class=HTMLResponse)
def auth_register_page():
    html = _TPL_ENV.get_template("auth.html.j2").render(
        page="auth", nav="", title="Create account", mode="register")
    return HTMLResponse(html)


# ---------- 个人页(生词本 + 阅读进度 + 阅读历史)----------
@app.get("/me", response_class=HTMLResponse)
def me_page():
    # 页面骨架交给模板,三个区块的数据由 static/me.js 拿 token 填充
    html = _TPL_ENV.get_template("me.html.j2").render(page="me", nav="me")
    return HTMLResponse(html)


@app.get("/auth/logout")
def logout():
    # token 在前端
    return HTMLResponse(
        "<script>localStorage.clear();location.href='/';</script>"
    )


# ---------- 用户数据路由 ----------
@app.post("/api/history")
def add_history(payload: dict,
                sess: Session = Depends(db.get_db),
                user: db.User = Depends(auth.require_user)):
    """记录一次"打开了哪篇文章"。"""
    issue_key = (payload.get("issue_key") or "").strip()
    article_id = (payload.get("article_id") or "").strip()
    title = (payload.get("title") or "").strip()[:512]
    if not issue_key or not article_id:
        raise HTTPException(400, "issue_key and article_id required")
    sess.add(db.ReadingHistory(
        user_id=user.id, issue_key=issue_key, article_id=article_id, title=title,
    ))
    sess.commit()
    return {"ok": True}


@app.post("/api/v1/progress")
def report_progress(payload: dict,
                    sess: Session = Depends(db.get_db),
                    user: db.User = Depends(auth.require_user)):
    """前端每 ~10 秒上报阅读时长 + 滚动百分比。"""
    issue_key = (payload.get("issue_key") or "").strip()
    article_id = (payload.get("article_id") or "").strip()
    try:
        seconds = max(0, int(payload.get("seconds") or 0))
        scroll = max(0.0, min(100.0, float(payload.get("scroll_pct") or 0)))
    except (TypeError, ValueError):
        raise HTTPException(400, "seconds and scroll_pct must be numeric")
    if scroll != scroll:                     # float("nan") 能穿过 min/max
        scroll = 0.0
    completed = bool(payload.get("completed") or scroll >= 95)
    if not issue_key or not article_id:
        raise HTTPException(400, "issue_key and article_id required")

    p = sess.query(db.ReadingProgress).filter_by(
        user_id=user.id, issue_key=issue_key, article_id=article_id,
    ).first()
    if p is None:
        p = db.ReadingProgress(user_id=user.id,
                               issue_key=issue_key,
                               article_id=article_id)
        sess.add(p)
    p.seconds_read = max(p.seconds_read or 0, seconds)
    p.scroll_pct = max(p.scroll_pct or 0, scroll)
    if completed:
        p.completed = True
    sess.commit()
    return {"ok": True, "completed": p.completed,
            "scroll_pct": p.scroll_pct, "seconds": p.seconds_read}


@app.post("/api/v1/vocab")
def add_vocab(payload: dict,
              sess: Session = Depends(db.get_db),
              user: db.User = Depends(auth.require_user)):
    """把一个生词加入自己的生词本。"""
    word = (payload.get("word") or "").strip().lower()
    if not word:
        raise HTTPException(400, "word required")
    sentence = (payload.get("sentence") or "")[:1000]
    issue_key = (payload.get("issue_key") or "")[:32]
    article_id = (payload.get("article_id") or "")[:64]
    definition = (payload.get("definition") or "")[:1000]
    translation = (payload.get("translation") or "")[:1000]

    existing = sess.query(db.VocabEntry).filter_by(
        user_id=user.id, word=word,
        source_issue=issue_key, source_article=article_id,
    ).first()
    if existing:
        existing.definition = definition or existing.definition
        existing.translation = translation or existing.translation
        existing.sentence = sentence or existing.sentence
        sess.commit()
        return {"ok": True, "id": existing.id, "updated": True}

    entry = db.VocabEntry(
        user_id=user.id, word=word, sentence=sentence,
        source_issue=issue_key, source_article=article_id,
        definition=definition, translation=translation,
    )
    sess.add(entry)
    sess.commit()
    return {"ok": True, "id": entry.id}


@app.delete("/api/v1/vocab/{entry_id}")
def remove_vocab(entry_id: int,
                 sess: Session = Depends(db.get_db),
                 user: db.User = Depends(auth.require_user)):
    entry = sess.query(db.VocabEntry).filter_by(
        id=entry_id, user_id=user.id,
    ).first()
    if not entry:
        raise HTTPException(404, "Not found")
    sess.delete(entry)
    sess.commit()
    return {"ok": True}


@app.get("/api/v1/vocab")
def list_vocab(sess: Session = Depends(db.get_db),
               user: db.User = Depends(auth.require_user)):
    rows = sess.query(db.VocabEntry).filter_by(user_id=user.id)\
               .order_by(db.VocabEntry.created_at.desc()).all()
    return {"entries": [
        {
            "id": e.id,
            "word": e.word,
            "sentence": e.sentence,
            "definition": e.definition,
            "translation": e.translation,
            "source_issue": e.source_issue,
            "source_article": e.source_article,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]}


@app.get("/api/v1/progress")
def list_progress(sess: Session = Depends(db.get_db),
                  user: db.User = Depends(auth.require_user)):
    rows = (sess.query(db.ReadingProgress)
            .filter_by(user_id=user.id)
            .order_by(db.ReadingProgress.updated_at.desc())
            .all())
    index = _catalog_index()
    out = []
    for p in rows:
        meta = index.get((p.issue_key, p.article_id)) or {}
        out.append({
            "issue_key": p.issue_key,
            "article_id": p.article_id,
            # 标题从 catalog 补 —— 进度表里没存,否则每行都只剩一个期号
            "title": meta.get("title", ""),
            "level": meta.get("level", ""),
            "seconds_read": p.seconds_read,
            "scroll_pct": p.scroll_pct,
            "completed": p.completed,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        })
    return {"progress": out}


MAX_DISPLAY_NAME = 24


@app.post("/api/v1/profile")
def update_profile(payload: dict,
                   sess: Session = Depends(db.get_db),
                   user: db.User = Depends(auth.require_user)):
    """改昵称(显示名)。邮箱是身份,改不了;昵称只影响页面上怎么称呼你。"""
    raw = (payload.get("display_name") or "").strip()
    if len(raw) > MAX_DISPLAY_NAME:
        raise HTTPException(400, f"昵称最多 {MAX_DISPLAY_NAME} 个字符。")
    if any(ch in raw for ch in "\r\n\t"):
        raise HTTPException(400, "昵称不能包含换行。")
    row = sess.query(db.User).filter_by(id=user.id).first()
    if not row:
        raise HTTPException(404, "User not found")
    row.display_name = raw            # 传空串即清空,前端会回退显示邮箱
    sess.commit()
    sess.refresh(row)
    return {"ok": True, "user": auth.UserOut.model_validate(row).model_dump()}


@app.get("/api/v1/history")
def list_history(limit: int = Query(60, ge=1, le=500),
                 sess: Session = Depends(db.get_db),
                 user: db.User = Depends(auth.require_user)):
    """最近的打开记录 —— /me 的「阅读历史」区块用。"""
    rows = (sess.query(db.ReadingHistory)
            .filter_by(user_id=user.id)
            .order_by(db.ReadingHistory.opened_at.desc())
            .limit(limit).all())
    return {"entries": [
        {
            "id": r.id,
            "issue_key": r.issue_key,
            "article_id": r.article_id,
            "title": r.title,
            "opened_at": r.opened_at.isoformat() if r.opened_at else None,
        }
        for r in rows
    ]}


# ---------- 辅助 ----------
_articles_ctx_cache: dict[str, tuple[float, list[dict]]] = {}
_CTX_CACHE_MAX = 32          # 条数上限;超限整体清空(每条只是几 KB 的标题清单)


def _load_articles_context(issue_key: str) -> list[dict]:
    """从本期索引里抽出文章的标题/URL/来源,作为 LLM 上下文。

    每次提问都重读 + 正则扫一遍本期全部文章 HTML 是白费力气,内容没变就直接复用。
    失效靠 meta.json 的 mtime:每次重渲染都会重写它(只覆盖同名文件的话目录
    mtime 是不动的,不能拿来当判据)。
    """
    issue_dir = OUT_DIR / issue_key
    meta_path = issue_dir / "meta.json"
    try:
        stamp = meta_path.stat().st_mtime if meta_path.exists() else issue_dir.stat().st_mtime
    except OSError:
        return []

    hit = _articles_ctx_cache.get(issue_key)
    if hit and hit[0] == stamp:
        return hit[1]

    out: list[dict] = []
    for p in sorted(issue_dir.glob("article-*.html")):
        try:
            txt = p.read_text(encoding="utf-8")
            title_m = re.search(r"<h1>([^<]+)</h1>", txt)
            src_m = re.search(r'class="src">([^<]+)</span>', txt)
            url_m = re.search(r'href="(https?://[^"]+)"', txt)
            out.append({
                "title": title_m.group(1).strip() if title_m else "",
                "source": src_m.group(1).strip() if src_m else "",
                "url": url_m.group(1).strip() if url_m else "",
            })
        except Exception:
            continue
    if len(_articles_ctx_cache) > _CTX_CACHE_MAX:
        _articles_ctx_cache.clear()
    _articles_ctx_cache[issue_key] = (stamp, out)
    return out


def _load_focus_article(article_id: str | None,
                        issue_key: str | None) -> dict | None:
    """用户正在读的那篇文章:标题/来源/URL + 正文摘录(前 4000 字符)。

    没有正文,模型只看得到标题清单,问"这篇文章里 X 是什么意思"就会被误判成
    超范围拒答。正文取自该期 search.json 语料(渲染时落盘的全量小写文本),
    只进 system 消息不落库。article_id 不在指定期里时按 catalog 反查真实归属期
    —— 前端只从 URL 抠 id,期号传错的场合仍能带上正确正文。
    """
    aid = (article_id or "").strip()[:64]
    if not aid:
        return None
    catalog = _catalog()
    art, key = None, issue_key
    for it in catalog:                       # 先在指定期里找
        if it["key"] != key:
            continue
        art = next((a for a in it["articles"] if a.get("id") == aid), None)
        break
    if art is None:                          # 再全 catalog 反查
        for it in catalog:
            found = next((a for a in it["articles"] if a.get("id") == aid), None)
            if found:
                key, art = it["key"], found
                break
    if art is None:
        return None
    body = _issue_corpus(key).get(aid, "")[:4000]
    return {
        "title": art.get("title", ""),
        "source": art.get("source", ""),
        "url": art.get("url", ""),
        "body": body,
    }


# ---------- 启动时不阻塞:后台线程做初始抓取 ----------
def _background_refresh_once():
    # 0) 老期页面 + 老 meta.json 的就地迁移(先备份再改)。
    #    语料抽到 search.json、删掉编号目录与词汇汇总块、去掉内嵌 data-body、修旧文案。
    #    必须在 refresh_asset_versions 之前 —— 这样备份里存的是"这轮改动之前"的原样。
    try:
        core.migrate_rendered_data(OUT_DIR)
    except Exception as e:
        log.warning("Rendered-data migration failed: %s", e)

    # 1) 磁盘上现成的页面引用的是渲染那一刻的资源版本号 —— 先就地改写,
    #    免得浏览器继续用缓存里的旧 app.js / style.css(不联网,很快)
    try:
        core.refresh_asset_versions(OUT_DIR)
    except Exception as e:
        log.warning("Asset version refresh failed: %s", e)

    # 1b) 页面上的生词表也是渲染那一刻的白名单算出来的 —— 按当前
    #     data/cefr_vocab/* 重筛一遍,把后来加白的词从老期页面摘掉(不联网)
    try:
        core.refresh_rare_words(OUT_DIR)
    except Exception as e:
        log.warning("Rare-word refresh failed: %s", e)

    # 1b2) 导航新增了雅思专区入口 —— 老页面的导航是渲染那一刻烙进去的,
    #      landing 又不会因它重建,就地补上(不联网)
    try:
        core.refresh_nav_links(OUT_DIR)
    except Exception as e:
        log.warning("Nav link refresh failed: %s", e)

    # 1c) 离线词典:首次启动从 gz 建 sqlite(几秒),放到后台做,别让第一个
    #     查词的请求去等建索引
    try:
        dict_client.ensure_ready()
    except Exception as e:
        log.warning("ECDICT index warm-up failed: %s", e)

    # 2) 改造前生成的期没有 meta.json —— 就地从它们自己的 HTML 重渲染,保证全站模板一致
    try:
        rebuilt = core.rebuild_legacy_issues(OUT_DIR)
    except Exception as e:
        rebuilt = 0
        log.warning("Legacy issue rebuild failed: %s", e)

    # 3) catalog.json 缺失(旧卷)、刚迁移过、或刚补进历史期 —— 重建首页 / archive / 跨期索引
    if rebuilt or _seeded_issue_count or not (OUT_DIR / "catalog.json").exists():
        try:
            issues = core.scan_issues(OUT_DIR)
            if issues:
                core.write_index_landing(OUT_DIR, issues)
                log.info("Rebuilt home + catalog (%d issue(s), %d migrated)",
                         len(issues), rebuilt)
        except Exception as e:
            log.warning("Landing rebuild failed: %s", e)

    # 4) 一期都没有时才联网抓取
    if not (OUT_DIR / "index.html").exists():
        try:
            core.full_refresh(OUT_DIR)
        except Exception as e:
            log.warning("Initial refresh failed: %s", e)


# ---------- 定时调度:周一晚预生成简介,周二 06:30 正式更新 ----------
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _scheduled_refresh():
    try:
        log.info("Scheduled weekly refresh starting...")
        result = core.full_refresh(OUT_DIR)
        core.refresh_asset_versions(OUT_DIR)   # 老期不会因这次抓取重渲染,版本号得单独补
        log.info("Scheduled refresh done: %s", result)
    except Exception as e:
        log.warning("Scheduled refresh failed: %s", e)


# 卡片简介:周一晚离线预生成,周二早上出刊按 id 直取 article_summary 表,不再等
# AI 往返。(DeepSeek 无官方 Batch API —— 其 /files 只服务聊天文件上传 —— 所以
# "批量"实为出刊前的内联并发预生成,直接写缓存库。)
def _prepare_batch_job():
    try:
        result = core.prepare_summary_batch()
        log.info("Summary pre-generation done: %s", result)
    except Exception as e:
        log.warning("Summary pre-generation failed: %s", e)


scheduler.add_job(
    _scheduled_refresh,
    CronTrigger(day_of_week="tue", hour=6, minute=30),
    id="weekly_refresh",
    replace_existing=True,
    coalesce=True,
)
scheduler.add_job(
    _prepare_batch_job,
    CronTrigger(day_of_week="mon", hour=20, minute=0),
    id="summary_prepare",
    replace_existing=True,
    coalesce=True,
)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server.main:app", host="0.0.0.0", port=port, reload=False)

# ---------- 渲染页兜底路由(必须放在最后,避免遮蔽 /api/* 等路由) ----------
# 模板里的链接是相对静态文件名(article-<id>.html / <key>/index.html),
# 在 file:// 本地预览下可用;以下兜底路由让同样的链接在服务器 URL 下也能命中。

@app.get("/{file_path:path}", include_in_schema=False)
def static_pages_fallback(file_path: str):
    """兜底:serve OUT_DIR 下生成的静态页(<key>/index.html、archive.html 等)。"""
    if not file_path:
        raise HTTPException(404, "Not Found")
    candidate = (OUT_DIR / file_path).resolve()
    if not candidate.is_relative_to(OUT_DIR.resolve()):
        raise HTTPException(404, "Not Found")
    if candidate.suffix.lower() not in {".html", ".css", ".js",
                                        ".png", ".jpg", ".jpeg", ".svg", ".ico",
                                        ".woff", ".woff2"}:
        raise HTTPException(404, "Not Found")
    if not candidate.is_file():
        raise HTTPException(404, "Not Found")
    media = "text/html; charset=utf-8" if candidate.suffix == ".html" else None
    return FileResponse(candidate, media_type=media)
