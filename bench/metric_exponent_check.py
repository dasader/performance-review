#!/usr/bin/env python3
"""추출된 수치가 원문의 지수 표기를 보존했는가.

왜 따로 필요한가. `temp0_fulltext.py`의 "수치 근거율"은 **값의 숫자가 초록에 있는가**만
본다. 그 검사는 지수 손실을 통과시킨다 — 원문 `8 ± 2 × 10⁻¹² M☉/yr`에서 추출된
`8 M☉/yr`은 "8이 초록에 있으므로" 근거 있음으로 세어지지만 **12자릿수 틀린 값**이다.
그리고 이 값은 `stats.aggregate_metrics`가 그대로 집계한다.

실측 계기: SR 12 c 논문(HST/WFC3 강착률)에서 Gemini가 temperature 0·1 양쪽 모두
`×10⁻⁵`·`×10⁻¹²`를 통째로 날렸고 deepseek-v4-pro:0813만 보존했다.

판정 방법: 추출값의 선두 숫자를 초록에서 찾고, **그 직후에 `×10ⁿ`이 붙어 있는데**
추출값에는 지수가 없으면 손실로 센다. 같은 숫자가 초록의 다른 자리에도 나오면
오탐이 생기므로 **이 값은 손실률의 상한**이다.

    PYTHONPATH=backend backend/.venv/bin/python bench/metric_exponent_check.py
"""
import argparse
import re

import psycopg2

NUM = re.compile(r"\d+(?:\.\d+)?")
EXP_AFTER = re.compile(r"^[\s^±\-–−\d.]{0,12}(?:[×x*]\s*10|10\s*[\^−\-–])\s*[−\-–]?\s*\d")
VAL_EXP = re.compile(r"(?:10\^|[×x*]\s*10|[eE][+\-−]?\d|\^\s*[−\-]?\d)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--show", type=int, default=8)
    args = ap.parse_args()

    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()
    cur.execute("""
        SELECT p.abstract, e.metrics_json FROM paper_extractions e
        JOIN papers p ON p.paper_key = e.paper_key
        WHERE json_array_length(e.metrics_json) > 0
          AND p.abstract ~ '(×|x) ?10 ?[−-]? ?[0-9]'
        LIMIT %s
    """, (args.limit,))
    rows = cur.fetchall()
    conn.close()

    need = kept = 0
    lost = []
    for ab, mj in rows:
        if not ab:
            continue
        for m in (mj or []):
            if not isinstance(m, dict):
                continue
            v = str(m.get("value") or "")
            n = NUM.search(v)
            if not n:
                continue
            hit = None
            for mm in re.finditer(re.escape(n.group(0)), ab):
                if EXP_AFTER.match(ab[mm.end():mm.end() + 20]):
                    hit = mm
                    break
            if hit is None:
                continue
            need += 1
            if VAL_EXP.search(v + str(m.get("unit") or "")):
                kept += 1
            elif len(lost) < args.show:
                lost.append((m.get("name"), v, m.get("unit"),
                             ab[max(0, hit.start() - 30):hit.end() + 25].replace("\n", " ")))

    print(f"원문에서 ×10ⁿ이 붙어 있던 지표: {need:,}개")
    print(f"추출물이 지수를 보존한 것:      {kept:,}개 = {kept / need if need else 0:.1%}")
    print(f"(손실 {need - kept:,}개는 상한 — 같은 숫자가 초록 다른 자리에 나오면 오탐)\n")
    for name, v, u, ctx in lost:
        print(f"  {name} = {v}{u or ''}\n      원문: …{ctx.strip()}…")


if __name__ == "__main__":
    main()
