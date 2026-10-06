"""验证讯飞 ISE 客户端的分帧与解析 —— 不需要真实密钥。

两件事:
  1) 起一个本地 WebSocket 服务冒充 ISE,逐帧断言客户端发出的帧符合官方协议
     (首帧 status=0 且 data 为空、音频帧 aus 1/2/4、末帧 status=2、分片 ≤1280B、
      text 带 BOM、extra_ability=multi_dimension)。
  2) 用一份手工构造的 XML 结果验证解析:分数维度、逐词得分、漏读/读错的音素。

用法: python tools/check_ise_protocol.py
"""
import asyncio
import base64
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PORT = 8899
FAILURES = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)


SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<xml_result>
  <read_sentence accuracy_score="72.500000" fluency_score="81.200000"
                 integrity_score="90.000000" total_score="76.400000"
                 phone_score="70.100000" is_rejected="false" word_count="3">
    <sentence>
      <word content="my" total_score="92.000000" dp_message="0">
        <syll content="my" dp_message="0">
          <phone content="m" dp_message="0" perr_msg="0"/>
          <phone content="ay" dp_message="0" perr_msg="0"/>
        </syll>
      </word>
      <word content="hometown" total_score="55.000000" dp_message="0">
        <syll content="home" dp_message="0">
          <phone content="hh" dp_message="0" perr_msg="0"/>
          <phone content="ow" dp_message="128" perr_msg="1"/>
        </syll>
      </word>
      <word content="is" total_score="0.000000" dp_message="16">
        <syll content="is" dp_message="16">
          <phone content="ih" dp_message="16" perr_msg="0"/>
        </syll>
      </word>
    </sentence>
  </read_sentence>
