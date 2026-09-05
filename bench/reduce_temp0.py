#!/usr/bin/env python3
"""reduce가 temperature 0에서 재현되는가 — ⑥ 착수 첫 실험.

운영 reduce는 temperature 미지정(=1.0)이다(`gemini_sync.generate`는 schema 모드에만 0을
박는다). 6.8-c·⑤의 변동은 그 조건에서 잰 것. 여기서 같은 입력을 {기본, 0} 각 2회 만들어
① 텍스트 동일 여부 ② 유사도(difflib) ③ 수치 개수 차이를 잰다. 0에서 동일해지면 한 줄
수정으로 끝나고, 아니면 5.3.3절과 같은 기제(긴 출력)라 구조를 바꿔야 한다.
"""
import asyncio, difflib, json, sys, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(REPO / "backend"))
import importlib.util
sp = importlib.util.spec_from_file_location("rn", REPO / "bench/reduce_numeric_ab.py")
rn = importlib.util.module_from_spec(sp); sp.loader.exec_module(rn)
from app.database import SessionLocal
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import Field, Subfield
from app.models.paper import Paper, PaperExtraction
from app.prompts import REDUCE_INSTRUCTION
from app.services import mapper, reducer
from google import genai
from google.genai import types

def inputs(db, field_id):
    rows = (db.query(Subfield, Analysis).join(Analysis, Analysis.subfield_id == Subfield.id)
            .filter(Subfield.field_id == field_id, Analysis.year == 2026, Analysis.country == "KR",
                    Analysis.status == "done").order_by(Subfield.name).all())
    for sub, ana in rows:
        keys = [k for (k,) in db.query(Paper.paper_key).join(AnalysisPaper, AnalysisPaper.paper_id == Paper.id)
                .filter(AnalysisPaper.analysis_id == ana.id).all()]
        ex = db.query(PaperExtraction).filter(PaperExtraction.paper_key.in_(keys),
                                              PaperExtraction.model_ver == mapper.model_ver()).all()
        papers = {p.paper_key: p for p in db.query(Paper).filter(Paper.paper_key.in_(keys)).all()}
        if ex: yield sub.name, f"[세부기술: {sub.name} / 2026 / KR]\n" + reducer.format_extractions(ex, papers)

ARMS = {
    "기본(1.0)":        {},
    "temp0":            {"temperature": 0},
    "temp0+seed":       {"temperature": 0, "seed": 20260905},
    "temp0+topk1":      {"temperature": 0, "top_k": 1},
    "temp0+topp0":      {"temperature": 0, "top_p": 0.0},
    "temp0+topk1+topp0": {"temperature": 0, "top_k": 1, "top_p": 0.0},
    "temp0+topk1+seed": {"temperature": 0, "top_k": 1, "seed": 20260905},
}

async def gen(client, user, arm, sem):
    cfg = types.GenerateContentConfig(system_instruction=REDUCE_INSTRUCTION,
        thinking_config=types.ThinkingConfig(thinking_level=reducer.settings.thinking_reduce),
        max_output_tokens=reducer.settings.gemini_max_output_tokens,
        service_tier=types.ServiceTier.FLEX, **ARMS[arm])
    async with sem:
        for a in range(4):
            try:
                r = await asyncio.to_thread(client.models.generate_content, model=reducer.settings.reduce_model, contents=user, config=cfg)
                return r.text or ""
            except Exception as e:
                if a == 3: raise
                await asyncio.sleep(2 ** a * 2)

async def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--field", default="반도체")
    ap.add_argument("--arms", default="기본(1.0),temp0,temp0+seed"); args = ap.parse_args()
    db = SessionLocal(); f = db.query(Field).filter(Field.name.like(args.field + "%")).one()
    items = list(inputs(db, f.id)); db.close()
    client = genai.Client(api_key=reducer.settings.gemini_api_key); sem = asyncio.Semaphore(4)
    print(f"세부기술 {len(items)}건 × {{기본, temp0}} × 2회\n")
    res = {}
    for label in [x.strip() for x in args.arms.split(",")]:
        temp = label
        t0 = time.monotonic()
        outs = await asyncio.gather(*[gen(client, u, temp, sem) for _ in (0, 1) for _, u in items])
        n = len(items); r1, r2 = outs[:n], outs[n:]
        ident = sum(a == b for a, b in zip(r1, r2))
        sims = [difflib.SequenceMatcher(None, a, b).ratio() for a, b in zip(r1, r2)]
        d1 = [rn.density(a)[0] for a in r1]; d2 = [rn.density(b)[0] for b in r2]
        res[label] = {"동일": f"{ident}/{n}", "유사도 평균": round(sum(sims)/n, 3), "유사도 최소": round(min(sims), 3),
                      "수치 개수 1회차": sum(d1), "수치 개수 2회차": sum(d2), "표본": [(items[i][0], round(sims[i],3), d1[i], d2[i]) for i in range(n)]}
        print(f"{label:<10} 동일 {ident}/{n} · 유사도 평균 {sum(sims)/n:.3f} (최소 {min(sims):.3f}) · 수치 {sum(d1)}→{sum(d2)}개 · {time.monotonic()-t0:.0f}s")
        for name, s_, a, b in res[label]["표본"]: print(f"    {name[:20]:<20} 유사도 {s_:.3f}  수치 {a:>2}→{b:<2}")
    p = REPO / f"bench/results/reduce-temp0-{args.field}-2026.json"
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    old.update(res); res = old
    p.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"); print(f"\n→ {p}")
asyncio.run(main())
