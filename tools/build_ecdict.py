"""
构建离线词典 —— 从 ECDICT(skywind3000/ECDICT, MIT) 抓全量 csv,过滤后压缩成
data/dict/ecdict.csv.gz,供 dict_client 建 sqlite 索引。

为什么要过滤:ECDICT 全量约 77 万条(含大量词组、专名、生僻形),63MB。本站读者是
B1–C1 的新闻读者,只需要"真词汇 + 有中文释义"的那部分。保留判据(任一命中):
    oxford=1 | collins>0 | tag 非空(zk/gk/cet4/cet6/ky/toefl/ielts/gre) | bnc>0 | frq>0
    或  连字符复合词(≤30 字符,如 self-confidence / hard-coded / zero-sum)

复合词单列一条:它们天然没有频次标记,但新闻里出现得多、读者最常点(self-confidence、
sun-drenched、ex-girlfriend…),ECDICT 都收了且是简体释义,不该扔给机翻。

短语另有第三条判据:**站点语料里真实出现过的 2~4 词短语**(从各期 search.json 抽 n-gram)。
加上这条是为了让"划词选中一个短语"也能本地秒出简体释义 —— MyMemory 机翻虽然也翻得动,
但要等 0.6~2.2 秒,而且偶尔返回繁体。只收语料里出现过的,所以是有界的千余条,不会把
ECDICT 那 36 万条多词条目全搬进来。语料缺失时这条规则自动跳过(全交给 MyMemory)。

用法:
    python tools/build_ecdict.py                 # 下载 → 过滤 → 写 data/dict/ecdict.csv.gz
    python tools/build_ecdict.py --rebuild-index # 顺带建出 ecdict.sqlite
    python tools/build_ecdict.py --csv <本地csv> # 跳过下载,用本地已有 csv
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "dict"
OUT_GZ = OUT_DIR / "ecdict.csv.gz"

# 直连优先(快),失败退回 ghfast 镜像(raw.githubusercontent 在国内常被重置)
SOURCES = [
    "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv",
    "https://ghfast.top/https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv",
]

# 只留"真词汇":允许连字符/撇号/空格(词组),但排除纯符号、带数字、多标点的条目
_KEEP_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'’.\- ]*$")
MAX_WORD_LEN = 40

# 输出列(丢掉 definition/exchange/detail/audio 等大字段,音标+词性+中文释义足够)
FIELDS = ("word", "phonetic", "pos", "translation", "frq")


def _log(msg: str) -> None:
    print(msg, flush=True)


def download(dest: Path) -> Path:
    """带断点续传的下载 —— 链路会被重置,所以按 1MB 分块续,直到拿满或放弃。"""
    import urllib.request

    last_err = None
    for src in SOURCES:
        attempt = 0
        while attempt < 200:
            attempt += 1
            have = dest.stat().st_size if dest.exists() else 0
            req = urllib.request.Request(src, headers={
                "Range": f"bytes={have}-",
                "User-Agent": "weekly-english/1.0 (+build script)",
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    total = None
                    cr = resp.headers.get("Content-Range")  # bytes a-b/total
                    if cr and "/" in cr:
                        total = int(cr.split("/")[-1])
                    mode = "ab" if have else "wb"
                    with open(dest, mode) as f:
                        while True:
                            chunk = resp.read(1024 * 256)
                            if not chunk:
                                break
                            f.write(chunk)
                    now = dest.stat().st_size
                    if total and now < total:
                        raise IOError(f"incomplete {now}/{total}")
                    _log(f"  下载完成: {now:,} bytes  <- {src}")
                    return dest
            except Exception as e:                    # 连接重置/超时/不完整
                last_err = e
                size = dest.stat().st_size if dest.exists() else 0
                if size and attempt % 10 == 1:
                    _log(f"  续传中… {size:,} bytes (第 {attempt} 次,{type(e).__name__})")
                time.sleep(0.8)
        _log(f"  放弃该源 {src}: {last_err}")
    raise SystemExit(f"下载失败(最后错误: {last_err})")


def _num(v: str) -> int:
    v = (v or "").strip()
    return int(v) if v.isdigit() else 0


_WORD_TOKEN_RE = re.compile(r"[a-z][a-z'-]*")


def corpus_phrases() -> set[str]:
    """站点语料里出现过的 2~4 词短语(小写)。语料缺失返回空集 —— 该规则自动跳过。

    语料是各期 search.json(全文,小写):本地 data/preview 有就优先用它(覆盖最全),
    兜底用随仓库走的 data/archive_seed。两处都只是文章正文,不含任何用户数据。
    """
    import json
    out: set[str] = set()
    files = sorted(ROOT.glob("data/preview/*/search.json")) or \
            sorted(ROOT.glob("data/archive_seed/*/search.json"))
    for p in files:
        try:
            docs = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            _log(f"  跳过语料 {p}: {e}")
            continue
        for body in (docs or {}).values():
            toks = _WORD_TOKEN_RE.findall(str(body).lower())
            for n in (2, 3, 4):
                for i in range(len(toks) - n + 1):
                    ng = " ".join(toks[i:i + n])
                    if len(ng) <= 30:
                        out.add(ng)
    return out


def build_gz(csv_path: Path, out_gz: Path, phrases: set[str] | None = None) -> dict:
    """流式过滤并压缩写出。返回统计。"""
    out_gz.parent.mkdir(parents=True, exist_ok=True)
    phrases = phrases or set()
    best: dict[str, int] = {}          # 小写词 → 已收录那条的质量分
    picked: dict[str, tuple] = {}      # 小写词 → 最终写出的行
    stats = {"read": 0, "kept": 0, "dup": 0, "no_zh": 0, "rejected": 0, "phrases": 0}

    with open(csv_path, "r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        for row in csv.DictReader(f):
            stats["read"] += 1
            word = (row.get("word") or "").strip()
            zh = (row.get("translation") or "").strip()
            if not word or not zh:
                stats["no_zh"] += 1
                continue
            if len(word) > MAX_WORD_LEN or not _KEEP_WORD_RE.match(word):
                stats["rejected"] += 1
                continue
            bnc, frq = _num(row.get("bnc", "")), _num(row.get("frq", ""))
            collins = _num(row.get("collins", ""))
            quality = ((1 if (row.get("oxford") or "").strip() == "1" else 0) * 1_000_000
                       + collins * 100_000
                       + (10_000 if (row.get("tag") or "").strip() else 0)
                       + (5_000 if bnc else 0)
                       + (1 if frq else 0))
            # 两条专列:连字符复合词;语料里出现过的短语(→ 划词也能本地秒出)
            compound = "-" in word and len(word) <= 30
            in_corpus = " " in word and word.lower() in phrases
            if quality <= 0 and not compound and not in_corpus:
                stats["rejected"] += 1
                continue
            if in_corpus and quality <= 0 and not compound:
                stats["phrases"] += 1
            key = word.lower()
            if key in best:
                stats["dup"] += 1
                if quality <= best[key]:
                    continue                       # 已有更好的那条,跳过
            best[key] = quality
            picked[key] = (key, (row.get("phonetic") or "").strip(),
                           (row.get("pos") or "").strip(), zh.replace("\r", ""), frq)
    stats["kept"] = len(picked)

    with gzip.open(out_gz, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for key in sorted(picked):
            w.writerow(picked[key])
    stats["gz_bytes"] = out_gz.stat().st_size
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="构建离线 ECDICT 词典(过滤 + 压缩)")
    ap.add_argument("--csv", default=None, help="本地已有的 ecdict.csv(跳过下载)")
    ap.add_argument("--rebuild-index", action="store_true", help="顺带建出 ecdict.sqlite")
    ap.add_argument("--out", default=str(OUT_GZ), help="输出 gz 路径")
    args = ap.parse_args()

    out_gz = Path(args.out)
    if args.csv:
        csv_path = Path(args.csv)
        _log(f"用本地 csv: {csv_path}")
    else:
        csv_path = ROOT / ".ecdict_raw.csv"
        if csv_path.exists() and csv_path.stat().st_size > 60_000_000:
            _log(f"已有完整原始 csv,跳过下载: {csv_path} ({csv_path.stat().st_size:,} bytes)")
        else:
            _log("下载 ECDICT 全量 csv(约 63MB,断点续传)…")
            download(csv_path)

    _log("抽取站点语料短语(划词用)…")
    phrases = corpus_phrases()
    _log(f"  语料短语 {len(phrases):,} 个")

    _log("过滤并压缩…")
    st = build_gz(csv_path, out_gz, phrases)
    _log(f"  读入 {st['read']:,} 条 → 保留 {st['kept']:,} 条"
         f"(其中语料短语 {st['phrases']:,}；同形 {st['dup']:,} / 无中文 {st['no_zh']:,}"
         f" / 低质量 {st['rejected']:,})")
    _log(f"  写出 {out_gz} ({st['gz_bytes']:,} bytes = {st['gz_bytes']/1048576:.1f} MB)")

    if args.rebuild_index:
        from dict_client import build_index
        p = build_index(force=True)
        _log(f"  索引: {p} ({p.stat().st_size/1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
