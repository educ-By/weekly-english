"""
雅思核心词表 —— /ielts 专区用。

数据来源: data/cefr_vocab/ 下 academic/b2/c1 三份 csv 去重合并(刻意不碰
pipeline.vocab_profiles 的加载集合,否则会改变全站超纲词高亮的判定)。
每个词的音标/中文释义查离线 ECDICT(dict_client.lookup_local,不调用 AI)。

产物缓存到 data/ielts_words.json。指纹里带 SCHEMA 版本号 —— 只认 csv 的 mtime
的话,改了这里的数据结构(比如本次新增 tags)老缓存不会被重建。

词性说明: ECDICT 的 pos 列整列为空,但释义串自带前缀("vt. 放弃…；n. 放任…"),
所以词性从释义里解析,见 _parse_tags()。
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
from pathlib import Path

import dict_client

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
VOCAB_DIR = ROOT / "data" / "cefr_vocab"
CACHE_PATH = ROOT / "data" / "ielts_words.json"

# 缓存数据结构版本 —— 改字段就 +1,老缓存会被重建
SCHEMA = 2

# 三个来源文件的注释说明了各自定位,这里保持与计划一致
_SOURCES = ["academic.csv", "b2.csv", "c1.csv"]
_WORDS_PER_PAGE = 24
DEFAULT_DRILL = 20
MAX_DRILL = 200

_lock = threading.Lock()
_cache: dict = {"fingerprint": None, "words": []}

# 每段释义开头的词性标记,如 "vt." "n.&vt." "a. 抽象的"
_POS_LEAD_RE = re.compile(r"^\s*((?:[a-zA-Z]{1,5}\.[\s&]*)+)")
_POS_ALIAS = {"a": "adj", "ad": "adv"}
# "pl."(复数)、"abbr."(缩写)这类是词形标注而非词性,不进 chips
_POS_SKIP = {"pl", "abbr", "sing"}
_MAX_TAGS = 3


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


def _parse_tags(translation: str) -> list[str]:
    """从 ECDICT 释义串里解析词性,如 "vt. 放弃…；n. 放任…" → ["vt", "n"]。"""
    tags: list[str] = []
    for segment in re.split(r"[;；]", translation or ""):
        seg = segment.strip()
        # 剥掉 "[化] "[经] " 之类的科目前缀,再看段首有没有词性
        seg = re.sub(r"^(?:\[[^\]]{1,4}\]\s*)+", "", seg)
        m = _POS_LEAD_RE.match(seg)
        if not m:
            continue
        for raw in m.group(1).split("."):
            tag = raw.strip().lower()
            if not tag or tag in _POS_SKIP:
                continue
            tag = _POS_ALIAS.get(tag, tag)
            if tag not in tags:
                tags.append(tag)
        if len(tags) >= _MAX_TAGS:
            break
    return tags[:_MAX_TAGS]


def _fingerprint() -> str:
    parts = [f"v{SCHEMA}"]
    for name in _SOURCES:
        try:
            parts.append(f"{name}:{(VOCAB_DIR / name).stat().st_mtime_ns}")
        except OSError:
            parts.append(f"{name}:missing")
    return "|".join(parts)


def _build(word_list: list[str]) -> list[dict]:
    """逐词查 ECDICT。首次构建几千词 × sqlite 查询,秒级完成。"""
    out = []
    for i, w in enumerate(word_list):
        info = dict_client.lookup_local(w) or {}
        translation = info.get("translation") or ""
        out.append({
            "word": w,
            "phonetic": info.get("phonetic") or "",
            "tags": _parse_tags(translation),
            "translation": translation,
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


def _match(words: list[dict], q: str) -> list[dict]:
    """q 匹配单词前缀/包含,前缀命中排前。"""
    needle = (q or "").strip().lower()
    if not needle:
        return words
    starts = [w for w in words if w["word"].startswith(needle)]
    contains = [w for w in words
                if needle in w["word"] and not w["word"].startswith(needle)]
    return starts + contains


def query(q: str = "", page: int = 1) -> dict:
    """搜索 + 分页(词表页用)。"""
    matched = _match(ensure_words(), q)
    total = len(matched)
    pages = max(1, (total + _WORDS_PER_PAGE - 1) // _WORDS_PER_PAGE)
    page = max(1, min(page, pages))
    slice_ = matched[(page - 1) * _WORDS_PER_PAGE: page * _WORDS_PER_PAGE]
    return {"q": (q or "").strip().lower(), "pageno": page, "pages": pages,
            "total": total, "items": slice_}


def sample(q: str = "", limit: int = DEFAULT_DRILL,
           mastered: set[str] | None = None) -> dict:
    """闪卡抽词 —— 从(可选的)未掌握池里随机抽 limit 个。

    随机而不是顺序取:以前恒取词表开头,未登录/零进度用户永远练同 60 个词,
    3255 个词里绝大多数永远出不来。
    """
    limit = max(1, min(int(limit or DEFAULT_DRILL), MAX_DRILL))
    matched = _match(ensure_words(), q)
    pool = [w for w in matched if w["word"] not in (mastered or set())]
    spare = [w for w in matched if w["word"] in (mastered or set())]
    # 未掌握池不够就补已掌握的,尽量凑满这一轮
    picked = random.sample(pool, min(limit, len(pool)))
    if len(picked) < limit:
        picked += random.sample(spare, min(limit - len(picked), len(spare)))
    random.shuffle(picked)
    return {"items": picked, "pool": len(pool), "total": len(matched)}


def wordlist_lines() -> list[str]:
    """纯词表(给文章页查词弹窗判断"是不是雅思核心词"用)。"""
    return [w["word"] for w in ensure_words()]
