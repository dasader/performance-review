#!/usr/bin/env python3
"""산문 퇴화 검사 — 결정적 파라미터(temp0·top_k=1·seed)가 세부기술 보고서 품질을 해치는가.

자매 문서 5.6.5절은 temperature 0의 부작용을 **추출(218~365토큰)** 에서만 쟀다. 3k 토큰
국문 산문에서는 미측정이라, 운영 reduce에 파라미터를 박기 전에 여기서 잰다. 입력은
`reduce_temp0.py`가 저장한 본문(기본 vs 결정적, 각 2회 × 세부기술 10건). LLM 호출 없음.

지표: 3-gram 반복률(같은 3어절이 다시 나오는 비율) · 길이 · 문장 수·평균 문장 길이 ·
어휘 다양도(고유 어절/전체) · 수치 밀도 · 논문 인용 수(괄호 안 제목) · 최장 반복 구절.
"""
import json, re, sys
from collections import Counter
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
d = json.loads((REPO / "bench/results/reduce-temp0-반도체-2026.json").read_text(encoding="utf-8"))
NUM = re.compile(r"\d[\d,.]*\s*(nm|㎚|μm|um|mm|fJ|pJ|mJ|ns|ps|ms|%|배|단|층|TOPS|W/mK|mA|μA|A|V|K|GHz|MHz|nF|pF|Ω|Wh/kg|mAh|cd/m2|㎠|건|편)")
CITE = re.compile(r"\(([^()]{15,200})\)")

def metrics(t):
    w = t.split(); n = len(w)
    tri = Counter(tuple(w[i:i+3]) for i in range(max(0, n-2)))
    rep = sum(c-1 for c in tri.values()) / max(1, len(tri))
    sents = [x for x in re.split(r"(?<=[.。!?])\s+", t) if x.strip()]
    longest = max((c for c in tri.values()), default=0)
    return {"자수": len(t), "어절": n, "3gram 반복률": rep, "문장 수": len(sents),
            "평균 문장 길이": len(t)/max(1,len(sents)), "어휘 다양도": len(set(w))/max(1,n),
            "수치/천자": len(NUM.findall(t))/max(1,len(t))*1000, "인용 수": len(CITE.findall(t)),
            "최다 반복 3gram": longest}

arms = [a for a in sys.argv[1:]] or ["기본(1.0)", "temp0+topk1+seed"]
rows = {}
for arm in arms:
    if arm not in d or "본문" not in d[arm]:
        print(f"!{arm}: 본문 없음 — reduce_temp0.py를 본문 저장 버전으로 다시 돌려야 함"); continue
    ms = [metrics(t) for pair in d[arm]["본문"].values() for t in pair]
    rows[arm] = {k: sum(m[k] for m in ms)/len(ms) for k in ms[0]}
keys = list(next(iter(rows.values())))
print(f"{'지표':<16}" + "".join(f"{a:>22}" for a in rows) + f"{'변화':>10}")
for k in keys:
    v = [rows[a][k] for a in rows]
    ch = (v[-1]-v[0])/v[0]*100 if v[0] else 0
    fmt = (lambda x: f"{x:.3f}") if k in ("3gram 반복률","어휘 다양도") else (lambda x: f"{x:,.1f}")
    print(f"{k:<16}" + "".join(f"{fmt(x):>22}" for x in v) + f"{ch:>+9.1f}%")
out = REPO / "bench/results/prose-degeneration-반도체-2026.json"
out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"); print(f"\n→ {out}")
