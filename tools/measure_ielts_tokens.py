"""测量写作批改的单次 token 用量(prompt/completion/reasoning)。

用法: python tools/measure_ielts_tokens.py [次数]
用于比较提示词改动前后的思考量差异 —— 结论不能靠感觉,得看数。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

import deepseek_client as d  # noqa: E402

ESSAY = ("Nowadays many people argue that universities should admit only students with the highest "
         "grades. In my opinion this policy is too narrow because grades measure a small part of a "
         "person ability. Firstly academic performance depends heavily on the quality of a student "
         "previous school so a bright teenager from a weak school may look average on paper. Secondly "
         "universities need students who can contribute in different ways including volunteering sport "
         "and practical projects. If admission were based only on examination results such candidates "
         "would never be considered. However I accept that grades are a simple and fairly objective "
         "standard and completely ignoring them would create space for favours. A better solution is "
         "therefore to keep grades as one requirement while also requiring a short interview or a "
         "portfolio. In conclusion although high grades are a reasonable signal they should not be the "
         "only door into higher education because talent appears in more forms than a mark sheet can "
         "capture and that is why I disagree with the statement given above in this essay today.")


def sample(n: int = 3):
    cfg = d._cfg("ASK", "DEEPSEEK", "LLM")
    rows = []
    # 直接调生产用的 prompt 构造函数,测的才是线上真实请求
    prompt = d._writing_prompt("Universities should only admit top graders. Discuss.", ESSAY)
    client = d._client(cfg, timeout=120.0)
    for i in range(n):
        r = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "system", "content": d.IELTS_SYSTEM_PROMPT},
                      {"role": "user", "content": prompt}],
            max_tokens=d.MAX_OUTPUT_TOKENS, temperature=0.3)
        u = r.usage
        det = getattr(u, "completion_tokens_details", None)
        reasoning = getattr(det, "reasoning_tokens", None) if det else None
        rows.append({
            "total": u.total_tokens,
            "completion": u.completion_tokens,
            "reasoning": reasoning,
            "chars": len(r.choices[0].message.content or ""),
        })
        print(f"  run {i+1}: total={u.total_tokens} completion={u.completion_tokens} "
              f"reasoning={reasoning} chars={rows[-1]['chars']}")
    if rows:
        print(f"  avg total = {sum(r['total'] for r in rows) / len(rows):.0f}, "
              f"avg reasoning = {sum((r['reasoning'] or 0) for r in rows) / len(rows):.0f}")
    return rows


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"IELTS writing tokens, {n} runs:")
    sample(n)
