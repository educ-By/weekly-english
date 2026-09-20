"""
词表分层:
- HIGH_FREQ: 高中高频考纲词(新课标 3500 / 高考 3500 常用)
- ACADEMIC:  学术高频词(进阶写作常用 — AWL 学术词表近似)
- RARE:      真正的难词/生僻词(超出 B2 词表)
- C1_TERMS:  C1+ 高阶表达(雅思 7+ / 高考冲刺)

前端会用三种颜色区分:
  highfreq: 暖橙     — "这是你要掌握的高频考词"
  academic: 海军蓝   — "这是进阶写作会遇到的学术词"
  rare:     深紫线 — "这是拓展词汇,认识即可"
"""
from __future__ import annotations
from pathlib import Path
import csv
import re

VOCAB_DIR = Path(__file__).resolve().parent.parent / "data" / "cefr_vocab"

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")


def _load(name: str) -> set[str]:
    path = VOCAB_DIR / f"{name}.csv"
    if not path.exists():
        return set()
    words: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row:
                continue
            w = row[0].strip().lower()
            if w and not w.startswith("#"):
                words.add(w)
    return words


HIGH_FREQ = _load("highfreq")    # 高考高频考纲词
ACADEMIC  = _load("academic")    # 学术高频
B1        = _load("b1")
B2        = _load("b2")
C1        = _load("c1")


def classify(token: str) -> str | None:
    """
    返回词性标签: 'highfreq' / 'academic' / 'rare' / None
    优先级:高频 > 学术 > rare > None
    """
    t = token.lower()
    if t in HIGH_FREQ:
        return "highfreq"
    if t in ACADEMIC:
        return "academic"
    if t in B2 or t in B1:
        return None  # 已在基础范围,不标
    if t in C1:
        return "rare"   # C1+ 算生僻
    return "rare"        # 词表外一律生僻


def find_vocab(text: str, limit: int = 40) -> dict[str, str]:
    """
    返回 {word: tag} — 给前端按颜色高亮使用。
    """
    seen: dict[str, str] = {}
    counts: dict[str, int] = {}
    for w in _WORD_RE.findall(text):
        wl = w.lower()
        if len(wl) < 4:
            continue
        if wl in {"mr", "mrs", "ms", "dr", "st", "jr", "sr",
                  "inc", "ltd", "co", "corp", "llc",
                  "http", "https", "www"}:
            continue
        tag = classify(wl)
        if not tag:
            continue
        counts[wl] = counts.get(wl, 0) + 1
        if wl not in seen:
            seen[wl] = tag
    # 按频度排序,限制数量
    items = sorted(seen.items(), key=lambda kv: -counts[kv[0]])[:limit]
    return {w: tag for w, tag in items}