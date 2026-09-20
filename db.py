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
    create_engine, Column, Integer, String, DateTime,
    Float, ForeignKey, UniqueConstraint, Index, Text, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.pool import StaticPool

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


def add_usage(provider: str, tokens: int):
    if not tokens:
        return
    month = datetime.utcnow().strftime("%Y-%m")
    with get_session_local()() as ses:
        row = ses.query(TokenUsage).filter_by(provider=provider, month=month).first()
        if row:
            row.tokens = (row.tokens or 0) + tokens
        else:
            ses.add(TokenUsage(provider=provider, month=month, tokens=tokens))
        ses.commit()


def get_month_usage(provider: str) -> int:
    month = datetime.utcnow().strftime("%Y-%m")
    with get_session_local()() as ses:
        row = ses.query(TokenUsage).filter_by(provider=provider, month=month).first()
        return (row.tokens or 0) if row else 0


def get_shared_gloss(cache_key: str):
    """命中返回 dict,未命中返回 None;命中时 hits+1。"""
    with get_session_local()() as ses:
        row = ses.query(WordGloss).filter_by(cache_key=cache_key).first()
        if not row:
            return None
        row.hits = (row.hits or 0) + 1
        ses.commit()
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
            connect_args = {"check_same_thread": False}
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


def init_db():
    Base.metadata.create_all(get_engine())


def get_db():
    """FastAPI 依赖 — yield 一个 Session。"""
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()