"""
腾讯云智聆口语评测(SOE)—— 雅思专区的发音评测后端。

这是全站唯一会拿到"音频"的地方:前面所有 AI 路径都只有文字,评不了发音。
智聆是音素级评测:能给出逐词得分,以及具体哪个音素读错(标准音素 vs 实际音素),
所以口语模块终于可以真的指出"你把 /θ/ 念成了 /s/"。

音频要求(硬性):16k 采样率、16bit、单声道、wav —— 浏览器端在
static/ielts.js 里用 Web Audio 重采样并编码成这个格式,这里再校验一次。

用官方 SDK(tencentcloud-sdk-python-soe)而不是手写 TC3 签名:没有密钥就无法
本地验证签名,而签名错了表现为"整个功能静默失败",代价太大。SDK 还能拿到权威
的字段定义。
"""
from __future__ import annotations

import base64
import logging
import os
import re
import struct
import uuid

log = logging.getLogger(__name__)

# 智聆音频规格 —— 浏览器端与这里必须一致,不一致会被 evaluate() 直接拒绝
WAV_SAMPLE_RATE = 16000
WAV_CHANNELS = 1
WAV_BITS = 16
MAX_AUDIO_BYTES = 6 * 1024 * 1024        # 16k/mono/16bit ≈ 32KB/s,6MB 够 3 分钟

# EvalMode
EVAL_WORD = 0
EVAL_SENTENCE = 1
EVAL_PARAGRAPH = 2
EVAL_FREE = 3

# 文本长度限制(官方:句子 ≤30 词,段落 ≤120 词)
SENTENCE_MAX_WORDS = 30
PARAGRAPH_MAX_WORDS = 120

_ENDPOINT = "soe.tencentcloudapi.com"
_REGION = ""            # SOE 不需要 Region


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_configured() -> bool:
    return bool(_env("TENCENT_SECRET_ID") and _env("TENCENT_SECRET_KEY"))


def _score_coeff() -> float:
    """苛刻指数 [1.0-4.0]。官方:1.0 适合儿童,4.0 适合成人严格打分。"""
    raw = _env("TENCENT_SOE_SCORE_COEFF") or "3.0"
    try:
        v = float(raw)
    except ValueError:
        return 3.0
    return min(4.0, max(1.0, v))


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'’-]+", text or ""))


def pick_eval_mode(text: str) -> tuple[int, str]:
    """按参考文本长度选评测模式;超长返回 (0, 原因)。"""
    n = word_count(text)
    if n == 0:
        return 0, "参考文本是空的"
    if n <= SENTENCE_MAX_WORDS:
        return EVAL_SENTENCE, ""
    if n <= PARAGRAPH_MAX_WORDS:
        return EVAL_PARAGRAPH, ""
    return 0, (f"朗读文本太长(现在 {n} 词,上限 {PARAGRAPH_MAX_WORDS} 词),"
               "请分几段分别评测")


def _check_wav(data: bytes) -> str:
    """校验 wav 头是否符合智聆要求。返回空串表示通过,否则是给用户看的原因。"""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return "音频不是 WAV 格式"
    # 扫 fmt 块:不能假设它一定在偏移 12
    pos = 12
    fmt = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            fmt = body
            break
        if cid == b"data":
            break
        pos += 8 + size + (size & 1)
    if fmt is None:
        return "音频缺少格式信息"
    audio_format, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if audio_format != 1:
        return "音频不是未压缩 PCM"
    if channels != WAV_CHANNELS:
        return f"音频需要单声道(收到 {channels} 声道)"
    if rate != WAV_SAMPLE_RATE:
        return f"音频需要 {WAV_SAMPLE_RATE // 1000}k 采样率(收到 {rate})"
    if bits != WAV_BITS:
        return f"音频需要 {WAV_BITS}bit(收到 {bits}bit)"
    return ""


