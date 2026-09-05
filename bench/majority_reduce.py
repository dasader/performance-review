#!/usr/bin/env python3
"""소규모 세부기술 다수결 생성 — 갈리는 곳만 3회 만들어 다수결로 고르면 재현되는가.

⑦-2까지의 결론: 파라미터·모델로 reduce 재현은 80~90%가 상한이고, 갈리는 곳은 늘 같은
소규모(≤110편) 세부기술 2~3곳이다. 그렇다면 그곳만 K=3회 생성해 **다수결**(2개 이상 동일한
것, 없으면 유사도 중앙값 medoid)로 고르면 구조를 바꾸지 않고 재현 확률을 올릴 수 있다.

측정: 독립 시행 T=3회. 시행마다 K=3 생성 → 승자 선택. **승자가 시행 간에 동일한가**가
답이다(쌍 3개). 대조군은 시행마다 첫 생성 1개(=현행 단일 생성)의 쌍별 동일률.

    PYTHONPATH=backend backend/.venv/bin/python bench/majority_reduce.py  (--paper-max 110 --k 3 --trials 3)
"""
import argparse, asyncio, difflib, importlib.util, json, sys, time
from collections import Counter
from itertools import combinations
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(REPO / "backend"))
sp = importlib.util.spec_from_file_location("rt", REPO / "bench/reduce_temp0.py")
rt = importlib.util.module_from_spec(sp); sp.loader.exec_module(rt)
from app.database import SessionLocal
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.services import reducer
from google import genai

def pick(cands):
    """다수결: 2개 이상 바이트 동일이면 그것. 전부 다르면 다른 것들과의 평균 유사도가 최대인 medoid."""
    c = Counter(cands)
    top, n = c.most_common(1)[0]
    if n >= 2: return top, f"동일 {n}/{len(cands)}"
    sims = [sum(difflib.SequenceMatcher(None, a, b).ratio() for b in cands if b is not a) / (len(cands) - 1) for a in cands]
    return cands[max(range(len(cands)), key=lambda i: sims[i])], f"medoid(유사도 {max(sims):.2f})"

async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--field", default="반도체")
    ap.add_argument("--paper-max", type=int, default=110); ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--trials", type=int, default=3); a = ap.parse_args()
    rt.MODEL = reducer.settings.reduce_model
    db = SessionLocal(); f = db.query(Field).filter(Field.name.like(a.field + "%")).one()
    counts = {s.name: an.analyzed_count for s, an in db.query(Subfield, Analysis).join(Analysis, Analysis.subfield_id == Subfield.id)
              .filter(Subfield.field_id == f.id, Analysis.year == 2026, Analysis.country == "KR")}
    items = [(n, u) for n, u in rt.inputs(db, f.id) if counts.get(n, 0) <= a.paper_max]; db.close()
    client = genai.Client(api_key=reducer.settings.gemini_api_key); sem = asyncio.Semaphore(4)
    print(f"모델 {rt.MODEL} · 소규모(≤{a.paper_max}편) 세부기술 {len(items)}건: " + ", ".join(f"{n}({counts[n]})" for n, _ in items))
    print(f"K={a.k} 생성 × T={a.trials} 시행 = {len(items)*a.k*a.trials}콜\n")

    winners = {n: [] for n, _ in items}; firsts = {n: [] for n, _ in items}; how = {n: [] for n, _ in items}; texts = {n: [] for n, _ in items}
    for t in range(a.trials):
        t0 = time.monotonic()
        outs = await asyncio.gather(*[rt.gen(client, u, "temp0+topk1+seed", sem) for _, u in items for _ in range(a.k)])
        for i, (n, _) in enumerate(items):
            cands = outs[i*a.k:(i+1)*a.k]; w, h = pick(cands)
            winners[n].append(w); firsts[n].append(cands[0]); how[n].append(h); texts[n].append(cands)
        print(f"시행 {t+1}  {time.monotonic()-t0:.0f}s  " + " · ".join(f"{n[:6]}:{how[n][-1]}" for n, _ in items), flush=True)

    print("\n── 시행 간 승자 동일 (쌍 기준) ──────────────────")
    pairs = list(combinations(range(a.trials), 2)); tot_w = tot_s = 0
    print(f"  {'세부기술':<22}{'다수결 승자':>12}{'단일 생성':>12}")
    for n, _ in items:
        w = sum(winners[n][i] == winners[n][j] for i, j in pairs); s_ = sum(firsts[n][i] == firsts[n][j] for i, j in pairs)
        tot_w += w; tot_s += s_
        print(f"  {n[:20]:<22}{w:>7}/{len(pairs)}{s_:>10}/{len(pairs)}")
    print(f"  {'합계':<22}{tot_w:>7}/{len(pairs)*len(items)} = {tot_w/(len(pairs)*len(items)):.2f}"
          f"{tot_s:>10}/{len(pairs)*len(items)} = {tot_s/(len(pairs)*len(items)):.2f}")
    kinds = Counter(h.split('(')[0] for n in how for h in how[n])
    print(f"\n  선택 방식 분포: {dict(kinds)}   (medoid = 3개 전부 다른 경우)")
    out = {"모델": rt.MODEL, "K": a.k, "T": a.trials, "세부기술": [n for n, _ in items],
           "다수결 승자 동일": f"{tot_w}/{len(pairs)*len(items)}", "단일 생성 동일": f"{tot_s}/{len(pairs)*len(items)}",
           "선택 방식": {n: how[n] for n in how}, "본문": texts}
    p = REPO / f"bench/results/majority-reduce-{a.field}-2026.json"; p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"); print(f"\n→ {p}")
asyncio.run(main())
