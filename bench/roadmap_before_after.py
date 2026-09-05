#!/usr/bin/env python3
"""로드맵 점검 파이프라인 전후 비교 — 한 표 방식 대 행 단위 방식.

**운영 코드를 그대로 부른다**(reducer.judge_goals / _assemble_roadmap_report).
DB는 건드리지 않는다 — 기존 보고서는 백업본에서 읽어 비교만 한다.

    DATABASE_URL=postgresql://perfrev:perfrev@localhost:5403/perfrev \\
      PYTHONPATH=backend backend/.venv/bin/python bench/roadmap_before_after.py
"""
import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.database import SessionLocal                      # noqa: E402
from app.models.field import Field, Roadmap, Subfield      # noqa: E402
from app.services import reducer                           # noqa: E402
from app.services.roadmap_parse import parse_goals         # noqa: E402

VERDICTS = ["관련 연구 확인", "부분 관련", "데이터 없음", "분석 범위 밖"]


def verdicts_from_md(md: str) -> dict[int, str]:
    """옛 보고서(한 표 방식)의 표에서 행 순서대로 판정을 뽑는다."""
    out, i, in_tbl = {}, 0, False
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            in_tbl = False
            continue
        if re.match(r"^\|[\s:|-]+\|$", s):
            in_tbl = True
            continue
        if not in_tbl:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        hit = [v for v in VERDICTS if any(v in c for c in cells)]
        i += 1
        if len(hit) == 1:
            out[i] = hit[0]
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="반도체")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--runs", type=int, default=2)
    a = ap.parse_args()

    db = SessionLocal()
    field = db.query(Field).filter(Field.name.like(a.field + "%")).one()
    roadmap = db.query(Roadmap).filter(Roadmap.field_id == field.id).one()
    reports = reducer.collect_subfield_reports(db, field.id, a.year)
    present = [n for n, _ in reports]
    all_names = [s.name for s in db.query(Subfield).filter(Subfield.field_id == field.id)]
    missing = [n for n in all_names if n not in present]
    goals = parse_goals(roadmap.content_md)
    context = "\n\n".join(f"## {n}\n{md}" for n, md in reports)

    print(f"{field.name} {a.year} — 목표 {len(goals)}행 · 보고서 {len(reports)}/{len(all_names)}건")
    print(f"  판정 척도 {'4지' if missing else '3지'}"
          + (f" (미대조: {', '.join(missing)})" if missing else " ← 보고서 전부 있음"))

    runs = []
    for k in range(a.runs):
        t0 = time.monotonic()
        judged = await reducer.judge_goals(goals, context, present, missing)
        runs.append({j["id"]: j["판정"] for j in judged})
        fail = sum(1 for j in judged if not j["판정"])
        print(f"  행 단위 {k+1}회차 — {len(judged)-fail}/{len(goals)}행 "
              f"{time.monotonic()-t0:.0f}s", flush=True)
        if k == 0:
            first = judged

    # 서술 절까지 붙여 실제 보고서를 한 번 만들어 본다(조립이 깨지지 않는지).
    narrative = await reducer.gemini_sync.generate(
        reducer.ROADMAP_NARRATIVE_INSTRUCTION,
        "\n".join(f"{j['id']}. [{j['중점기술']} · {j['단계']}] {j['목표']}\n"
                  f"   → {j['판정'] or '판정 실패'} · {j['근거']}" for j in first),
        thinking=reducer.settings.thinking_reduce,
    )
    new_md = reducer._assemble_roadmap_report(first, present, missing, narrative)

    old = json.loads((REPO / "bench/results/roadmap-checks-백업-한표방식.json").read_text())
    old_md = next(x["report_md"] for x in old
                  if x["field_id"] == field.id and x["year"] == a.year)
    old_v = verdicts_from_md(old_md)

    def agree(x, y):
        both = [i for i in x if i in y and x[i] and y[i]]
        return sum(1 for i in both if x[i] == y[i]), len(both)

    out = {"분야": field.name, "연도": a.year, "목표 행": len(goals),
           "판정 척도": "4지" if missing else "3지"}
    print("\n── 재현성 ────────────────────────────────────")
    if len(runs) >= 2:
        c, n = agree(runs[0], runs[1])
        out["행 단위 자기 일치"] = {"일치": f"{c}/{n}", "비율": round(c / n, 3)}
        print(f"  행 단위(신규)  {c}/{n} = {c/n:.3f}")
    print("  한 표(기존)    0.585  ← bench/results/onetable-vs-perrow-반도체-2026.json")

    print("\n── 판정 분포 ─────────────────────────────────")
    dn, do = Counter(v for v in runs[0].values() if v), Counter(old_v.values())
    out["신규 분포"], out["기존 분포"] = dict(dn), dict(do)
    print(f"  {'판정':<14}{'기존(한 표)':>12}{'신규(행 단위)':>14}")
    for v in VERDICTS:
        if do.get(v) or dn.get(v):
            print(f"  {v:<14}{do.get(v,0):>12}{dn.get(v,0):>14}")

    c, n = agree(old_v, runs[0])
    out["기존↔신규 일치"] = {"일치": f"{c}/{n}", "비율": round(c / n, 3) if n else None}
    print(f"\n── 기존 보고서와의 일치 ──────────────────────\n  {c}/{n} = {c/n:.3f}")

    out["신규 보고서 길이"] = len(new_md)
    out["기존 보고서 길이"] = len(old_md)
    out["신규 보고서"] = new_md
    p = REPO / "bench/results" / f"roadmap-before-after-{a.field}-{a.year}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n보고서 길이 {len(old_md):,} → {len(new_md):,}자\n→ {p}")
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
