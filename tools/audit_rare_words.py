"""
超纲词清单审计 —— 只读,不联网,不动任何页面。

已渲染的页面是"渲染那一刻"的产物,里面 `<details class="rare-words">` 的
data-word 按钮就是当时烤进去的 rare_words。本脚本把它们全部读出来,连同每页
自己的正文,按**当前**白名单 + 当前分词重筛一遍(core.classify_page_rare_words),
给出"现在还会标哪些词"的真实清单,供挑选加白。

三桶输出:
  1. 候选清单 —— 仍会被判为超纲的词(按出现篇数降序);
  2. 已加白 —— 曾被标、当前白名单已覆盖的词;
  3. 非词残留 —— 正文里只以非词形态出现的词(URL 片段、被截断的撇号)。

用法:
    python tools/audit_rare_words.py
    python tools/audit_rare_words.py --out data/rare_words_audit.txt
    python tools/audit_rare_words.py --json data/rare_words_audit.json
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from pipeline.highlight import WHITELIST  # noqa: E402

_RARE_BLOCK_RE = re.compile(r'<details class="rare-words">.*?</details>', re.S)
_DATA_WORD_RE = re.compile(r'data-word="([^"]*)"')


def _page_rare_words(txt: str) -> list[str]:
    block = _RARE_BLOCK_RE.search(txt)
    if not block:
        return []
    return [html.unescape(w) for w in _DATA_WORD_RE.findall(block.group(0))]


def _occurrences(word: str, body_lower: str) -> int:
    if not body_lower:
        return 0
    pat = rf"(?<![A-Za-z]){re.escape(word.lower())}(?![A-Za-z])"
    return len(re.findall(pat, body_lower))


def audit(out_dir: Path) -> dict:
    pages = sorted(p for p in out_dir.glob("*/article-*.html"))
    docs: collections.Counter = collections.Counter()       # 出现篇数
    total: collections.Counter = collections.Counter()      # 正文出现次数
    whitelisted: collections.Counter = collections.Counter()
    nontoken: collections.Counter = collections.Counter()
    pages_with_block = 0
    corpus_cache: dict = {}

    for page in pages:
        try:
            txt = page.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        words = _page_rare_words(txt)
        if not words:
            continue
        pages_with_block += 1
        body = core.page_body_text(txt)
        corpus = core.page_corpus_text(page, corpus_cache)
        body_lower = (body + "\n" + corpus).lower()
        keep, now_white, now_nontoken = core.classify_page_rare_words(words, body, corpus)
        for w in dict.fromkeys(keep):
            docs[w] += 1
            total[w] += _occurrences(w, body_lower) or 1
        for w in dict.fromkeys(now_white):
            whitelisted[w] += 1
        for w in dict.fromkeys(now_nontoken):
            nontoken[w] += 1

    ranked = sorted(total, key=lambda w: (-docs[w], -total[w], w))
    return {
        "pages": len(pages),
        "pages_with_block": pages_with_block,
        "whitelist_size": len(WHITELIST),
        "candidates": [{"word": w, "articles": docs[w], "occurrences": total[w]} for w in ranked],
        "whitelisted": [{"word": w, "articles": c} for w, c in whitelisted.most_common()],
        "nontoken": [{"word": w, "articles": c} for w, c in nontoken.most_common()],
    }


def _print_report(res: dict, top_n: int) -> None:
    cand = res["candidates"]
    print(f"扫描 {res['pages']} 个正文页(其中 {res['pages_with_block']} 个带生词块)"
          f",当前白名单 {res['whitelist_size']} 个词形。")
    print(f"\n== 候选清单:仍会被标为超纲的词（{len(cand)} 个，按出现篇数降序）==")
    for row in cand[:top_n]:
        print(f"{row['articles']:4d} 篇  {row['occurrences']:5d} 次  {row['word']}")
    if len(cand) > top_n:
        print(f"... 其余 {len(cand) - top_n} 个见输出文件")

    white = res["whitelisted"]
    print(f"\n== 已加白:曾被标、当前白名单已覆盖（{len(white)} 个）==")
    if white:
        print("   " + "  ".join(f"{r['word']}({r['articles']})" for r in white[:24])
              + (" ..." if len(white) > 24 else ""))
    else:
        print("   （无）")

    nt = res["nontoken"]
    print(f"\n== 非词残留:只出现在 URL / 被截断的撇号里（{len(nt)} 个）==")
    if nt:
        print("   " + "  ".join(f"{r['word']}({r['articles']})" for r in nt))
    else:
        print("   （无）")


def main() -> int:
    ap = argparse.ArgumentParser(description="审计已渲染页面里仍被判为超纲的词")
    ap.add_argument("--dir", default=str(ROOT / "data" / "output"),
                    help="渲染产物目录（默认 data/output）")
    ap.add_argument("--out", default=None, help="候选清单按行写到该文件（便于圈选）")
    ap.add_argument("--json", dest="json_out", default=None, help="完整结果写到该 JSON 文件")
    ap.add_argument("--top", type=int, default=200, help="控制台最多显示多少个候选词")
    args = ap.parse_args()

    out_dir = Path(args.dir).expanduser().resolve()
    if not out_dir.is_dir():
        print(f"目录不存在: {out_dir}", file=sys.stderr)
        return 1

    res = audit(out_dir)
    _print_report(res, args.top)

    if args.out:
        Path(args.out).write_text(
            "\n".join(r["word"] for r in res["candidates"]) + "\n", encoding="utf-8")
        print(f"\n候选清单已写入 {args.out}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"完整结果已写入 {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
