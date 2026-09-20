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
import logging
import os
import threading
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("server")

app = FastAPI(title="Weekly English", version="1.0.0")

# gzip 压缩:HTML/CSS/JS 体积减 70%+,弱网/远程访问明显提速
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)

# 静态资源缓存:带版本参数时浏览器可长缓存,减少重复下载
class CachedStatic(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp

app.mount("/static", CachedStatic(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _init_db():
    try:
        db.init_db()
        log.info("DB initialized: %s", db.get_database_url().split("://")[0])
    except Exception as e:
        log.warning("DB init failed: %s", e)


# ---------- 主页 / 精读页 / archive ----------
def _latest_issue_key() -> Optional[str]:
    issues = core.scan_issues(OUT_DIR)
    return issues[0]["key"] if issues else None


@app.get("/", response_class=HTMLResponse)
def index():
    key = _latest_issue_key()
    if not key:
        # 没有期数时自动触发一次
        log.info("No issue found, triggering refresh...")
        result = core.full_refresh(OUT_DIR)
        if not result.get("ok"):
            return HTMLResponse(
                "<h1>Weekly English</h1>"
                "<p>No articles collected yet. Check your RSS sources and network.</p>",
                status_code=503,
            )
        key = result["issue_key"]
    path = OUT_DIR / key / "index.html"
    if not path.exists():
        raise HTTPException(404, "Issue page missing.")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/archive", response_class=HTMLResponse)
def archive():
    path = OUT_DIR / "archive.html"
    if not path.exists():
        # 无 archive 时先空生成一份
        issues = core.scan_issues(OUT_DIR)
        core.write_index_landing(OUT_DIR, issues)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/issue/article-{article_id}.html", response_class=HTMLResponse)
def article_under_issue_prefix(article_id: str):
    """/issue/2026-W38 页上的相对链接会解析成 /issue/article-x.html — 回落最新一期。"""
    issues = core.scan_issues(OUT_DIR)
    if not issues:
        raise HTTPException(404, "No issues rendered yet")
    path = OUT_DIR / issues[0]["key"] / f"article-{article_id}.html"
    if not path.exists():
        raise HTTPException(404, "Article not found")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/issue/static/{file_path:path}")
def issue_prefix_static(file_path: str):
    """/issue/article-x.html 页的相对静态资源解析到 /issue/static/... — 用主静态目录伺服。"""
    candidate = (STATIC_DIR / file_path).resolve()
    if not str(candidate).startswith(str(STATIC_DIR.resolve())) or not candidate.is_file():
        raise HTTPException(404, "Not Found")
    return FileResponse(candidate)


@app.get("/issue/{key}", response_class=HTMLResponse)
def issue_index(key: str):
    path = OUT_DIR / key / "index.html"
    if not path.exists():
        raise HTTPException(404, f"Issue {key} not found")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/article/{issue_key}/{article_id}", response_class=HTMLResponse)
def article(issue_key: str, article_id: str):
    path = OUT_DIR / issue_key / f"article-{article_id}.html"
    if not path.exists():
        raise HTTPException(404, "Article not found")
    return HTMLResponse(path.read_text(encoding="utf-8"))


# ---------- 渲染页兜底路由 ----------
# 模板里的链接是相对静态文件名(article-<id>.html / <key>/index.html),
# 在 file:// 本地预览下可用;以下兜底路由让同样的链接在服务器 URL 下也能命中。

@app.get("/issue/{issue_key}/article-{article_id}.html", response_class=HTMLResponse)
def article_in_issue(issue_key: str, article_id: str):
    path = OUT_DIR / issue_key / f"article-{article_id}.html"
    if not path.exists():
        raise HTTPException(404, "Article not found")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/article-{article_id}.html", response_class=HTMLResponse)
def article_latest(article_id: str):
    issues = core.scan_issues(OUT_DIR)
    if not issues:
        raise HTTPException(404, "No issues rendered yet")
    path = OUT_DIR / issues[0]["key"] / f"article-{article_id}.html"
    if not path.exists():
        raise HTTPException(404, "Article not found")
    return HTMLResponse(path.read_text(encoding="utf-8"))


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


@app.post("/api/ask")
async def api_ask(request: Request):
    """用户提问(严格限制到本周文章 + 英语学习)。"""
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    if _quota_exceeded("deepseek", request):
        return {"ok": False, "refused": False, "content": "本月 AI 额度已用完,下月自动恢复。"}
    issue_key = body.get("issue_key") or _latest_issue_key()
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
        title="Sign in", mode="login")
    return HTMLResponse(html)


@app.get("/auth/register", response_class=HTMLResponse)
def auth_register_page():
    html = _TPL_ENV.get_template("auth.html.j2").render(
        title="Create account", mode="register")
    return HTMLResponse(html)


# ---------- 个人页(生词本 + 阅读历史)----------
@app.get("/me", response_class=HTMLResponse)
def me_page():
    # 前端 JS 拿 token 读 /api/v1/vocab 与 /api/v1/progress
    return HTMLResponse(
        "<!doctype html><html><head><meta charset=utf-8>"
        "<title>Me · Weekly English</title>"
        "<link rel=stylesheet href=/static/style.css></head>"
        "<body data-page=me><header class=masthead><div class=wrap>"
        "<a class=brand href=/><span class=logo-mark>W·E</span>"
        "<span class=logo-text>Weekly English</span></a>"
        "<nav class=meta><a class=nav-link href=/>← Latest issue</a></nav>"
        "</div></header><main class=wrap>"
        "<section class=cover cover-compact>"
        "<div class=cover-kicker>Account</div>"
        "<h1 class=cover-title id=me-title>Loading…</h1>"
        "<div class=cover-meta id=me-meta></div>"
        "</section>"
        "<section id=me-content><p>Loading your data…</p></section>"
        "</main><footer class=foot><div class=wrap>"
        "<span>Weekly English</span>"
        "<span>Originals preserved verbatim.</span>"
        "</div></footer>"
        "<script src=/static/me.js></script></body></html>"
    )


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
    rows = sess.query(db.ReadingProgress).filter_by(user_id=user.id).all()
    return {"progress": [
        {
            "issue_key": p.issue_key,
            "article_id": p.article_id,
            "seconds_read": p.seconds_read,
            "scroll_pct": p.scroll_pct,
            "completed": p.completed,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in rows
    ]}


# ---------- 辅助 ----------
def _load_articles_context(issue_key: str) -> list[dict]:
    """从本期索引里抽出文章的标题/URL/来源,作为 LLM 上下文。"""
    issues = core.scan_issues(OUT_DIR)
    target = next((i for i in issues if i["key"] == issue_key), None)
    if not target:
        return []
    issue_dir = OUT_DIR / issue_key
    out = []
    for p in issue_dir.glob("article-*.html"):
        try:
            txt = p.read_text(encoding="utf-8")
            import re as _re
            title_m = _re.search(r"<h1>([^<]+)</h1>", txt)
            src_m = _re.search(r'class="src">([^<]+)</span>', txt)
            url_m = _re.search(r'href="(https?://[^"]+)"', txt)
            out.append({
                "title": title_m.group(1).strip() if title_m else "",
                "source": src_m.group(1).strip() if src_m else "",
                "url": url_m.group(1).strip() if url_m else "",
            })
        except Exception:
            continue
    return out


# ---------- 启动时不阻塞:后台线程做初始抓取 ----------
def _background_refresh_once():
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
