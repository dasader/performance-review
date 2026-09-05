#!/usr/bin/env python3
"""⑤ reduce 비재현이 판정에 얼마나 전파되는가.

6.8-c 부수 발견: 같은 지시문·같은 입력으로 만든 세부기술 보고서가 실행마다 다르다
(수치 37 vs 60개). 판정(절차)은 1.000이지만 **그 입력이 흔들린다.** 여기서는 보고서를
같은 조건으로 두 번 만들고, 각각에 운영과 같은 절차 판정을 걸어 판정이 얼마나 갈리는지 잰다.
판정 자체는 결정적이므로 갈린 만큼이 곧 reduce 변동의 전파량이다.

    DATABASE_URL=… PYTHONPATH=backend backend/.venv/bin/python bench/reduce_variance_propagation.py
"""
import asyncio, importlib.util, json, sys, time
from collections import Counter
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
def _load(name):
    sp = importlib.util.spec_from_file_location(name, REPO / "bench" / f"{name}.py")
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m
rn = _load("reduce_numeric_ab"); jp = _load("judge_prompt_ab")
from app.database import SessionLocal                  # noqa: E402
from app.models.field import Field, Roadmap             # noqa: E402
from app.prompts import REDUCE_INSTRUCTION              # noqa: E402
from app.services.roadmap_parse import parse_goals      # noqa: E402

async def main():
    db = SessionLocal()
    field = db.query(Field).filter(Field.name.like("반도체%")).one()
    goals = parse_goals(db.query(Roadmap).filter(Roadmap.field_id == field.id).one().content_md)
    sets = []
    for k in (1, 2):
        print(f"── 보고서 세트 {k} 생성 ──", flush=True)
        sets.append(await rn.build_reports(db, field.id, 2026, "KR", REDUCE_INSTRUCTION, f"세트{k}"))
    db.close()
    dens = [rn.density("\n".join(md for _, md in s)) for s in sets]
    print(f"\n수치 밀도  세트1 {dens[0][1]:.2f}/천자 ({dens[0][0]}개) · 세트2 {dens[1][1]:.2f}/천자 ({dens[1][0]}개)")
    # 어느 세부기술 보고서가 갈렸는지 — 이것이 없으면 판정 전파의 원인을 못 짚는다(⑦ 1차 실측의 교훈)
    same_rep = [n for (n, a), (_, b) in zip(sets[0], sets[1]) if a == b]
    print(f"보고서 동일 {len(same_rep)}/{len(sets[0])} · 갈림: "
          + ", ".join(n for (n, a), (_, b) in zip(sets[0], sets[1]) if a != b))

    ver = []
    for k, reps in enumerate(sets, 1):
        ctx = "\n\n".join(f"## {n}\n{md}" for n, md in reps)
        t0 = time.monotonic()
        full = await jp.judge_v2(goals, ctx, 6)
        ver.append({i: d["판정"] for i, d in full.items() if d})
        print(f"판정 세트{k}  {len(ver[-1])}/{len(goals)}행 {time.monotonic()-t0:.0f}s", flush=True)

    a, b = ver
    both = [i for i in a if i in b]; same = sum(a[i] == b[i] for i in both)
    print(f"\n── 판정 일치 (세트1 ↔ 세트2) ──\n  {same}/{len(both)} = {same/len(both):.3f}")
    print(f"  분포 세트1 {dict(Counter(a.values()))}\n  분포 세트2 {dict(Counter(b.values()))}")
    ch = Counter(f"{a[i]} → {b[i]}" for i in both if a[i] != b[i])
    for k2, v in ch.most_common(): print(f"  {v:>2}건 {k2}")
    out = {"수치 밀도": dens, "판정 일치": f"{same}/{len(both)}",
           "보고서 동일": f"{len(same_rep)}/{len(sets[0])}", "보고서 갈림": [n for (n, a), (_, b) in zip(sets[0], sets[1]) if a != b],
           "본문": {n: [a, b] for (n, a), (_, b) in zip(sets[0], sets[1])}, "세트1": {str(i): v for i, v in a.items()},
           "세트2": {str(i): v for i, v in b.items()}, "이동": dict(ch)}
    p = REPO / "bench/results/reduce-variance-propagation-반도체-2026.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"); print(f"→ {p}")
asyncio.run(main())
