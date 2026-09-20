"""
HTML → PDF 导出。
优先使用 WeasyPrint(高质量 CSS 排版);如系统缺少 GTK 运行时,
退到 playwright(需要安装过 playwright + chromium)→ 浏览器打印。
如果两者都不可用,抛出明确异常,让用户手动用浏览器"打印 → 另存为 PDF"。
"""
from __future__ import annotations
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def export_pdf(html_path: Path, pdf_path: Path) -> str:
    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # 1) WeasyPrint
    try:
        from weasyprint import HTML  # type: ignore
        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        log.info("PDF exported (weasyprint): %s", pdf_path)
        return str(pdf_path)
    except Exception as e:
        log.warning("weasyprint unavailable: %s", e)

    # 2) Playwright
    pw = shutil.which("playwright")
    if pw:
        try:
            script = f"""
import asyncio
from playwright.async_api import async_playwright
async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto('file:///{html_path.as_posix()}')
        await page.emulate_media(media='print')
        await page.pdf(path='{pdf_path.as_posix()}',
                       format='A4', margin={{'top':'18mm','bottom':'18mm','left':'18mm','right':'18mm'}},
                       print_background=True)
        await browser.close()
asyncio.run(run())
"""
            subprocess.run(["python", "-c", script], check=True)
            log.info("PDF exported (playwright): %s", pdf_path)
            return str(pdf_path)
        except Exception as e:
            log.warning("playwright fallback failed: %s", e)

    raise RuntimeError(
        "PDF 导出失败:未安装 weasyprint 或 playwright。\n"
        "请安装其一:\n"
        "  pip install weasyprint     (Windows 还需要 GTK 运行时,见 README)\n"
        "  pip install playwright && playwright install chromium\n"
        "或直接在浏览器中:文件 → 打印 → 另存为 PDF。"
    )