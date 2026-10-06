"""
数据库层 — SQLAlchemy 2.x 风格

支持:
  - 本地 SQLite(开发,无 DATABASE_URL 时)
  - Postgres / Neon(部署,设 DATABASE_URL=postgresql+psycopg://...)

模型:
  - User          邮箱+密码用户
  - ReadingHistory  阅读记录(哪期/哪篇/何时打开)
  - ReadingProgress 阅读时长与完成度(秒 + 百分比)
  - VocabEntry    用户生词本
"""
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, inspect, text,
    Float, ForeignKey, UniqueConstraint, Index, Text, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

import logging
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DEFAULT_SQLITE = f"sqlite:///{ROOT / 'data' / 'app.db'}"

Base = declarative_base()


# ---------- 模型 ----------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    # 昵称 = 显示名。邮箱仍是身份,昵称只影响页面上怎么称呼你;
    # 留空则各处回退显示邮箱。
    display_name = Column(String(24), nullable=False, default="",
                          server_default="")

    history = relationship("ReadingHistory", back_populates="user",
                           cascade="all, delete-orphan")
    progress = relationship("ReadingProgress", back_populates="user",
                            cascade="all, delete-orphan")
    vocab = relationship("VocabEntry", back_populates="user",
                         cascade="all, delete-orphan")


class ReadingHistory(Base):
    __tablename__ = "reading_history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    issue_key = Column(String(32), nullable=False)
    article_id = Column(String(64), nullable=False)
    title = Column(String(512), default="")
    opened_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="history")

    __table_args__ = (
        Index("ix_history_user_article", "user_id", "issue_key", "article_id"),
    )


class ReadingProgress(Base):
    __tablename__ = "reading_progress"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    issue_key = Column(String(32), nullable=False)
    article_id = Column(String(64), nullable=False)
    seconds_read = Column(Integer, default=0)
    scroll_pct = Column(Float, default=0.0)        # 0~100
    completed = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    user = relationship("User", back_populates="progress")

    __table_args__ = (
        UniqueConstraint("user_id", "issue_key", "article_id",
                         name="uq_progress_one"),
    )


class VocabEntry(Base):
    __tablename__ = "vocab_entries"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    word = Column(String(128), nullable=False)
    sentence = Column(Text, default="")
    source_issue = Column(String(32), default="")
    source_article = Column(String(64), default="")
    definition = Column(Text, default="")
    translation = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="vocab")

    __table_args__ = (
        UniqueConstraint("user_id", "word", "source_issue", "source_article",
                         name="uq_vocab_one"),
    )


class WordGloss(Base):
    """共享单词释义缓存 — 一个用户查过,所有用户复用(省 token)。"""
    __tablename__ = "word_gloss"
    id = Column(Integer, primary_key=True)
    cache_key = Column(String(220), nullable=False, unique=True)  # word|ctx指纹
    word = Column(String(128), nullable=False, index=True)
    translation = Column(Text, default="")
    phonetic = Column(String(64), default="")
    model = Column(String(64), default="")
    hits = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TokenUsage(Base):
    """每月 AI token 用量(按 provider 分计)。"""
    __tablename__ = "token_usage"
    id = Column(Integer, primary_key=True)
    provider = Column(String(32), nullable=False)   # glm / deepseek
    month = Column(String(7), nullable=False)       # 2026-09
    tokens = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("provider", "month", name="uq_usage_month"),)


class ArticleSummary(Base):
    """卡片中文简介的预生成缓存 — 键是文章 id(=sha1(url)[:16],跨天稳定)。

    周一晚把候选文章的简介用 DeepSeek 批量接口(半价)预生成好,周二早上正式
    更新时按 id 直取,不再等 8 线程的 AI 往返。周一抓到而周二没进刊的文章会
    白存几条,但同一篇文章早晚会被收录,命中率只会越滚越高。
    """
    __tablename__ = "article_summary"
    key = Column(String(32), primary_key=True)     # 文章 id = sha1(url)[:16]
    title = Column(String(512), default="")
    summary = Column(Text, default="")
    model = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class IeltsCardProgress(Base):
    """雅思闪卡自评进度 — 连续认识 3 次(box=3)即视为已掌握。
    前端把掌握的卡移出默认复习池,但用户仍可翻全表。"""
    __tablename__ = "ielts_card_progress"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    word = Column(String(128), nullable=False, index=True)
    box = Column(Integer, default=0)            # 0~3:连续认识次数
    known = Column(Boolean, default=False)      # 最近一次自评
    last_seen = Column(DateTime, default=datetime.utcnow,
                       onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "word", name="uq_ielts_card_one"),
    )


def ielts_progress_get(user_id: int, words: list[str]) -> dict[str, dict]:
    """一批词的闪卡进度(word → {box, known})。"""
    if not words:
        return {}
    with get_session_local()() as ses:
        rows = (ses.query(IeltsCardProgress)
                .filter(IeltsCardProgress.user_id == user_id,
                        IeltsCardProgress.word.in_(words)).all())
        return {r.word: {"box": r.box or 0, "known": bool(r.known)}
                for r in rows}


def ielts_progress_set(user_id: int, word: str, known: bool) -> dict:
    """记录一次自评:认识则 box+1(封顶 3),不认识则清零。返回最新状态。"""
    with get_session_local()() as ses:
        row = ses.query(IeltsCardProgress).filter_by(
            user_id=user_id, word=word).first()
        if not row:
            row = IeltsCardProgress(user_id=user_id, word=word)
            ses.add(row)
        row.box = min(3, (row.box or 0) + 1) if known else 0
        row.known = bool(known)
        ses.commit()
        return {"word": word, "box": row.box, "known": bool(row.known)}


