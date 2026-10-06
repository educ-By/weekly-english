"""
雅思核心词表 —— /ielts 专区用。

数据来源: data/cefr_vocab/ 下 academic/b2/c1 三份 csv 去重合并(刻意不碰
pipeline.vocab_profiles 的加载集合,否则会改变全站超纲词高亮的判定)。
每个词的音标/词性/中文释义查离线 ECDICT(dict_client.lookup_local,不调用 AI)。

产物缓存到 data/ielts_words.json,键里带词表 csv 的 mtime 指纹 —— csv 没变就
直接读缓存,ECDICT 索引缺失时优雅降级(词条没有释义也照样能出列表)。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

import dict_client

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
VOCAB_DIR = ROOT / "data" / "cefr_vocab"
CACHE_PATH = ROOT / "data" / "ielts_words.json"

# 三个来源文件的注释说明了各自定位,这里保持与计划一致
_SOURCES = ["academic.csv", "b2.csv", "c1.csv"]
_WORDS_PER_PAGE = 24

_lock = threading.Lock()
_cache: dict = {"fingerprint": None, "words": []}


def _load_word_set() -> list[str]:
    words: set[str] = set()
    for name in _SOURCES:
        path = VOCAB_DIR / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            log.warning("ielts vocab source missing: %s", path)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for w in line.split(","):
                w = w.strip().lower()
                if re.fullmatch(r"[a-z][a-z'-]*", w):
                    words.add(w)
    return sorted(words)


def _fingerprint() -> str:
    parts = []
    for name in _SOURCES:
        try:
            parts.append(f"{name}:{(VOCAB_DIR / name).stat().st_mtime_ns}")
        except OSError:
            parts.append(f"{name}:missing")
    return "|".join(parts)


def _build(word_list: list[str]) -> list[dict]:
    """逐词查 ECDICT。首次构建约几千词 × sqlite 查询,秒级完成。"""
    out = []
    for i, w in enumerate(word_list):
        info = dict_client.lookup_local(w) or {}
        out.append({
            "word": w,
            "phonetic": info.get("phonetic") or "",
            "pos": info.get("pos") or "",
            "translation": info.get("translation") or "",
        })
        if i % 500 == 0 and i:
            log.info("ielts vocab glossed %d/%d", i, len(word_list))
    return out


def ensure_words() -> list[dict]:
    """词表(带释义),进程内 + 磁盘双层缓存。"""
    fp = _fingerprint()
    with _lock:
        if _cache["fingerprint"] == fp and _cache["words"]:
            return _cache["words"]

    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if data.get("fingerprint") == fp and isinstance(data.get("words"), list):
                _cache["fingerprint"] = fp
                _cache["words"] = data["words"]
                return _cache["words"]
        except Exception as e:
            log.warning("ielts vocab cache unreadable: %s", e)

    word_list = _load_word_set()
    words = _build(word_list)
    _cache["fingerprint"] = fp
    _cache["words"] = words
    try:
        CACHE_PATH.write_text(
            json.dumps({"fingerprint": fp, "words": words}, ensure_ascii=False),
            encoding="utf-8")
        log.info("ielts vocab built: %d words → %s", len(words), CACHE_PATH.name)
    except OSError as e:
        log.warning("ielts vocab cache write failed: %s", e)
    return words


def query(q: str = "", page: int = 1) -> dict:
    """搜索 + 分页。q 匹配单词前缀/包含(前缀命中排前)。"""
    words = ensure_words()
    needle = (q or "").strip().lower()
    if needle:
        starts = [w for w in words if w["word"].startswith(needle)]
        contains = [w for w in words
                    if needle in w["word"] and not w["word"].startswith(needle)]
        matched = starts + contains
    else:
        matched = words
    total = len(matched)
    pages = max(1, (total + _WORDS_PER_PAGE - 1) // _WORDS_PER_PAGE)
    page = max(1, min(page, pages))
    slice_ = matched[(page - 1) * _WORDS_PER_PAGE: page * _WORDS_PER_PAGE]
    return {"q": needle, "pageno": page, "pages": pages, "total": total,
            "items": slice_}
