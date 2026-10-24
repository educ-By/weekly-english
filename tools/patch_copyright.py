"""把版权署名/页脚声明打进已渲染的存量 HTML(命令行入口)。

核心逻辑在 core.apply_copyright_patch,启动时的渲染迁移(migrate_rendered_data)
也会自动跑同一份实现 —— 这个工具用于不重启服务就手动补一遍,幂等可重复跑。

用法: python tools/patch_copyright.py [out_dir]   # 默认 data/output
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else core.rendered_dir(ROOT / "data" / "output")

if __name__ == "__main__":
    changed = core.apply_copyright_patch(OUT_DIR)
    core.write_index_landing(OUT_DIR, core.scan_issues(OUT_DIR))
    core._sync_static_copies(OUT_DIR)
    core.refresh_asset_versions(OUT_DIR)
    print(f"patched pages: {changed}; copyright.html -> {OUT_DIR / 'copyright.html'}")
