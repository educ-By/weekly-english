"""
有道智云 API 接入骨架 — 当前为预留调用位,不实际调用。

要启用(在 .env 里填):
    YOUDAO_APP_KEY=xxxxx
    YOUDAO_APP_SECRET=xxxxx

启用后,精读页点击超纲词会触发 fetch_youdao_dict(word) 异步取中文释义并弹出。
本模块故意保持最小可插拔设计 — 不在主流程里调用,前端 hover/click 触发。

参考 endpoint:
  词典: https://openapi.youdao.com/api  (path=/dict/json?word=...)
  文本翻译: https://openapi.youdao.com/api  (action=translate)
  TTS: https://openapi.youdao.com/ttsapi  (q=text, voiceName=en_US, format=mp3)

⚠️ 当前为预留位 — 不实际发送请求(避免误扣费/未授权)。
    is_configured() 返回 False 时,所有 *_lookup() 直接返回 None,前端降级到本地释义。
"""
from __future__ import annotations
import hashlib
import logging
import os
import time
import uuid
from typing import Optional

log = logging.getLogger(__name__)


def _env(name: str) -> Optional[str]:
    v = os.environ.get(name) or os.environ.get(name.lower())
    return v.strip() if v else None


def is_configured() -> bool:
    """是否配置了有道 appKey。返回 False 时,_enable_returns_None 且无网络请求。"""
    return bool(_env("YOUDAO_APP_KEY") and _env("YOUDAO_APP_SECRET"))


def _sign(app_key: str, salt: str, app_secret: str, q: str) -> str:
    return hashlib.sha256(f"{app_key}{q}{salt}{app_secret}".encode("utf-8")).hexdigest()


def lookup_dict(word: str) -> Optional[dict]:
    """
    有道词典查询。
    返回示例:
        {
          "word": "intervention",
          "phonetic": "/ˌɪntəˈvenʃn/",
          "translation": "n. 干预;介入;调停",
          "examples": ["military intervention", ...]
        }
    未配置或调用失败返回 None — 前端降级到内置 fallback。
    """
    if not is_configured():
        return None
    try:
        import requests  # noqa
    except Exception:
        return None

    app_key = _env("YOUDAO_APP_KEY")
    app_secret = _env("YOUDAO_APP_SECRET")
    salt = uuid.uuid4().hex
    curtime = str(int(time.time()))
    payload = {
        "q": word,
        "from": "en",
        "to": "zh-CHS",
        "appKey": app_key,
        "salt": salt,
        "sign": _sign(app_key, salt, app_secret, word),
        "signType": "v3",
        "curtime": curtime,
        "vocabId": "false",
    }
    try:
        r = requests.post("https://openapi.youdao.com/api",
                          data=payload, timeout=4)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("youdao dict lookup failed: %s", e)
        return None

    if data.get("errorCode") != "0":
        return None

    basic = (data.get("basic") or {})
    explains = basic.get("explains") or []
    phonetic = basic.get("phonetic") or ""
    examples = [w["text"] for w in (data.get("web") or {}).get("dict", {}).get("list", [])
                      if "text" in w][:3]
    return {
        "word": word,
        "phonetic": phonetic,
        "translation": "；".join(explains) if explains else "",
        "examples": examples,
    }