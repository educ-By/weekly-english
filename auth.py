"""
鉴权层 — 邮箱 + 密码 + JWT
- 密码用 bcrypt 散列
- 登录返回 JWT(token 放在 Authorization: Bearer)
- 提供 FastAPI 依赖 get_current_user()

依赖:
  pip install "passlib[bcrypt]" "python-jose[cryptography]" email-validator
"""
from __future__ import annotations
import os
import datetime as dt
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from db import User, get_db


# ---------- 配置 ----------
JWT_SECRET = os.environ.get("JWT_SECRET") or "dev-only-secret-change-in-prod"
JWT_ALG = "HS256"
JWT_TTL_HOURS = 24 * 30      # 30 天有效期

# Render 部署时务必设置 JWT_SECRET(任意长字符串),否则重启会失效
if JWT_SECRET == "dev-only-secret-change-in-prod":
    pass  # 留给启动期提示


# ---------- 密码(直接用 bcrypt,避免 passlib 与新 bcrypt 版本不兼容) ----------
def _truncate(plain: str) -> bytes:
    # bcrypt 硬性限制 72 字节 — 超长密码截断而非报错
    return plain.encode("utf-8")[:72]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_truncate(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_truncate(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def create_access_token(user_id: int) -> str:
    expire = dt.datetime.utcnow() + dt.timedelta(hours=JWT_TTL_HOURS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        sub = payload.get("sub")
        return int(sub) if sub else None
    except (JWTError, ValueError, TypeError):
        return None


# ---------- Pydantic schemas ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str  # 至少 8 位,前端 + 后端均校验


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: int
    email: EmailStr
    created_at: dt.datetime

    class Config:
        from_attributes = True


# ---------- 依赖 ----------
def get_current_user(token: Optional[str] = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)) -> Optional[User]:
    if not token:
        return None
    uid = decode_token(token)
    if not uid:
        return None
    return db.query(User).get(uid)


def require_user(user: Optional[User] = Depends(get_current_user)) -> User:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


TokenOut.model_rebuild()