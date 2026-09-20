"""
CEFR B1/B2/C1 难度评估
策略:
  1. 词级覆盖: 文本中词与各级词表的重合率
  2. 平均句长: C1 句子普遍更长
  3. 低频词比例: 总词数中非 B2 词表词的比例
综合打分后映射到 B1 / B2 / C1。

⚠️ 词表是常用教学清单的近似,适合高中进阶参考,不能替代正式 CEFR 测评。
替换方式:把自己准备的 CSV 放到 data/cefr_vocab/{b1,b2,c1}.csv,每行一词。
"""
from __future__ import annotations
import os
import re
import csv
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "cefr_vocab"

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")
_SENT_RE = re.compile(r"[.!?]+\s")


def _load_vocab(level: str) -> set[str]:
    path = DATA_DIR / f"{level}.csv"
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


VOCAB: dict[str, set[str]] = {
    lvl: _load_vocab(lvl)
    for lvl in ("b1", "b2", "c1")
}


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _sentence_lengths(text: str) -> list[int]:
    sentences = [s for s in _SENT_RE.split(text) if s.strip()]
    return [len(_tokenize(s)) for s in sentences]


def evaluate(text: str) -> dict:
    """
    返回:
      {
        "level": "B1" | "B2" | "C1",
        "score": 0~100,
        "stats": {total_words, sentences, avg_sent_len,
                  coverage_b1, coverage_b2, coverage_c1,
                  rare_word_ratio},
      }
    """
    tokens = _tokenize(text)
    if not tokens:
        return {"level": "B1", "score": 0, "stats": {}}

    sent_lens = _sentence_lengths(text) or [len(tokens)]
    avg_sent_len = sum(sent_lens) / len(sent_lens)

    total = len(tokens)
    unique = set(tokens)

    def cov(vocab: set[str]) -> float:
        if not vocab or not unique:
            return 0.0
        return sum(1 for w in unique if w in vocab) / len(unique)

    cov_b1 = cov(VOCAB["b1"])
    cov_b2 = cov(VOCAB["b2"])
    cov_c1 = cov(VOCAB["c1"])

    # 不在 B2 词表里的词视为"低频/生词"
    rare = sum(1 for w in tokens if w not in VOCAB["b2"])
    rare_ratio = rare / total

    # 综合分数:
    # - B2 覆盖率越高 → 越接近 B2
    # - 句长 > 22 偏向 C1
    # - 生词比例 > 25% 偏向 C1
    score = 50
    score += (cov_b2 - 0.5) * 60          # ±30
    score += (cov_c1 - 0.2) * 40          # ±16
    score += (avg_sent_len - 18) * 1.2    # 句长影响
    score += (rare_ratio - 0.2) * 60      # 生词比例
    score = max(0, min(100, score))

    if score < 40:
        level = "B1"
    elif score < 70:
        level = "B2"
    else:
        level = "C1"

    return {
        "level": level,
        "score": round(score, 1),
        "stats": {
            "total_words": total,
            "sentences": len(sent_lens),
            "avg_sent_len": round(avg_sent_len, 1),
            "coverage_b1": round(cov_b1, 3),
            "coverage_b2": round(cov_b2, 3),
            "coverage_c1": round(cov_c1, 3),
            "rare_word_ratio": round(rare_ratio, 3),
        },
    }