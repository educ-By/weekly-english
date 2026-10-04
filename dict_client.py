"""
查词引擎 —— 全链路不调用任何 AI。

数据源两级:
  1. 离线 ECDICT(MIT 开源词典, 见 tools/build_ecdict.py):
     data/dict/ecdict.csv.gz → 启动时建成 data/dict/ecdict.sqlite(词→音标/词性/中文释义)
     查不到就按 pipeline.highlight._lemmas() 做词形归一(drones→drone、sought→seek),
     与超纲词判定同一套规则,口径一致。
  2. MyMemory 免费机翻(https://mymemory.translated.net):词典没有的生僻词与词组兜底。
     可用 MYMEMORY_EMAIL 环境变量填邮箱提升免费额度。

调用方(server.main 的 /api/dict)负责共享缓存与响应组装;这里只负责"取词"。
"""
from __future__ import annotations

import csv
import gzip
import html
import json
import logging
import os
import re
import sqlite3
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DICT_DIR = ROOT / "data" / "dict"
GZ_PATH = DICT_DIR / "ecdict.csv.gz"
DB_PATH = DICT_DIR / "ecdict.sqlite"

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MYMEMORY_TIMEOUT = 6.0

# MyMemory 偶尔把带内联标记的段落原样吐回来(traditional characters →
# '<bpt i="1" type="bold">{}</b…'),必须剥掉标签并要求结果里真有中文,否则
# 这种垃圾会被当成释义显示。
_XLIFF_TAG_RE = re.compile(r"</?(?:bpt|ept|ph|it|g|x|bx|ex)\b[^>]*>")
_PLACEHOLDER_RE = re.compile(r"\{\d*\}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_lock = threading.Lock()
_ready = False


# ---------------- 索引 ----------------

def build_index(force: bool = False) -> Optional[Path]:
    """把 ecdict.csv.gz 建成 sqlite(幂等;force 时重建)。gz 不存在返回 None。"""
    if not GZ_PATH.exists():
        log.warning("ecdict gz missing at %s — 离线词典不可用", GZ_PATH)
        return None
    if DB_PATH.exists() and not force:
        return DB_PATH
    tmp = DB_PATH.with_suffix(".sqlite.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(str(tmp))
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("CREATE TABLE entries ("
                    "word TEXT PRIMARY KEY, phonetic TEXT, pos TEXT, "
                    "translation TEXT, frq INTEGER)")
        rows = []
        with gzip.open(GZ_PATH, "rt", encoding="utf-8", newline="") as f:
            for i, row in enumerate(csv.DictReader(f)):
                if i % 20000 == 0 and i:
                    con.executemany("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?)", rows)
                    rows.clear()
                try:
                    frq = int(row.get("frq") or 0)
                except ValueError:
                    frq = 0
                rows.append((row["word"], row.get("phonetic") or "",
                             row.get("pos") or "", row.get("translation") or "", frq))
        if rows:
            con.executemany("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?)", rows)
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    finally:
        con.close()
    tmp.replace(DB_PATH)
    log.info("ECDICT index built: %s (%s entries, %.1f MB)",
             DB_PATH, f"{n:,}", DB_PATH.stat().st_size / 1048576)
    return DB_PATH


def ensure_ready(force: bool = False) -> bool:
    """确保索引可用(首次调用会建一次)。返回是否可用。"""
    global _ready
    if _ready and not force:
        return True
    with _lock:
        if _ready and not force:
            return True
        _ready = build_index(force=force) is not None
        return _ready


def _candidates(word: str) -> list[str]:
    """查词候选:原词优先,再按 highlight 的词形归一规则补原型。"""
    from pipeline.highlight import _lemmas
    w = word.strip().lower()
    out = [w]
    try:
        for c in _lemmas(w):
            if c and c not in out:
                out.append(c)
    except Exception:                      # 归一失败也不该挡住查词
        pass
    return out


def _tidy_zh(text: str) -> str:
    """ECDICT 释义压成一行,弹窗里读起来清爽些。

    注意它家的换行是**字面量** "\n"(反斜杠+n),不是真的换行符,两种都要切。
    """
    parts = re.split(r"\\n|\r?\n", text or "")
    out = [re.sub(r"\s+", " ", p).strip() for p in parts]
    return "; ".join(p for p in out if p).strip("; ").strip()


def lookup_local(word: str) -> Optional[dict]:
    """离线词典查询(含词形归一)。多个候选命中时取最常见的那条。"""
    if not ensure_ready():
        return None
    cands = _candidates(word)
    ph = ",".join("?" * len(cands))
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error as e:
        log.warning("ecdict open failed: %s", e)
        return None
    try:
        rows = con.execute(
            f"SELECT word, phonetic, pos, translation, frq FROM entries WHERE word IN ({ph})",
            cands).fetchall()
    except sqlite3.Error as e:
        log.warning("ecdict query failed: %s", e)
        return None
    finally:
        con.close()
    if not rows:
        return None
    exact = [r for r in rows if r[0] == cands[0]]
    pool = exact or rows
    # frq=0 表示不在 COCA(生僻);优先取有条目编号的,编号小的更常见
    pool.sort(key=lambda r: (0, r[4]) if r[4] else (1, 0))
    w, phonetic, pos, translation, frq = pool[0]
    return {"word": w, "phonetic": phonetic, "pos": pos,
            "translation": _tidy_zh(translation), "frq": frq,
            "lemmatized": w != cands[0]}


# ---------------- 机翻兜底 ----------------

def translate_fallback(text: str) -> Optional[str]:
    """MyMemory 免费机翻(免 key)。失败返回 None,绝不抛。"""
    q = {"q": text, "langpair": "en|zh-CN"}
    email = os.environ.get("MYMEMORY_EMAIL")
    if email:
        q["de"] = email
    url = MYMEMORY_URL + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "weekly-english/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=MYMEMORY_TIMEOUT) as resp:
            data = json.load(resp)
    except Exception as e:
        log.info("mymemory failed for %r: %s", text, e)
        return None
    try:
        status = int(data.get("responseStatus") or 0)
    except (TypeError, ValueError):
        status = 0
    zh = html.unescape(str((data.get("responseData") or {}).get("translatedText") or "")).strip()
    if status != 200 or not zh:
        log.info("mymemory empty/status=%s for %r", status, text)
        return None
    zh = _PLACEHOLDER_RE.sub("", _XLIFF_TAG_RE.sub("", zh)).strip()
    if not _CJK_RE.search(zh):                  # 没中文 = 没翻出来(含标记残留)
        log.info("mymemory non-chinese result for %r: %r", text, zh[:60])
        return None
    upper = zh.upper()
    if "MYMEMORY WARNING" in upper or "QUERY LENGTH LIMIT" in upper or "INVALID" in upper:
        return None
    if zh.lower() == text.strip().lower():      # 回显原文 = 没翻出来
        return None
    return zh


# ---------------- 统一入口 ----------------

def lookup(word: str) -> dict:
    """查词:离线词典 → MyMemory。返回结构让调用方直接包成 /api/dict 响应。"""
    w = (word or "").strip()
    if not w:
        return {"ok": False, "word": word, "translation": ""}
    hit = lookup_local(w)
    if hit:
        return {
            "ok": True, "word": w, "phonetic": hit["phonetic"],
            "pos": hit["pos"], "translation": hit["translation"],
            "definition_en": "", "examples": [], "cefr_level": "",
            "model": "ecdict",
        }
    zh = translate_fallback(w)
    if zh:
        return {
            "ok": True, "word": w, "phonetic": "", "pos": "",
            "translation": zh, "definition_en": "", "examples": [],
            "cefr_level": "", "model": "mymemory",
        }
    return {"ok": False, "word": w, "translation": ""}