def evaluate(audio: bytes, ref_text: str, eval_mode: int | None = None) -> dict:
    """评测一段朗读音频。返回 {ok, ...};失败时 ok=False 且 content 是给用户的中文原因。

    成功时返回:
      score        总分 SuggestedScore [0,100]
      accuracy     准确度 PronAccuracy [-1,100]
      fluency      流利度 PronFluency [0,1](词模式无意义)
      completeness 完整度 PronCompletion [0,1]
      words        [{word, accuracy, match, mispronounced:[{phone, should_be, ...}]}]
    """
    if not is_configured():
        return {"ok": False, "content":
                "发音评测未配置:需要在服务器上设置 TENCENT_SECRET_ID / "
                "TENCENT_SECRET_KEY(腾讯云访问密钥)。"}
    ref_text = (ref_text or "").strip()
    if not ref_text:
        return {"ok": False, "content": "缺少参考文本"}
    if not audio:
        return {"ok": False, "content": "没有收到音频"}
    if len(audio) > MAX_AUDIO_BYTES:
        return {"ok": False, "content":
                f"录音太长了(超过 {MAX_AUDIO_BYTES // 1048576}MB),请分几段评测"}
    bad = _check_wav(audio)
    if bad:
        return {"ok": False, "content": bad}

    if eval_mode is None:
        eval_mode, why = pick_eval_mode(ref_text)
        if not eval_mode:
            return {"ok": False, "content": why}
    if len(ref_text) > 2000:
        ref_text = ref_text[:2000]

    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.soe.v20180724 import models, soe_client
    except ImportError as e:
        log.error("tencent sdk missing: %s", e)
        return {"ok": False, "content": "服务器缺少腾讯云 SDK(tencentcloud-sdk-python-soe)"}

    cred_args = (_env("TENCENT_SECRET_ID"), _env("TENCENT_SECRET_KEY"))
    token = _env("TENCENT_TOKEN")           # 临时密钥(STS)才需要
    cred = credential.Credential(*cred_args, token) if token else credential.Credential(*cred_args)

    http_profile = HttpProfile()
    http_profile.endpoint = _ENDPOINT
    http_profile.reqTimeout = 30
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = soe_client.SoeClient(cred, _REGION, client_profile)

    req = models.TransmitOralProcessWithInitRequest()
    req.SessionId = uuid.uuid4().hex
    req.RefText = ref_text
    req.WorkMode = 1                 # 1 = 非流式一次性评估(整段音频一次传完)
    req.EvalMode = eval_mode
    req.ScoreCoeff = _score_coeff()
    req.ServerType = 0               # 0 = 英文
    req.VoiceFileType = 2            # 2 = wav
    req.VoiceEncodeType = 1          # 1 = pcm
    req.SeqId = 1
    req.IsEnd = 1
    req.UserVoiceData = base64.b64encode(audio).decode("ascii")
    # SoeAppId 不填:官方明确"未新建请勿填入,否则报欠费"
    app_id = _env("TENCENT_SOE_APP_ID")
    if app_id:
        req.SoeAppId = app_id

    try:
        resp = client.TransmitOralProcessWithInit(req)
    except Exception as e:
        # SDK 的异常 message 里带 RequestId 与错误码,原样记日志便于排查;
        # 给用户的是不泄露内部细节的一句
        code = getattr(e, "code", "") or ""
        log.warning("soe evaluate failed: %s", e)
        if "AuthFailure" in code or "SecretId" in str(e):
            msg = "发音评测的密钥无效,请在服务器上检查 TENCENT_SECRET_ID / TENCENT_SECRET_KEY"
        elif "LimitExceeded" in code or "欠费" in str(e) or "Insufficient" in code:
            msg = "发音评测额度不足或服务未开通,请检查腾讯云账户"
        elif "timeout" in str(e).lower():
            msg = "发音评测超时,请重试"
        else:
            msg = "发音评测服务暂时不可用,请稍后再试"
        return {"ok": False, "content": msg, "code": code}

    return _normalize(resp)


def _normalize(resp) -> dict:
    """把 SOE 响应整理成前端好用的形状,并挑出读错的音素。"""
    def f(x):
        try:
            return round(float(x), 2)
        except (TypeError, ValueError):
            return None

    words = []
    for w in (getattr(resp, "Words", None) or []):
        phones = []
        for p in (getattr(w, "PhoneInfos", None) or []):
            acc = f(getattr(p, "PronAccuracy", None))
            phones.append({
                "phone": getattr(p, "Phone", "") or "",
                "reference": getattr(p, "ReferencePhone", "") or "",
                "accuracy": acc,
                "should_stress": bool(getattr(p, "Stress", False)),
                "detected_stress": bool(getattr(p, "DetectedStress", False)),
            })
        # 读错的音素:准确度低,或识别出的音素与标准音素不一致
        mis = [p for p in phones if (p["accuracy"] is not None and p["accuracy"] < 60)
               or (p["reference"] and p["phone"] and p["reference"] != p["phone"])]
        words.append({
            "word": getattr(w, "Word", "") or getattr(w, "ReferenceWord", "") or "",
            "accuracy": f(getattr(w, "PronAccuracy", None)),
            "match": getattr(w, "MatchTag", None),
            "mispronounced": mis,
        })

    accuracy = f(getattr(resp, "PronAccuracy", None))
    fluency = f(getattr(resp, "PronFluency", None))
    completeness = f(getattr(resp, "PronCompletion", None))
    score = f(getattr(resp, "SuggestedScore", None))
    # 词/自由说模式下流利度无意义(-1);别把 -1 当分数显示
    if fluency is not None and fluency < 0:
        fluency = None
    if completeness is not None and completeness < 0:
        completeness = None
    # 统一成百分制:智聆这两项是 0-1,而讯飞是 0-100,前端按一个口径渲染
    if fluency is not None:
        fluency = round(fluency * 100, 1)
    if completeness is not None:
        completeness = round(completeness * 100, 1)

    return {"ok": True, "score": score, "accuracy": accuracy,
            "fluency": fluency, "completeness": completeness,
            "words": words, "status": getattr(resp, "Status", "") or "",
            "request_id": getattr(resp, "RequestId", "") or ""}