</xml_result>
"""


def make_result_message():
    payload = base64.b64encode(SAMPLE_XML.encode("utf-8")).decode("ascii")
    return json.dumps({"code": 0, "message": "success", "sid": "ise-test-1",
                       "data": {"status": 2, "data": payload}})


def make_wav(seconds=0.5, rate=16000):
    import struct
    n = int(rate * seconds)
    pcm = b"\x10\x27" * n
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm), n * 2


frames_seen = []


async def handler(websocket, *args):
    try:
        async for raw in websocket:
            frames_seen.append(json.loads(raw))
            data = frames_seen[-1].get("data") or {}
            if data.get("status") == 2:
                await websocket.send(make_result_message())
                break
    except Exception as e:      # 客户端提前断开等
        print("  (server)", type(e).__name__, e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


def start_server():
    import websockets

    async def main():
        async with websockets.serve(handler, "127.0.0.1", PORT,
                                    subprotocols=None):
            await asyncio.sleep(30)

    def run():
        asyncio.run(main())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def main():
    wav, pcm_len = make_wav(0.5)
    os.environ["XFYUN_APP_ID"] = "testappid"
    os.environ["XFYUN_API_KEY"] = "testkey"
    os.environ["XFYUN_API_SECRET"] = "testsecret"
    os.environ["XFYUN_ISE_URL"] = f"ws://127.0.0.1:{PORT}/v2/open-ise"

    start_server()

    import ise_client as ise
    import time
    time.sleep(1.0)

    print("== 一、鉴权 URL ==")
    url = ise.build_auth_url("appid", "apikey", "apisecret", ISE_TEST_URL := f"ws://127.0.0.1:{PORT}/v2/open-ise")
    check("URL 带 authorization/date/host",
          all(k in url for k in ("authorization=", "date=", "host=")))
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(url).query)
    auth_raw = base64.b64decode(q["authorization"][0]).decode()
    check("authorization 结构正确",
          auth_raw.startswith('api_key="apikey", algorithm="hmac-sha256", '
                              'headers="host date request-line", signature="'),
          auth_raw[:60] + "…")
    check("host 参数正确", q["host"][0] == "127.0.0.1:8899", q["host"][0])

    print("== 二、分帧(对着本地假 ISE) ==")
    res = ise.evaluate(wav, "my hometown is")
    check("评测返回 ok", res.get("ok") is True, str(res)[:160])

    check("至少收到参数帧 + 音频帧", len(frames_seen) >= 2, f"{len(frames_seen)} 帧")
    first = frames_seen[0]
    check("首帧含 common.app_id", (first.get("common") or {}).get("app_id") == "testappid")
    biz = first.get("business") or {}
    check("首帧 sub=ise / ent=en_vip / category=read_sentence",
          biz.get("sub") == "ise" and biz.get("ent") == "en_vip"
          and biz.get("category") == "read_sentence")
    check("首帧 text 带 UTF-8 BOM", str(biz.get("text", "")).startswith("\ufeff"))
    check("首帧带 extra_ability=multi_dimension",
          biz.get("extra_ability") == "multi_dimension")
    check("首帧 aue=raw / auf=16k",
          biz.get("aue") == "raw" and biz.get("auf") == "audio/L16;rate=16000")
    check("首帧 data.status=0 且 data 为空",
          first["data"].get("status") == 0 and first["data"].get("data") == "")

    audio_frames = frames_seen[1:]
    expect_frames = -(-pcm_len // 1280)
    check("音频帧数 = ceil(PCM/1280)", len(audio_frames) == expect_frames,
          f"{len(audio_frames)} vs {expect_frames}")
    sizes = [len(base64.b64decode(f["data"]["data"])) for f in audio_frames]
    check("每片 ≤1280B", all(s <= 1280 for s in sizes), f"max={max(sizes)}")
    check("分片总长 = PCM 长度", sum(sizes) == pcm_len, f"{sum(sizes)} vs {pcm_len}")
    check("首音频帧 aus=1", (audio_frames[0].get("business") or {}).get("aus") == 1)
    check("中间音频帧 cmd=auw / status=1 / data_type=1 / encoding=raw",
          (audio_frames[0].get("business") or {}).get("cmd") == "auw"
          and audio_frames[0]["data"].get("status") == 1
          and audio_frames[0]["data"].get("data_type") == 1
          and audio_frames[0]["data"].get("encoding") == "raw")
    last = audio_frames[-1]
    check("末帧 aus=4 且 status=2",
          (last.get("business") or {}).get("aus") == 4 and last["data"].get("status") == 2)

    print("== 三、结果解析(XML) ==")
    check("总分", res.get("score") == 76.4, str(res.get("score")))
    check("准确度", res.get("accuracy") == 72.5, str(res.get("accuracy")))
    check("流利度", res.get("fluency") == 81.2, str(res.get("fluency")))
    check("完整度", res.get("completeness") == 90.0, str(res.get("completeness")))
    words = {w["word"]: w for w in res.get("words") or []}
    check("解析出 3 个词", len(words) == 3, str(list(words)))
    check("正常词无误报", words.get("my", {}).get("mispronounced") == [])
    issues = words.get("hometown", {}).get("mispronounced") or []
    check("读错的音素被标出(dp=128)", len(issues) == 1 and issues[0]["kind"] == "读错",
          str(issues))
    miss = words.get("is", {}).get("mispronounced") or []
    check("漏读被标出(dp=16)", miss and miss[0]["kind"] == "漏读", str(miss))

    print("== 四、乱读与缺权限 ==")
    rej = ise.parse_result({"data": {"status": 2, "data": base64.b64encode(
        SAMPLE_XML.replace('is_rejected="false"', 'is_rejected="true"')
        .encode()).decode()}})
    check("is_rejected=true 被识别", rej.get("ok") is False and "乱读" in rej.get("content", ""),
          str(rej)[:80])
    no_multi = ise.parse_result({"data": {"status": 2, "data": base64.b64encode(
        SAMPLE_XML.replace(' accuracy_score="72.500000"', "")
        .replace(' fluency_score="81.200000"', "")
        .replace(' integrity_score="90.000000"', "")
        .replace(' phone_score="70.100000"', "")      # 没开全维度时这项也不返回
        .encode()).decode()}})
    check("缺全维度时只留总分并给出提示",
          no_multi.get("score") == 76.4 and no_multi.get("accuracy") is None
          and bool(no_multi.get("note")),
          str(no_multi.get("note"))[:60])

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} 项失败: {FAILURES}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
