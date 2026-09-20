"""
CEFR B1/B2/C1 难度评估
策略:
  1. 词级覆盖: 基于真实词表(高考 3500 + 四六级,含屈折展开)的覆盖率
     - coverage_hs: 高考词表覆盖率 → 越高越 B1
     - coverage_all: 高考+四六级覆盖率 → 越高越 B2
     - rare_ratio: 超出四六级词表的比例 → 越高越 C1
  2. 平均句长: C1 句子普遍更长
综合打分后映射到 B1 / B2 / C1。

⚠️ 词表是常用教学清单的近似,适合高中进阶参考,不能替代正式 CEFR 测评。
"""
from __future__ import annotations
import os
import re
import csv
import logging
from pathlib import Path

from pipeline.highlight import _load_whitelist

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "cefr_vocab"

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")
_SENT_RE = re.compile(r"[.!?]+\s")

# 真实词表(带屈折/派生展开):
_VOCAB_HS: set[str] = _load_whitelist(("highschool_whitelist.txt",))
_VOCAB_ALL: set[str] = _load_whitelist()   # 高考 + 四六级

# 兼容旧接口
VOCAB: dict[str, set[str]] = {
    "b1": _VOCAB_HS,
    "b2": _VOCAB_ALL,
    "c1": _VOCAB_ALL,
}


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

    cov_b1 = cov(_VOCAB_HS)     # 高考词覆盖
    cov_b2 = cov(_VOCAB_ALL)    # 高考+四六级覆盖
    cov_c1 = max(0.0, 1.0 - cov_b2)  # 超纲比例(兼容旧 stats 字段)

    # 不在 高考+四六级 词表里的词视为"超纲生词"
    rare = sum(1 for w in tokens if w not in _VOCAB_ALL)
    rare_ratio = rare / total

    # 综合分数(真实词表口径):
    # - 高考词覆盖率高、全词表覆盖率高 → 简单(B1)
    # - 四六级覆盖住大部分、高考覆盖一般 → B2
    # - 超纲比例高 / 句长长 → C1
    score = 50
    score += (cov_b2 - 0.85) * 60         # 全词表覆盖 ±9
    score += (cov_b1 - 0.60) * 50         # 高考词覆盖 ±20
    score += (avg_sent_len - 18) * 1.2    # 句长影响
    score += (rare_ratio - 0.10) * 60     # 超纲生词比例
    score = max(0, min(100, score))

    # 判级规则(按真实语料干净正文分组校准):
    #   B1: 高考词覆盖 >= 0.72 且平均句长 <= 16   (News in Levels / Breaking News English)
    #   C1: 超纲比例 >= 0.26 或平均句长 >= 24     (Nature 等学术源)
    #   其余 → B2
    if cov_b1 >= 0.72 and avg_sent_len <= 16:
        level = "B1"
    elif rare_ratio >= 0.26 or avg_sent_len >= 24:
        level = "C1"
    else:
        level = "B2"

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