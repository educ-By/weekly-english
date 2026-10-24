"""把版权署名/页脚声明一次性打进已渲染的旧期 HTML。

旧期没有存原始 JSON,无法重渲染,只能就地对既有页面做字符串级补丁:
  1. 页脚 About 段 "Originals preserved verbatim; ..." → 明确的版权声明
  2. article-*.html 在 stats 段前插入来源署名(优先取 .src 链接里的原文 URL)
  3. 重新生成根 copyright.html,并刷 asset 版本号 / 各期 static 副本

模板已改的部分只影响之后渲染的期;这里负责存量。幂等,可重复跑。

用法: python tools/patch_copyright.py [out_dir]   # 默认 data/output
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "output"

OLD_ABOUT = (
    "Hand-picked English reading, graded B1–C1. Originals preserved verbatim; "
    "out-of-scope words are marked against the 3,500-word gaokao list and its inflections."
)
NEW_ABOUT = (
    "Hand-picked English reading, graded B1–C1. Articles are reproduced from their "
    "original publishers for non-commercial educational use only; copyright belongs "
    "to the original authors and publishers. Out-of-scope words are marked against "
    "the 3,500-word gaokao list and its inflections."
)

# 存量文章页:来源在 entry-meta 里,新模板是 <a class="src" href="URL">,更老的是纯 span
SRC_A_RE = re.compile(r'<a class="src" href="([^"]+)"[^>]*>([^<]*)</a>')
SRC_SPAN_RE = re.compile(r'<span class="src">([^<]*)</span>')

ATTR_WITH_URL = (
    '<p class="src-attribution">Originally published at '
    '<a href="{url}" target="_blank" rel="noopener noreferrer">{source}</a>. '
    "Reproduced for non-commercial educational use only; all rights belong to the "
    "original author and publisher.</p>"
)
ATTR_NO_URL = (
    '<p class="src-attribution">Originally published in {source}. '
    "Reproduced for non-commercial educational use only; all rights belong to the "
    "original author and publisher.</p>"
)


ATTR_P_RE = re.compile(r'<p class="src-attribution">.*?</p>', re.S)


def inject_footer_link(html: str) -> str:
    """在 <footer> 块的 Browse 列 Archive 链接后面补一条 Copyright & takedown(幂等)。"""
    i = html.find('<footer class="foot">')
    if i < 0 or 'href="/copyright.html"' in html[i:]:
        return html
    foot = html[i:]
    foot_new = foot.replace(
        '<a href="/archive">Archive</a>',
        '<a href="/archive">Archive</a>\n      <a href="/copyright.html">Copyright &amp; takedown</a>',
        1,
    )
    return html[:i] + foot_new


def patch_article(path: Path) -> bool:
    html = path.read_text(encoding="utf-8")
    orig = html
    # 先剥掉旧跑插入的署名段(可能是占位文案),再按当前规则重插
    html = ATTR_P_RE.sub("", html)
    m = SRC_A_RE.search(html) or SRC_SPAN_RE.search(html)
    if m:
        if m.lastindex == 2:
            attr = ATTR_WITH_URL.format(url=m.group(1), source=m.group(2))
        else:
            attr = ATTR_NO_URL.format(source=m.group(1))
        html = html.replace('<p class="stats">', attr + '<p class="stats">', 1)
    html = html.replace(OLD_ABOUT, NEW_ABOUT)
    html = inject_footer_link(html)
    if html != orig:
        path.write_text(html, encoding="utf-8")
        return True
    return False


def main() -> None:
    if not OUT_DIR.exists():
        raise SystemExit(f"out dir not found: {OUT_DIR}")

    changed = 0
    for f in sorted(OUT_DIR.rglob("*.html")):
        if f.parent == OUT_DIR and f.name in {"index.html", "archive.html", "copyright.html"}:
            # 根目录页由 write_index_landing 统一重渲染,这里只补旧期目录
            continue
        if f.name.startswith("article-"):
            changed += patch_article(f)
        else:
            html = f.read_text(encoding="utf-8")
            new = inject_footer_link(html.replace(OLD_ABOUT, NEW_ABOUT))
            if new != html:
                f.write_text(new, encoding="utf-8")
                changed += 1

    core.write_index_landing(OUT_DIR, core.scan_issues(OUT_DIR))
    core._sync_static_copies(OUT_DIR)
    core.refresh_asset_versions(OUT_DIR)

    print(f"patched article pages: {changed}; "
          f"copyright.html -> {OUT_DIR / 'copyright.html'}")


if __name__ == "__main__":
    main()
