"""
本地命令行入口 — 一行命令渲染当期周报到指定目录。

所有业务逻辑都在 core.py,这里只是 CLI 包装。
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from export_pdf import export_pdf  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data" / "output",
                    help="输出根目录(默认 data/output)")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--articles-json", type=Path, default=None,
                    help="离线模式:从已有 articles.json 渲染(不联网)")
    args = ap.parse_args()

    if args.articles_json:
        # 离线渲染 — 调试 / 演示用
        raw = json.loads(args.articles_json.read_text(encoding="utf-8"))
        articles = [core.build_article(r) for r in raw]
        key = core.issue_key()
        week = core.week_label()
        number = dt.date.today().isocalendar()[1]
        title = core.issue_title(articles)
        issue_dir = args.out / key
        core.render_issue_to_dir(articles, issue_dir, week, title, number)
        issues = core.scan_issues(args.out)
        core.write_index_landing(args.out, issues)
        print(f"OK offline · {key} · {len(articles)} articles · {issue_dir}")
        return 0

    config = {}
    if args.config:
        config = json.loads(args.config.read_text(encoding="utf-8"))

    result = core.full_refresh(args.out, config=config, max_articles=args.limit)
    if not result.get("ok"):
        print("Refresh failed:", result)
        return 2

    if args.pdf:
        try:
            issue_dir = Path(result["issue_dir"])
            export_pdf(issue_dir / "index.html", issue_dir / "issue.pdf")
        except Exception as e:
            print("PDF skipped:", e)

    print(f"OK · {result['issue_key']} · {result['count']} articles · {result['issue_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())