"""
讯飞语音评测(ISE)流式版 —— 雅思专区的音素级发音评测。

为什么是讯飞:国内平台里唯一明确不要求成年的(用户协议写明"未满18周岁请在监护人
陪同下…使用本平台"),实名只是身份证+人脸识别;而它给的是英文音节/音素级评分,
这正是纯文字 AI 永远给不了的东西(见 deepseek_client 里"评不了发音"那条)。

协议按讯飞官方客户端(iflytek/astron-agent 的 ise_client.py)实现,不是照文档猜的:
  地址   wss://ise-api.xfyun.cn/v2/open-ise
  签名   host/date/request-line 三行 → HMAC-SHA256 → base64 → 再包一层 base64
  帧     首帧上传参数(status=0, data 为空) → 音频帧(aus 1/2/4, status 1/2)
  结果   data.status==2 时 data.data 是 base64 的 **XML**(不是 JSON)
  音频   16k / 单声道 / 16bit PCM,按 1280B 切片

注意 extra_ability="multi_dimension":不传它只返回总分,没有准确度/流利度/完整度。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import time
import uuid
import xml.etree.ElementTree as ET
from email.utils import formatdate
from urllib.parse import urlencode, urlsplit

log = logging.getLogger(__name__)

ISE_HOST = "ise-api.xfyun.cn"
ISE_PATH = "/v2/open-ise"
ISE_URL = f"wss://{ISE_HOST}{ISE_PATH}"

FRAME_SIZE = 1280                 # 官方客户端同款分片大小
SENTENCE_MAX_WORDS = 30           # 句子模式:≤30 词(免费额度内可用)
PARAGRAPH_MAX_WORDS = 120         # 篇章模式:≤120 词,但需要额外申请「篇章」权限
MAX_AUDIO_BYTES = 6 * 1024 * 1024
CONNECT_TIMEOUT = 15
RECV_TIMEOUT = 30

# dp_message:讯飞给出的逐词/逐音素诊断结果
_DP_TEXT = {
    0: "", 16: "漏读", 32: "增读", 64: "回读", 128: "读错",
}
# perr_msg:中文的声韵/调型错误;英文一般不给
_PERR_TEXT = {1: "声韵错", 2: "调型错", 3: "声韵调型皆错"}

# 错误码 → 给用户看的一句话
_CODE_TEXT = {
    "10163": "评测参数有误,请重试",
    "10313": "评测配置缺失(缺 app_id)",
    "11200": "评测授权已过期,请检查讯飞控制台",
    "11201": "本月评测额度已用完或授权超量",
    "40007": "音频无法解码,请重新录制",
    "60114": "录音太长了,请分几段评测",
    "68676": "识别为乱读或无有效语音,请看着文本重新朗读",
    "10114": "评测请求超时,请重试",
}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_configured() -> bool:
    return bool(_env("XFYUN_APP_ID") and _env("XFYUN_API_KEY")
                and _env("XFYUN_API_SECRET"))


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'’-]+", text or ""))


def allow_chapter() -> bool:
    """篇章题型(read_chapter)是讯飞的高阶权限,要单独申请购买,默认关。

    不申请就用篇章会直接报错,所以默认只走句子模式,超过 30 词就明确让人分段 ——
    比抛一个看不懂的错误码好。
    """
    return _env("XFYUN_ALLOW_CHAPTER") in ("1", "true", "yes")


def max_words() -> int:
    return PARAGRAPH_MAX_WORDS if allow_chapter() else SENTENCE_MAX_WORDS


def pick_category(text: str) -> tuple[str, str]:
    """选评测题型;超长返回 ("", 原因)。

    默认只用句子模式(免费额度内可用);开了 XFYUN_ALLOW_CHAPTER 才用篇章模式。
    """
    n = word_count(text)
    if n == 0:
        return "", "参考文本是空的"
    if n <= SENTENCE_MAX_WORDS:
        return "read_sentence", ""
    if allow_chapter() and n <= PARAGRAPH_MAX_WORDS:
        return "read_chapter", ""
    if allow_chapter():
        return "", (f"朗读文本太长(现在 {n} 词,上限 {PARAGRAPH_MAX_WORDS} 词),"
                    "请分几段评测")
    return "", (f"一次最多评测 {SENTENCE_MAX_WORDS} 词(现在 {n} 词)。"
                "请拆成短句分开读——分段读反而更容易看清每个音的得分。")


def _pcm_from_wav(data: bytes) -> tuple[bytes, str]:
    """从 wav 里取出原始 PCM(讯飞要 aue=raw),并校验规格。

    浏览器端已经统一产出 16k/单声道/16bit,这里再挡一道:规格不对宁可明确报错,
    也别送进去换回一个看不懂的分数。
    """
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return b"", "音频不是 WAV 格式"
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            fmt = body
        elif cid == b"data":
            pcm = body
            break
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None:
        return b"", "音频缺少数据块"
    audio_format, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if audio_format != 1:
        return b"", "音频不是未压缩 PCM"
    if channels != 1:
        return b"", f"音频需要单声道(收到 {channels} 声道)"
    if rate != 16000:
        return b"", f"音频需要 16k 采样率(收到 {rate})"
    if bits != 16:
        return b"", f"音频需要 16bit(收到 {bits}bit)"
    if not pcm:
        return b"", "音频是空的"
    return pcm, ""


def build_auth_url(app_id: str, api_key: str, api_secret: str,
                   url: str = "") -> str:
    """生成带鉴权参数的 WebSocket URL(签名串三行:host / date / request-line)。"""
    url = url or ISE_URL
    parsed = urlsplit(url)
    host, path = parsed.netloc or ISE_HOST, parsed.path or ISE_PATH
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    digest = hmac.new(api_secret.encode("utf-8"), origin.encode("utf-8"),
                      hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    auth_origin = (f'api_key="{api_key}", algorithm="hmac-sha256", '
                   f'headers="host date request-line", signature="{signature}"')
    authorization = base64.b64encode(auth_origin.encode("utf-8")).decode("utf-8")
    query = urlencode({"authorization": authorization, "date": date, "host": host})
    return f"{url}?{query}"


def _business(category: str) -> dict:
    return {
        "sub": "ise",
        "ent": "en_vip",
        "category": category,
        "cmd": "ssb",
        "tte": "utf-8",
        "ttp_skip": True,
        "aue": "raw",                    # 原始 PCM
        "auf": "audio/L16;rate=16000",
        "rst": "entirety",               # 整段结果
        "ise_unite": "1",
        # 不传这个就只有总分,没有准确度/流利度/完整度
        "extra_ability": "multi_dimension",
    }


def iter_frames(app_id: str, text: str, pcm: bytes, category: str):
    """按官方客户端的帧序产出待发送的 JSON 字符串:

    首帧(参数+文本,status=0,data 空) → 音频帧(aus=1 首 / 2 中 / 4 末),
    status 非末帧为 1、末帧为 2。单独抽成生成器是为了能在测试里逐帧断言。
    """
    business = _business(category)
    business["text"] = "\uFEFF" + text        # 官方要求 text 带 UTF-8 BOM 前缀
    yield json.dumps({
        "common": {"app_id": app_id},
        "business": business,
        "data": {"status": 0, "data": ""},
    })

    if not pcm:
        pcm = b"\x00\x00"
    total = len(pcm)
    offset, first_audio = 0, True
    while offset < total:
        chunk = pcm[offset:offset + FRAME_SIZE]
        offset += len(chunk)
        last = offset >= total
        aus = 4 if last else (1 if first_audio else 2)
        frame = {
            "business": {"cmd": "auw", "aus": aus},
            "data": {
                "status": 2 if last else 1,
                "data_type": 1,
                "encoding": "raw",
                "data": base64.b64encode(chunk).decode("ascii"),
            },
        }
        first_audio = False
        yield json.dumps(frame)


def evaluate(audio: bytes, ref_text: str, category: str | None = None) -> dict:
    """评测一段朗读。返回结构与其他提供方一致:score/accuracy/fluency/
    completeness + words[含 mispronounced],这样前端渲染器不用区分来源。"""
    if not is_configured():
        return {"ok": False, "content":
                "发音评测未配置:服务器需要设置 XFYUN_APP_ID / XFYUN_API_KEY / "
                "XFYUN_API_SECRET(讯飞开放平台)。"}
    ref_text = (ref_text or "").strip()
    if not ref_text:
        return {"ok": False, "content": "缺少参考文本"}
    if not audio:
        return {"ok": False, "content": "没有收到音频"}
    if len(audio) > MAX_AUDIO_BYTES:
        return {"ok": False, "content":
                f"录音太长了(超过 {MAX_AUDIO_BYTES // 1048576}MB),请分几段评测"}
    if not category:
        category, why = pick_category(ref_text)
        if not category:
            return {"ok": False, "content": why}

    pcm, bad = _pcm_from_wav(audio)
    if bad:
        return {"ok": False, "content": bad}

    try:
        import websocket          # websocket-client
    except ImportError:
        log.error("websocket-client missing")
        return {"ok": False, "content": "服务器缺少 websocket-client 依赖"}

    app_id = _env("XFYUN_APP_ID")
    url = build_auth_url(app_id, _env("XFYUN_API_KEY"), _env("XFYUN_API_SECRET"),
                         _env("XFYUN_ISE_URL"))
    sid = uuid.uuid4().hex
    try:
        ws = websocket.create_connection(url, timeout=CONNECT_TIMEOUT)
    except Exception as e:
        log.warning("ise connect failed: %s", e)
        # 握手被拒基本就是签名/密钥问题 —— 讯飞鉴权失败在握手阶段就断
        return {"ok": False, "content":
                "连不上讯飞评测服务:请检查 XFYUN_API_KEY / XFYUN_API_SECRET 是否正确"}

    try:
        ws.settimeout(RECV_TIMEOUT)
        for frame in iter_frames(app_id, ref_text, pcm, category):
            ws.send(frame)
        result = _collect(ws)
    except Exception as e:
        log.warning("ise request failed: %s", e)
        return {"ok": False, "content": "评测请求失败,请重试"}
    finally:
        try:
            ws.close()
        except Exception:
            pass

    if not result:
        return {"ok": False, "content": "评测没有返回结果,请重试"}
    top_code = str(result.get("code", "0"))
    if top_code not in ("0", "None", ""):
        return {"ok": False,
                "content": _CODE_TEXT.get(top_code, f"评测失败(讯飞错误码 {top_code})"),
                "code": top_code}
    return parse_result(result, sid)


def _collect(ws) -> dict | None:
    """收消息直到 status==2(评测完成);错误会带 code 提前返回。"""
    final = None
    for _ in range(600):          # 上限兜底,避免服务端不回包时死等
        raw = ws.recv()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            log.warning("ise sent non-json: %s", str(raw)[:200])
            continue
        if str(msg.get("code", "0")) not in ("0", "None", ""):
            return msg
        data = msg.get("data") or {}
        if data.get("status") == 2:
            final = msg
            break
    return final


def parse_result(msg: dict, sid: str = "") -> dict:
    """把讯飞结果(base64 → XML)解析成前端用的统一结构。

    XML 里 read_sentence 是元素属性,word/syll/phone 是嵌套子节点;
    dp_message 说明漏读/增读/回读/读错 —— 这就是"哪个音读错"的来源。
    """
    payload = (msg.get("data") or {}).get("data") or ""
    try:
        xml_text = base64.b64decode(payload).decode("utf-8", "replace")
    except Exception as e:
        log.warning("ise payload decode failed: %s", e)
        return {"ok": False, "content": "评测结果无法解析,请重试"}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("ise xml parse failed: %s | head=%s", e, xml_text[:200])
        return {"ok": False, "content": "评测结果格式异常,请重试"}

    def find(tag: str):
        for el in root.iter():
            if el.tag.lower().endswith(tag):
                return el
        return None

    node = find("read_sentence") or find("read_chapter") or find("read_word") or root
    if (node.get("is_rejected") or "").lower() == "true":
        return {"ok": False, "content":
                "这次录音被判定为乱读(没有对着文本念),请看着文本重新朗读。",
                "code": "rejected"}

    def num(el, *names):
        for n in names:
            v = (el.get(n) or "").strip()
            if v:
                try:
                    return round(float(v), 2)
                except ValueError:
                    continue
        return None

    score = num(node, "total_score")
    accuracy = num(node, "accuracy_score", "phone_score")
    fluency = num(node, "fluency_score")
    integrity = num(node, "integrity_score")

    words = []
    for w in root.iter():
        if not w.tag.lower().endswith("word") or w is node:
            continue
        word = (w.get("content") or w.get("word") or "").strip()
        if not word:
            continue
        issues = []
        for p in w.iter():
            if not p.tag.lower().endswith("phone"):
                continue
            dp = _int(p.get("dp_message"))
            perr = _int(p.get("perr_msg"))
            if dp and dp != 0:
                issues.append({"phone": (p.get("content") or "").strip(),
                               "reference": "", "accuracy": None,
                               "kind": _DP_TEXT.get(dp, "读错")})
            elif perr:
                issues.append({"phone": (p.get("content") or "").strip(),
                               "reference": "", "accuracy": None,
                               "kind": _PERR_TEXT.get(perr, "发音错误")})
        dp_word = _int(w.get("dp_message"))
        if dp_word and dp_word != 0 and not issues:
            issues.append({"phone": "", "reference": "", "accuracy": None,
                           "kind": _DP_TEXT.get(dp_word, "读错")})
        words.append({
            "word": word,
            "accuracy": num(w, "total_score", "phone_score"),
            "match": dp_word or 0,
            "mispronounced": issues,
        })

    out = {"ok": True, "score": score, "accuracy": accuracy,
           "fluency": _to100(fluency), "completeness": _to100(integrity),
           "words": words, "status": "Finished",
           "request_id": msg.get("sid") or sid}
    if score is not None and accuracy is None:
        # 没开"全维度"权限时只有总分 —— 明说,别让界面显示一堆空值
        out["note"] = ("只拿到了总分:讯飞账号需要开通「全维度评测」权限"
                       "(business.extra_ability=multi_dimension)才会返回"
                       "准确度/流利度/完整度。")
    return out


def _int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def _to100(v):
    """讯飞有时用 0-1 小数,有时用 0-100;统一成百分制(前端按 0-100 着色)。"""
    if v is None:
        return None
    return round(v * 100, 1) if v <= 1 else v
