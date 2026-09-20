"""
超纲词高亮 — 唯一原则:

    凡是不在"高中课标 ∩ 四六级"白名单里的英文词 = 超纲词。
    标红色圆点下划线。专有名词、首字母大写、缩写、数字一律不标。

白名单来源(完全公开,非模型生成):
  - 高中: https://github.com/mahavivo/english-wordlists/blob/master/Highschool_edited.txt
  - 四六级: https://github.com/mahavivo/english-wordlists/blob/master/CET_4+6_edited.txt

若用户接入有道词典 API,可在此基础上叠加"权威词典是否收录"二次校验,
接口骨架见 youdao_client.py。
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Iterable

VOCAB_DIR = Path(__file__).resolve().parent.parent / "data" / "cefr_vocab"

# 至少含 4 个字母,允许连字符、撇号(do-it-yourself)
_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]{3,}\b")

# 不规则词形表 — 这是兜底,避免把"became/built/held/fallen"误判成超纲
_IRREGULAR = {
    "became": "become", "become": "become",
    "began": "begin", "begun": "begin", "begins": "begin", "beginning": "begin",
    "bent": "bend",
    "broke": "break", "broken": "break", "breaks": "break", "breaking": "break",
    "brought": "bring", "brings": "bring", "bringing": "bring",
    "built": "build", "building": "build",
    "burnt": "burn", "burned": "burn",
    "bought": "buy", "buying": "buy", "buys": "buy",
    "caught": "catch", "catches": "catch", "catching": "catch",
    "chose": "choose", "chosen": "choose", "chooses": "choose", "choosing": "choose",
    "came": "come", "comes": "come", "coming": "come",
    "cost": "cost", "costs": "cost", "costing": "cost",
    "crept": "creep",
    "cut": "cut", "cuts": "cut", "cutting": "cut",
    "dealt": "deal", "deals": "deal", "dealing": "deal",
    "did": "do", "done": "do", "does": "do", "doing": "do",
    "drew": "draw", "drawn": "draw", "draws": "draw", "drawing": "draw",
    "drank": "drink", "drunk": "drink", "drinks": "drink", "drinking": "drink",
    "drove": "drive", "driven": "drive", "drives": "drive", "driving": "drive",
    "eaten": "eat", "eats": "eat", "eating": "eat",
    "fell": "fall", "fallen": "fall", "falls": "fall", "falling": "fall",
    "fed": "feed", "feeds": "feed", "feeding": "feed",
    "felt": "feel", "feels": "feel", "feeling": "feel",
    "fought": "fight", "fights": "fight", "fighting": "fight",
    "found": "find", "finds": "find", "finding": "find",
    "flew": "fly", "flown": "fly", "flies": "fly", "flying": "fly",
    "forgot": "forget", "forgotten": "forget", "forgets": "forget", "forgetting": "forget",
    "froze": "freeze", "frozen": "freeze", "freezes": "freeze", "freezing": "freeze",
    "gave": "give", "given": "give", "gives": "give", "giving": "give",
    "went": "go", "gone": "go", "goes": "go", "going": "go",
    "ground": "grind", "grinds": "grind", "grinding": "grind",
    "grew": "grow", "grown": "grow", "grows": "grow", "growing": "grow",
    "hung": "hang", "hangs": "hang", "hanging": "hang",
    "had": "have", "has": "have", "having": "have",
    "heard": "hear", "hears": "hear", "hearing": "hear",
    "hid": "hide", "hidden": "hide", "hides": "hide", "hiding": "hide",
    "hit": "hit", "hits": "hit", "hitting": "hit",
    "held": "hold", "holds": "hold", "holding": "hold",
    "hurt": "hurt", "hurts": "hurt", "hurting": "hurt",
    "kept": "keep", "keeps": "keep", "keeping": "keep",
    "knew": "know", "known": "know", "knows": "know", "knowing": "know",
    "laid": "lay", "lays": "lay", "laying": "lay",
    "led": "lead", "leads": "lead", "leading": "lead",
    "left": "leave", "leaving": "leave",
    "lent": "lend", "lending": "lend",
    "let": "let", "lets": "let", "letting": "let",
    "lay": "lie", "lain": "lie", "lying": "lie",
    "lit": "light", "lights": "light", "lighting": "light",
    "lost": "lose", "loses": "lose", "losing": "lose",
    "made": "make", "makes": "make", "making": "make",
    "meant": "mean", "means": "mean", "meaning": "mean",
    "met": "meet", "meets": "meet", "meeting": "meet",
    "paid": "pay", "pays": "pay", "paying": "pay",
    "put": "put", "puts": "put", "putting": "put",
    "read": "read", "reads": "read", "reading": "read",
    "rode": "ride", "ridden": "ride", "rides": "ride", "riding": "ride",
    "rang": "ring", "rung": "ring", "rings": "ring", "ringing": "ring",
    "rose": "rise", "risen": "rise", "rises": "rise", "rising": "rise",
    "ran": "run", "runs": "run", "running": "run",
    "said": "say", "says": "say", "saying": "say",
    "saw": "see", "seen": "see", "sees": "see", "seeing": "see",
    "sold": "sell", "sells": "sell", "selling": "sell",
    "sent": "send", "sends": "send", "sending": "send",
    "set": "set", "sets": "set", "setting": "set",
    "shook": "shake", "shaken": "shake", "shakes": "shake", "shaking": "shake",
    "shone": "shine", "shining": "shine",
    "shot": "shoot", "shoots": "shoot", "shooting": "shoot",
    "showed": "show", "shown": "show", "shows": "show", "showing": "show",
    "shut": "shut", "shuts": "shut", "shutting": "shut",
    "sang": "sing", "sung": "sing", "sings": "sing", "singing": "sing",
    "sat": "sit", "sits": "sit", "sitting": "sit",
    "slept": "sleep", "sleeps": "sleep", "sleeping": "sleep",
    "spoke": "speak", "spoken": "speak", "speaks": "speak", "speaking": "speak",
    "spent": "spend", "spends": "spend", "spending": "spend",
    "spread": "spread", "spreads": "spread", "spreading": "spread",
    "stole": "steal", "stolen": "steal", "steals": "steal", "stealing": "steal",
    "struck": "strike", "strikes": "strike", "striking": "strike",
    "stuck": "stick", "sticks": "stick", "sticking": "stick",
    "swore": "swear", "sworn": "swear", "swears": "swear", "swearing": "swear",
    "swam": "swim", "swum": "swim", "swims": "swim", "swimming": "swim",
    "took": "take", "taken": "take", "takes": "take", "taking": "take",
    "taught": "teach", "teaches": "teach", "teaching": "teach",
    "tore": "tear", "torn": "tear", "tears": "tear", "tearing": "tear",
    "told": "tell", "tells": "tell", "telling": "tell",
    "thought": "think", "thinks": "think", "thinking": "think",
    "threw": "throw", "thrown": "throw", "throws": "throw", "throwing": "throw",
    "understood": "understand", "understands": "understand", "understanding": "understand",
    "woke": "wake", "woken": "wake", "wakes": "wake", "waking": "wake",
    "wore": "wear", "worn": "wear", "wears": "wear", "wearing": "wear",
    "won": "win", "wins": "win", "winning": "win",
    "wrote": "write", "written": "write", "writes": "write", "writing": "write",
    # 比较级 / 最高级
    "better": "well", "best": "well", "worse": "bad", "worst": "bad",
    "further": "far", "furthest": "far", "farthest": "far",
    "older": "old", "elder": "old", "eldest": "old",
    # be / do / have 系(最常用,绝不能误标)
    "am": "be", "is": "be", "are": "be", "was": "be", "were": "be",
    "been": "be", "being": "be",
    "did": "do", "does": "do", "done": "do", "doing": "do",
    "had": "have", "has": "have", "having": "have",
    "said": "say", "says": "say", "saying": "say",
    "made": "make", "makes": "make", "making": "make",
    "went": "go", "gone": "go", "goes": "go", "going": "go",
    "got": "get", "gotten": "get", "gets": "get", "getting": "get",
    "saw": "see", "seen": "see", "sees": "see", "seeing": "see",
    "knew": "know", "known": "know", "knows": "know", "knowing": "know",
    "took": "take", "taken": "take", "takes": "take", "taking": "take",
    "gave": "give", "given": "give", "gives": "give", "giving": "give",
    "found": "find", "finds": "find", "finding": "find",
    "told": "tell", "tells": "tell", "telling": "tell",
    "felt": "feel", "feels": "feel", "feeling": "feel",
    "left": "leave", "leaves": "leave", "leaving": "leave",
    "kept": "keep", "keeps": "keep", "keeping": "keep",
    "held": "hold", "holds": "hold", "holding": "hold",
    "thought": "think", "thinks": "think", "thinking": "think",
    "taught": "teach", "teaches": "teach", "teaching": "teach",
    "won": "win", "wins": "win", "winning": "win",
    "lost": "lose", "loses": "lose", "losing": "lose",
    "met": "meet", "meets": "meet", "meeting": "meet",
    "ran": "run", "runs": "run", "running": "run",
    "rode": "ride", "ridden": "ride", "rides": "ride", "riding": "ride",
    "wrote": "write", "written": "write", "writes": "write", "writing": "write",
    "drove": "drive", "driven": "drive", "drives": "drive", "driving": "drive",
    "ate": "eat", "eaten": "eat", "eats": "eat", "eating": "eat",
    "drank": "drink", "drunk": "drink", "drinks": "drink", "drinking": "drink",
    "wore": "wear", "worn": "wear", "wears": "wear", "wearing": "wear",
    "blew": "blow", "blown": "blow", "blows": "blow", "blowing": "blow",
    "grew": "grow", "grown": "grow", "grows": "grow", "growing": "grow",
    "flew": "fly", "flown": "fly", "flies": "fly", "flying": "fly",
    "slept": "sleep", "sleeps": "sleep", "sleeping": "sleep",
    "spoke": "speak", "spoken": "speak", "speaks": "speak", "speaking": "speak",
    "stood": "stand", "stands": "stand", "standing": "stand",
    "understood": "understand", "understands": "understand",
    "sent": "send", "sends": "send", "sending": "send",
    "spent": "spend", "spends": "spend", "spending": "spend",
    "sold": "sell", "sells": "sell", "selling": "sell",
    "fell": "fall", "falls": "fall", "falling": "fall",
    "paid": "pay", "pays": "pay", "paying": "pay",
    "sat": "sit", "sits": "sit", "sitting": "sit",
    "meant": "mean", "means": "mean",
    "heard": "hear", "hears": "hear", "hearing": "hear",
    "led": "lead", "leads": "lead", "leading": "lead",
    "lay": "lie", "lain": "lie", "lies": "lie", "lying": "lie",
    "rose": "rise", "risen": "rise", "rises": "rise", "rising": "rise",
    "drew": "draw", "drawn": "draw", "draws": "draw", "drawing": "draw",
    "shook": "shake", "shaken": "shake", "shakes": "shake", "shaking": "shake",
    "stole": "steal", "stolen": "steal", "steals": "steal", "stealing": "steal",
    "forgot": "forget", "forgotten": "forget", "forgets": "forget",
    "hid": "hide", "hidden": "hide", "hides": "hide", "hiding": "hide",
    "rang": "ring", "rung": "ring", "rings": "ring", "ringing": "ring",
    "swam": "swim", "swum": "swim", "swims": "swim", "swimming": "swim",
    "sang": "sing", "sung": "sing", "sings": "sing", "singing": "sing",
    "froze": "freeze", "frozen": "freeze", "freezes": "freeze",
    "woke": "wake", "woken": "wake", "wakes": "wake", "waking": "wake",
}

# 极简词形归一:不做语言学完备,只覆盖常见英语曲折
# 让 "markets" / "rate" / "rates" / "running" / "loved" 都能命中白名单原型
def _lemma(t: str) -> str:
    if not t:
        return t
    # 先查不规则表
    if t in _IRREGULAR:
        return _IRREGULAR[t]
    # 所有格 world's / students' → 原词
    if t.endswith("'s") or t.endswith("s'"):
        return t[:-2]
    # -ies → -y
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    # -sses / -shes / -ches / -xes / -zes  → 去 es
    if t.endswith(("sses", "shes", "ches", "xes", "zes")):
        return t[:-2]
    # -ied → -y
    if t.endswith("ied"):
        return t[:-3] + "y"
    # -ses / -ges / -ces / -zes  → 去 s
    if t.endswith(("ses", "ges", "ces")) and len(t) > 4:
        return t[:-1]
    # -s (复数 / 三单)
    if t.endswith("s") and not t.endswith(("ss", "us", "is", "os")) and len(t) > 4:
        return t[:-1]
    # -ed (过去式 / 过去分词)
    if t.endswith("ed") and len(t) > 4:
        if len(t) > 4 and t[-3] == t[-4]:
            return t[:-3]
        return t[:-2]
    # -ing
    if t.endswith("ing") and len(t) > 5:
        if len(t) > 5 and t[-4] == t[-5]:
            return t[:-4]
        return t[:-3]
    # 形容词比较级 -er / -est (排除 -er 结尾的实词:teacher, officer)
    if t.endswith("er") and len(t) > 5:
        base = t[:-2]
        # 如果基础形是个白名单词,可能是比较级 — 我们直接返回 base,
        # 让上层查 base 即可。空白名单没有也不算错(本来就是超纲)。
        return base
    if t.endswith("est") and len(t) > 5:
        return t[:-3]
    return t

# 专有名词启发式 — 首字母大写不标(人名、地名、机构)
def _is_proper(token: str) -> bool:
    return token[0].isupper()

# 明显是专有名词的"全部大写缩写" — 不标
def _is_acronym(token: str) -> bool:
    return token.isupper() and len(token) <= 6

# 人名、机构、地名常出现在白名单外,但语义上学生不需要"超纲"标签 — 跳过
_COMMON_PROPER_HINTS = {
    # 期刊 / 媒体
    "economist", "guardian", "smitty", "smithsonian", "scientific",
    "american", "magazine", "npr", "voa", "bbc", "cnn", "reuters",
    # 常见地名 / 国家(已在大写里处理,但白名单外全小写时也跳过)
    "london", "paris", "tokyo", "beijing", "frankfurt", "washington",
    "york", "angeles", "america", "china", "britain", "europe", "asia",
    # 常见姓氏 / 名
    "smith", "john", "johnson", "mike", "david", "james", "robert",
    "mary", "jane", "linda", "susan",
}


def _load_whitelist(names: tuple = ("highschool_whitelist.txt", "cet_whitelist.txt")) -> set[str]:
    base: set[str] = set()
    for name in names:
        path = VOCAB_DIR / name
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                w = line.strip().lower()
                if not w or w.startswith("#"):
                    continue
                if " " in w:
                    continue
                if all(c.isalpha() or c in ("'", "-") for c in w):
                    base.add(w)

    # 闭包展开:对 base 中每个词,生成常见屈折/派生形,全部并入。
    expanded: set[str] = set(base)
    for w in list(base):
        # 复数
        if w.endswith(("s", "x", "ch", "sh")):
            expanded.add(w + "es")
        elif w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
            expanded.add(w[:-1] + "ies")
        else:
            expanded.add(w + "s")
        # 过去式 / 过去分词
        if w.endswith("e"):
            expanded.add(w + "d")
            expanded.add(w[:-1] + "ed")   # -e + -d
            # 也补 -ing
            expanded.add(w[:-1] + "ing")
        elif (w.endswith(("b","c","d","f","g","k","l","m","n","p","r","s","t","v","z"))
              and len(w) > 2 and w[-1] == w[-2]):
            # doubled consonant: stop → stopped, stopping
            expanded.add(w + "ed")
            expanded.add(w + "ing")
        else:
            expanded.add(w + "ed")
            expanded.add(w + "ing")
        # -er / -est
        if w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
            expanded.add(w[:-1] + "ier")
            expanded.add(w[:-1] + "iest")
        elif w.endswith("e") and len(w) > 2:
            expanded.add(w + "r")          # large → larger
            expanded.add(w + "st")         # large → largest
            expanded.add(w[:-1] + "er")
            expanded.add(w[:-1] + "est")
        elif len(w) > 2 and w[-1] not in "aeiouwxy":
            expanded.add(w + "er")
            expanded.add(w + "est")
        # -ly
        if w.endswith("y"):
            expanded.add(w[:-1] + "ily")
        else:
            expanded.add(w + "ly")
        # -s 三单 — 同 -s,覆盖
    return expanded


WHITELIST: set[str] = _load_whitelist()


def find_out_of_scope_words(text: str,
                            whitelist: Iterable[str] | None = None,
                            limit: int = 60) -> list[str]:
    """
    返回不在白名单内的英文词(降序按频度,排除专有名词/缩写/数字),
    这些就是"超纲词",前端会标红。
    """
    vocab = set(whitelist) if whitelist is not None else WHITELIST
    counts: dict[str, int] = {}
    for m in _WORD_RE.findall(text):
        t = m.lower()
        if _is_proper(m):
            continue
        if _is_acronym(t):
            continue
        if t in _COMMON_PROPER_HINTS:
            continue
        # 原型在白名单 → 不标
        if t in vocab:
            continue
        # 词形归一后命中
        if _lemma(t) in vocab:
            continue
        # -ly 派生(exactly/quietly):基础形容词在白名单里就放行
        if t.endswith("ly") and len(t) > 4 and t[:-2] in vocab:
            continue
        counts[t] = counts.get(t, 0) + 1

    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in items[:limit]]