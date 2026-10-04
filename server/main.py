"""
FastAPI 主程序 — Weekly English 后端服务
  - 主页 / 精读页 / archive:由 core 渲染
  - /api/dict?word=x:DeepSeek 词典查询(替代有道,可回退)
  - /api/ask:对 DeepSeek 提问,自动限制到本周文章
  - /api/issues:列出全部期数(给前端 SPA 或小部件用)
  - /api/admin/refresh:手动触发本周抓取
  - /healthz:健康检查
  - APScheduler:每周一 07:00 自动刷新
"""
from __future__ import annotations
import json
import logging
import os
import threading
import re
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
import db
import auth

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "output"
STATIC_DIR = ROOT / "static"

# ---------------- 模板 ----------------
from jinja2 import Environment, FileSystemLoader, select_autoescape
_TPL_ENV = Environment(
    loader=FileSystemLoader(str(ROOT / "templates")),
    autoescape=select_autoescape(["html"]),
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

app.mount("/static", CachedStatic(directory=str(STATIC_DIR)), name="static")


def _ensure_data_seed():
    """挂载持久化卷后 /app/data 可能为空、或缺少后来新增的词表文件 — 从镜像内种子补齐缺失文件。"""
    import shutil
    dst = Path(__file__).resolve().parent.parent / "data" / "cefr_vocab"
    for seed in (Path("/opt/cefr_seed/cefr_vocab"),):
        if not seed.exists():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        copied = []
        for f in seed.iterdir():
            if f.is_file() and not (dst / f.name).exists():
                shutil.copyfile(f, dst / f.name)
                copied.append(f.name)
        if copied:
            log.info("cefr_vocab seed restored: %s", ", ".join(copied))
        return
    log.warning("cefr_vocab missing and no seed found at %s", dst)


@app.on_event("startup")
def _init_db():
    _ensure_data_seed()
    try:
        db.init_db()
        log.info("DB initialized: %s", db.get_database_url().split("://")[0])
    except Exception as e:
        log.warning("DB init failed: %s", e)


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
        # 一期都还没有 —— 自动触发一次抓取
        log.info("No issue found, triggering refresh...")
        result = core.full_refresh(OUT_DIR)
        if not result.get("ok"):
            return HTMLResponse(
                "<h1>Weekly English</h1>"
                "<p>No articles collected yet. Check your RSS sources and network.</p>",
                status_code=503,
            )
    if not path.exists():
        raise HTTPException(404, "Home page missing.")
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
    if not str(candidate).startswith(str(STATIC_DIR.resolve())) or not candidate.is_file():
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


# ---------- API ----------
@app.get("/api/issues")
def api_issues():
    return {"issues": core.scan_issues(OUT_DIR)}


# ---------- AI 月度配额(每模型各 50 万 tokens;开发者账户不限) ----------
AI_MONTHLY_TOKEN_LIMIT = int(os.environ.get("AI_MONTHLY_TOKEN_LIMIT", "500000"))
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
    except Exception:
        return False


def _record_usage(provider: str, tokens: int, request: Request | None = None):
    if request is not None and _request_email(request) == DEVELOPER_EMAIL:
        return
    try:
        db.add_usage(provider, tokens)
    except Exception as e:
        log.warning("usage record failed: %s", e)


@app.get("/api/dict")
def api_dict(request: Request,
             word: str = Query(..., min_length=1),
             sentence: str | None = Query(None)):
    """单词速查:云端共享词典(所有用户共用一份) → 未命中才调 LLM 并入库。"""
    word = word.strip().lower()
    if not word:
        raise HTTPException(400, "word is required")
    ctx = (sentence or "").strip()[:160]
    ck = f"{word}|{ctx}"   # 与 deepseek_client.lookup_word 的键保持一致
    try:
        shared = db.get_shared_gloss(ck)
        if shared and shared.get("translation"):
            return shared
    except Exception as e:
        log.warning("shared gloss read failed: %s", e)
    if _quota_exceeded("glm", request):
        return {"ok": False, "word": word,
                "translation": "本月 AI 额度已用完,下月自动恢复。"}
    info = deepseek_client.lookup_word(word, sentence_context=sentence)
    if info.get("ok"):
        _record_usage("glm", info.get("usage_tokens") or 0, request)
        try:
            db.save_shared_gloss(ck, word, info, model=info.get("model") or "")
        except Exception as e:
            log.warning("shared gloss save failed: %s", e)
    return info


class AskIn(BaseModel):
    question: str = ""
    issue_key: str | None = None


@app.post("/api/ask")
def api_ask(payload: AskIn, request: Request):
    """用户提问(严格限制到本周文章 + 英语学习)。

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
    articles = _load_articles_context(issue_key) if issue_key else []
    info = deepseek_client.ask(question, context_articles=articles)
    _record_usage("deepseek", info.get("usage_tokens") or 0, request)
    return info


@app.post("/api/admin/refresh")
def manual_refresh():
    """手动触发一次完整刷新 — 用于首次启动或调试。"""
    result = core.full_refresh(OUT_DIR)
    return result


@app.get("/healthz")
def health():
    return {
        "ok": True,
        "deepseek_configured": deepseek_client.is_configured(),
        "latest_issue": _latest_issue_key(),
        "db": db.get_database_url().split("://")[0],
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
    seconds = max(0, int(payload.get("seconds") or 0))
    scroll = max(0.0, min(100.0, float(payload.get("scroll_pct") or 0)))
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
    _articles_ctx_cache[issue_key] = (stamp, out)
    return out


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

    # 2) 改造前生成的期没有 meta.json —— 就地从它们自己的 HTML 重渲染,保证全站模板一致
    try:
        rebuilt = core.rebuild_legacy_issues(OUT_DIR)
    except Exception as e:
        rebuilt = 0
        log.warning("Legacy issue rebuild failed: %s", e)

    # 3) catalog.json 缺失(旧卷)或刚迁移过 —— 重建首页 / archive / 跨期索引
    if rebuilt or not (OUT_DIR / "catalog.json").exists():
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


@app.on_event("startup")
def _on_startup():
    # 不阻塞主线程;失败用户在其他页面才会触发
    threading.Thread(target=_background_refresh_once,
                     name="initial-refresh",
                     daemon=True).start()


# ---------- 定时调度:每周一 07:00 ----------
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _scheduled_refresh():
    try:
        log.info("Scheduled weekly refresh starting...")
        result = core.full_refresh(OUT_DIR)
        core.refresh_asset_versions(OUT_DIR)   # 老期不会因这次抓取重渲染,版本号得单独补
        log.info("Scheduled refresh done: %s", result)
    except Exception as e:
        log.warning("Scheduled refresh failed: %s", e)


scheduler.add_job(
    _scheduled_refresh,
    CronTrigger(day_of_week="mon", hour=7, minute=0),
    id="weekly_refresh",
    replace_existing=True,
    coalesce=True,
)


@app.on_event("startup")
def _start_scheduler():
    if not scheduler.running:
        scheduler.start()


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
    if not str(candidate).startswith(str(OUT_DIR.resolve())):
        raise HTTPException(404, "Not Found")
    if candidate.suffix.lower() not in {".html", ".css", ".js",
                                        ".png", ".jpg", ".jpeg", ".svg", ".ico",
                                        ".woff", ".woff2"}:
        raise HTTPException(404, "Not Found")
    if not candidate.is_file():
        raise HTTPException(404, "Not Found")
    media = "text/html; charset=utf-8" if candidate.suffix == ".html" else None
    return FileResponse(candidate, media_type=media)
