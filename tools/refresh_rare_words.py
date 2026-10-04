"""
就地重筛已渲染页面的生词块 —— 手动入口。

改了 data/cefr_vocab/* 白名单之后跑这个,老期页面里已加白的词会被摘掉
(不重渲染正文、不联网)。服务端启动时也会自动跑一次,这里只是给本地/运维
一个能看 dry-run 的手动入口。

用法:
    python tools/refresh_rare_words.py --dry-run
    python tools/refresh_rare_words.py
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="按当前白名单就地重筛页面生词块")
    ap.add_argument("--dir", default=str(ROOT / "data" / "output"),
                    help="渲染产物目录（默认 data/output）")
    ap.add_argument("--dry-run", action="store_true", help="只统计会改多少页，不落盘")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out_dir = Path(args.dir).expanduser().resolve()
    n = core.refresh_rare_words(out_dir, dry_run=args.dry_run)
    if args.dry_run:
        print(f"[dry-run] {n} 个页面会被重写")
    else:
        print(f"已重写 {n} 个页面")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
