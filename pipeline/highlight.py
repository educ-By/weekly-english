"""
超纲词高亮 — 唯一原则:

    凡是不在"高考 3500 词表 + 其常见变形(屈折/派生/缩写)"内的英文词 = 超纲词。
    标红色圆点下划线。专有名词、首字母大写、缩写、数字一律不标。

白名单来源(完全公开,非模型生成):
  - 高考 3500: https://github.com/mahavivo/english-wordlists/blob/master/Highschool_edited.txt
  - 变形由 `_lemmas()`(规则)* `_IRREGULAR`(不规则表) 在查词时归一覆盖。

注:难度分级(pipeline/difficulty.py)另按"高考 + 四六级"口径判级,与此处白名单相互独立。
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

# 常见不规则复数、序数、以及补漏的常见不规则动词(只收常见形)
_IRREGULAR.update({
    # 不规则复数
    "women": "woman", "children": "child", "feet": "foot", "teeth": "tooth",
    "geese": "goose", "mice": "mouse", "people": "person", "dice": "die",
    "crises": "crisis", "analyses": "analysis", "hypotheses": "hypothesis",
    "theses": "thesis", "diagnoses": "diagnosis", "phenomena": "phenomenon",
    "criteria": "criterion", "bacteria": "bacterium", "fungi": "fungus",
    "nuclei": "nucleus", "stimuli": "stimulus", "alumni": "alumnus",
    "media": "medium", "data": "datum",
    # 序数
    "first": "one", "second": "two", "third": "three", "fourth": "four",
    "fifth": "five", "sixth": "six", "seventh": "seven", "eighth": "eight",
    "ninth": "nine", "tenth": "ten", "eleventh": "eleven", "twelfth": "twelve",
    "twentieth": "twenty",
    # 常见不规则动词补漏
    "born": "bear", "borne": "bear", "bore": "bear", "sought": "seek",
    "dug": "dig", "bred": "breed", "bled": "bleed", "fled": "flee",
    "sped": "speed", "wove": "weave", "woven": "weave", "trod": "tread",
    "clung": "cling", "flung": "fling", "stung": "sting",
    "sprang": "spring", "sprung": "spring", "spun": "spin",
    "sank": "sink", "sunk": "sink", "shrank": "shrink", "shrunk": "shrink",
    # 补全:与高考词表逐词核对后的漏项
    "men": "man", "oxen": "ox", "appendices": "appendix",
    "less": "little", "farther": "far", "most": "many",
    "arose": "arise", "arisen": "arise", "awoke": "awake", "awoken": "awake",
    "beaten": "beat", "bitten": "bite", "dove": "dive", "dreamt": "dream",
    "forbade": "forbid", "forbidden": "forbid",
    "forgave": "forgive", "forgiven": "forgive",
    "learnt": "learn", "mistook": "mistake", "overcame": "overcome",
    "sewn": "sew", "slid": "slide", "smelt": "smell", "spelt": "spell",
    "spat": "spit", "swept": "sweep", "swung": "swing", "wept": "weep",
    # 缩写边缘形
    "y'all": "you", "ma'am": "madam",
})

# 常见缩写(非动词缩写形)——列出以免被当生词
_COMMON_ABBREV = {"approx", "asap", "dept", "govt", "etc", "vs", "eg", "ie", "aka"}

# 缩写还原表 — 不规则缩写(不能靠简单去后缀得到原型)
_CONTRACTION_BASE = {
    "won't": "will", "can't": "can", "cannot": "can",
    "shan't": "shall", "ain't": "be",
}

# 词形归一 —— 返回词 t 的**常见原型候选**列表。
# 调用方命中任意一个候选即视为"在白名单内",所以天然容忍多步归一变体
# (father's → father、biggest → big、feet → foot、softly → soft)。
# 只覆盖常见英语变体:复数/三单、过去式/过去分词、-ing、比较级/最高级、
# 副词 -ly、所有格与缩写,外加常见不规则形;不做语言学完备。
def _lemmas(t: str) -> list[str]:
    out: list[str] = []

    def add(x: str) -> None:
        if x and len(x) >= 1 and x not in out:
            out.append(x)

    def add_irregular(x: str) -> None:
        add(x)
        if x in _IRREGULAR:
            add(_IRREGULAR[x])

    add_irregular(t)

    # 缩写 / 所有格:don't→do、won't→will、isn't→is、we're→we、I'm→I、world's→world
    base = t
    if t in _CONTRACTION_BASE:
        base = _CONTRACTION_BASE[t]
    elif t.endswith("n't"):
        base = t[:-3]
    elif t.endswith(("'re", "'ve", "'ll")):
        base = t[:-3]
    elif t.endswith(("'m", "'d")):
        base = t[:-2]
    elif t.endswith("'s") or t.endswith("s'"):
        base = t[:-2]
    add_irregular(base)

    s = base
    # 复数 / 三单
    if s.endswith("ies") and len(s) > 4:
        add_irregular(s[:-3] + "y")
    if s.endswith(("sses", "shes", "ches", "xes", "zes")):
        add_irregular(s[:-2])
    if s.endswith(("ses", "ges", "ces")) and len(s) > 4:
        add_irregular(s[:-1])
    if s.endswith("s") and not s.endswith(("ss", "us", "is", "os")) and len(s) > 3:
        add_irregular(s[:-1])
    # 过去式 / 过去分词
    if s.endswith("ied"):
        add_irregular(s[:-3] + "y")
    if s.endswith("ed") and len(s) > 3:
        add_irregular(s[:-2])
        add_irregular(s[:-1])
        if s[-3] == s[-4] and s[-3] not in "aeiou":
            add_irregular(s[:-3])
    # -ing(双写只对辅音:stopping→stop,freeing→free 不要误剥成 fre)
    if s.endswith("ing") and len(s) > 4:
        add_irregular(s[:-3])
        add_irregular(s[:-3] + "e")
        if len(s) > 5 and s[-4] == s[-5] and s[-4] not in "aeiou":
            add_irregular(s[:-4])
    # 比较级 / 最高级(含双写:biggest→big、hotter→hot)
    if s.endswith("est") and len(s) > 4:
        add_irregular(s[:-3])
        add_irregular(s[:-2])
        if s[-4] == s[-5] and s[-4] not in "aeiou":
            add_irregular(s[:-4])
    if s.endswith("er") and len(s) > 3:
        add_irregular(s[:-2])
        add_irregular(s[:-1])
        if s[-3] == s[-4] and s[-3] not in "aeiou":
            add_irregular(s[:-3])
    # 副词 -ly(fully←full、truly←true、really←real、duly←due)
    if s.endswith("ly") and len(s) > 3:
        add_irregular(s[:-2])
        add_irregular(s[:-1])
        add_irregular(s[:-2] + "e")
        if s[:-1].endswith("l"):
            add_irregular(s[:-1] + "e")
    # 名词复数 -ves → -f / -fe(knives/wolves/shelves/loaves/thieves/halves)
    if s.endswith("ves") and len(s) > 4:
        add_irregular(s[:-3] + "f")
        add_irregular(s[:-3] + "fe")
    # -oes → -o(tomatoes/potatoes/heroes/volcanoes/echoes)
    if s.endswith("oes") and len(s) > 4:
        add_irregular(s[:-2])
    # 副词派生:basically→basic、possibly→possible、simply→simple
    if s.endswith("ally") and len(s) > 5:
        add_irregular(s[:-4])          # basically → basic / specifically → specific
        add_irregular(s[:-2])          # → basical
    if s.endswith("bly") and len(s) > 5:
        add_irregular(s[:-3] + "ble")
    if s.endswith("ly") and len(s) > 3 and s[:-1].endswith("l"):
        add_irregular(s[:-1] + "e")
    # -ying → -ie(dying→die、tying→tie)
    if s.endswith("ying") and len(s) > 4:
        add_irregular(s[:-4] + "ie")
    # -cked → -c(panicked→panic)
    if s.endswith("cked"):
        add_irregular(s[:-4])
    return out


# 智能引号 → ASCII,避免 "doesn’t"/"world’s" 被切成 "doesn"/"world"
_QUOTE_MAP = {ord(c): "'" for c in ("\u2018", "\u2019", "\u02bc", "\u00b4", "`")}


def _normalize_quotes(s: str) -> str:
    return s.translate(_QUOTE_MAP)

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


WHITELIST: set[str] = _load_whitelist(("highschool_whitelist.txt", "basic_whitelist.txt"))


def find_out_of_scope_words(text: str,
                            whitelist: Iterable[str] | None = None,
                            limit: int = 60) -> list[str]:
    """
    返回不在白名单内的英文词(降序按频度,排除专有名词/缩写/数字),
    这些就是"超纲词",前端会标红。
    """
    vocab = set(whitelist) if whitelist is not None else WHITELIST
    text = _normalize_quotes(text)
    counts: dict[str, int] = {}
    for m in _WORD_RE.findall(text):
        t = m.lower()
        if _is_proper(m):
            continue
        if _is_acronym(t):
            continue
        if t in _COMMON_PROPER_HINTS:
            continue
        if t in _COMMON_ABBREV:
            continue
        # 原型在白名单 → 不标
        if t in vocab:
            continue
        # 任一常见变体原型命中白名单 → 不标
        if any(c in vocab for c in _lemmas(t)):
            continue
        # 连字符复合词:各段都在白名单内 → 不标(year-old、high-speed、wake-up)
        if "-" in t:
            parts = [p for p in t.split("-") if p]
            if parts and all(p in vocab or any(c in vocab for c in _lemmas(p))
                             for p in parts):
                continue
        counts[t] = counts.get(t, 0) + 1

    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in items[:limit]]