def summaries_existing(keys: list[str]) -> set[str]:
    """返回 keys 里已有简介的那些(批量提交前过滤,幂等)。"""
    if not keys:
        return set()
    with get_session_local()() as ses:
        rows = ses.query(ArticleSummary.key).filter(ArticleSummary.key.in_(keys)).all()
        return {r[0] for r in rows}


def save_summary(key: str, title: str, summary: str, model: str = "") -> None:
    """存一条预生成简介(已存在则忽略 —— 旧的不动,省一次写)。"""
    if not key or not summary:
        return
    with get_session_local()() as ses:
        if ses.get(ArticleSummary, key):
            return
        ses.add(ArticleSummary(key=key, title=(title or "")[:512],
                               summary=summary, model=model))
        ses.commit()


def get_summaries(keys: list[str]) -> dict[str, str]:
    """按文章 id 批量取预生成简介。"""
    if not keys:
        return {}
    with get_session_local()() as ses:
        rows = (ses.query(ArticleSummary.key, ArticleSummary.summary)
                .filter(ArticleSummary.key.in_(keys)).all())
        return {k: s for k, s in rows if s}


def add_usage(provider: str, tokens: int):
    if not tokens:
        return
    month = datetime.utcnow().strftime("%Y-%m")
    # 单条 upsert 原子累加:读-改-写两步在并发提问下会互相覆盖丢计数。
    # ON CONFLICT 语法 sqlite 3.24+ 与 postgres 都支持。
    with get_session_local()() as ses:
        ses.execute(text(
            "INSERT INTO token_usage (provider, month, tokens) "
            "VALUES (:p, :m, :t) "
            "ON CONFLICT (provider, month) "
            "DO UPDATE SET tokens = token_usage.tokens + :t2"
        ), {"p": provider, "m": month, "t": int(tokens), "t2": int(tokens)})
        ses.commit()


def get_month_usage(provider: str) -> int:
    month = datetime.utcnow().strftime("%Y-%m")
    with get_session_local()() as ses:
        row = ses.query(TokenUsage).filter_by(provider=provider, month=month).first()
        return (row.tokens or 0) if row else 0


def get_shared_gloss(cache_key: str):
    """命中返回 dict,未命中返回 None;命中时 hits+1(尽力而为,失败不影响释义返回)。"""
    with get_session_local()() as ses:
        row = ses.query(WordGloss).filter_by(cache_key=cache_key).first()
        if not row:
            return None
        try:
            row.hits = (row.hits or 0) + 1
            ses.commit()
        except Exception as e:
            # hits 只是统计;sqlite 写锁争用时放弃这次计数,别把查词挡住
            log.debug("gloss hits increment failed: %s", e)
            ses.rollback()
        return {"ok": True, "word": row.word, "translation": row.translation or "",
                "phonetic": row.phonetic or "", "definition_en": "",
                "examples": [], "cefr_level": "", "model": row.model or "shared"}


def save_shared_gloss(cache_key: str, word: str, info: dict, model: str = ""):
    """存/更新共享释义(仅缓存成功的查询)。"""
    if not info or not info.get("ok"):
        return
    with get_session_local()() as ses:
        row = ses.query(WordGloss).filter_by(cache_key=cache_key).first()
        if row:
            row.translation = info.get("translation") or row.translation
            row.phonetic = info.get("phonetic") or row.phonetic
        else:
            ses.add(WordGloss(cache_key=cache_key, word=word,
                              translation=info.get("translation") or "",
                              phonetic=info.get("phonetic") or "", model=model))
        ses.commit()


# ---------- 引擎 + Session ----------
_engine = None
_SessionLocal = None


def get_database_url() -> str:
    """优先用 DATABASE_URL,否则本地 SQLite。"""
    url = os.environ.get("DATABASE_URL")
    if url:
        # Neon / Render 通常用 postgres://,SQLAlchemy 2.x 推荐 postgresql+psycopg://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url
    # 本地 SQLite
    db_dir = ROOT / "data"
    db_dir.mkdir(exist_ok=True)
    return DEFAULT_SQLITE


def get_engine():
    global _engine
    if _engine is None:
        url = get_database_url()
        connect_args = {}
        if url.startswith("sqlite"):
            # check_same_thread=False:FastAPI 同步路由跑在线程池,需要跨线程复用;
            # timeout:写锁等待上限,降低并发下 "database is locked" 的概率
            connect_args = {"check_same_thread": False, "timeout": 30}
        _engine = create_engine(
            url,
            connect_args=connect_args,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_local():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(),
                                     autoflush=False, autocommit=False,
                                     expire_on_commit=False)
    return _SessionLocal


# create_all 只建"缺的表",绝不会给已有的表补列。所以往老库里加字段必须自己 ALTER,
# 而且得幂等 —— 本地 SQLite 和线上 Neon Postgres 都要照顾到。
_ADDED_COLUMNS: dict[str, str] = {
    "display_name": "ALTER TABLE users ADD COLUMN display_name VARCHAR(24) NOT NULL DEFAULT ''",
}


def _ensure_columns() -> None:
    engine = get_engine()
    try:
        insp = inspect(engine)
        if "users" not in insp.get_table_names():
            return
        existing = {c["name"] for c in insp.get_columns("users")}
        missing = {k: v for k, v in _ADDED_COLUMNS.items() if k not in existing}
        if not missing:
            return
        with engine.begin() as conn:
            for name, ddl in missing.items():
                conn.execute(text(ddl))
                log.info("Schema: added column users.%s", name)
    except Exception as e:
        # 加列失败不该让服务起不来 —— 读取时对缺失字段一律走 .get() 兜底
        log.warning("Schema check for users failed: %s", e)


def init_db():
    Base.metadata.create_all(get_engine())
    _ensure_columns()


def get_db():
    """FastAPI 依赖 — yield 一个 Session。"""
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()