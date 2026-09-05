#!/usr/bin/env python3
"""입력 구체성 실험 ② — 세부기술 보고서에 **수치 보존을 요구**하면 판정이 나아지는가.

6.8-b(논문 전량 주입)가 실패한 뒤의 다음 수. 그 실험의 교훈은 **"컨텍스트를 늘리는 것이
정보를 늘리는 것은 아니다"**였다. 그래서 같은 크기에 더 좋은 정보를 담는 쪽을 시도한다.

현행 `REDUCE_INSTRUCTION`은 수치를 **허용**할 뿐 **요구하지 않는다**
("수치는 서술 안에서 근거로 인용할 때만 쓰세요"). 로드맵 목표는 *10나노 이하급* ·
*1,000단 NAND* · *10fJ* 같은 사양이라, 보고서가 "성능이 향상되었다"로 뭉개면 대조가
성립하지 않는다.

**두 보고서를 지금 같은 코드로 함께 만든다.** DB에 저장된 보고서를 대조군으로 쓰면
생성 시점(8/3)과 모델 상태 차이가 섞인다. DB는 건드리지 않는다.

    PYTHONPATH=backend backend/.venv/bin/python bench/reduce_numeric_ab.py --limit 8
"""
import argparse
import asyncio
import importlib.util
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

_spec = importlib.util.spec_from_file_location("b2", REPO / "bench" / "context_b2_ab.py")
b2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b2)

from app.database import SessionLocal                        # noqa: E402
from app.models.analysis import Analysis, AnalysisPaper      # noqa: E402
from app.models.field import Field, Roadmap, Subfield        # noqa: E402
from app.models.paper import Paper, PaperExtraction          # noqa: E402
from app.prompts import REDUCE_INSTRUCTION                   # noqa: E402
from app.services import mapper, reducer                     # noqa: E402
from app.services.roadmap_parse import parse_goals           # noqa: E402

# 규칙 절에 한 항목을 더한다. 다른 곳은 손대지 않는다 — 무엇이 효과를 냈는지 알기 위해서.
NUMERIC_RULE = """- **수치를 뭉개지 마세요.** 목록에 수치·단위·조건이 있으면 서술 안에
  그대로 남기세요. "성능이 향상되었다"가 아니라 "스위칭 속도를 10ns에서 1ns로 줄였다"처럼
  씁니다. 공정 노드(nm), 적층 단수, 에너지(fJ·pJ), 효율(%), 이동도, 피치 등 **로드맵이
  요구하는 사양에 해당하는 수치는 특히 빠뜨리지 마세요.** 목록에 없는 수치를 지어내는
  것은 여전히 금지입니다."""


def numeric_variant() -> str:
    anchor = "- 제공된 성과 목록에 없는 내용을 만들어내지 마세요."
    assert REDUCE_INSTRUCTION.count(anchor) == 1
    return REDUCE_INSTRUCTION.replace(anchor, NUMERIC_RULE + "\n" + anchor)


_NUM_DENSITY = re.compile(
    r"\d[\d,.]*\s*(nm|㎚|μm|um|mm|fJ|pJ|mJ|ns|ps|ms|%|배|단|층|TOPS|W/mK|mA|μA|A|V|K|"
    r"GHz|MHz|nF|pF|Ω|Wh/kg|mAh|cd/m2|㎠|건|편)")


def density(md: str) -> tuple[int, float]:
    """보고서 1천 자당 '수치+단위' 개수. 프롬프트 변경이 실제로 먹혔는지 직접 본다."""
    n = len(_NUM_DENSITY.findall(md))
    return n, n / max(len(md), 1) * 1000


async def build_reports(db, field_id, year, country, instruction, label):
    """같은 코드 경로로 보고서를 새로 만든다. DB에는 쓰지 않는다."""
    rows = (db.query(Subfield, Analysis)
            .join(Analysis, Analysis.subfield_id == Subfield.id)
            .filter(Subfield.field_id == field_id, Analysis.year == year,
                    Analysis.country == country, Analysis.status == "done")
            .order_by(Subfield.name).all())
    out = []
    for sub, ana in rows:
        keys = [k for (k,) in db.query(Paper.paper_key)
                .join(AnalysisPaper, AnalysisPaper.paper_id == Paper.id)
                .filter(AnalysisPaper.analysis_id == ana.id).all()]
        if not keys:
            continue
        ex = (db.query(PaperExtraction)
              .filter(PaperExtraction.paper_key.in_(keys),
                      PaperExtraction.model_ver == mapper.model_ver()).all())
        papers = {p.paper_key: p for p in
                  db.query(Paper).filter(Paper.paper_key.in_(keys)).all()}
        if not ex:
            continue
        body = reducer.format_extractions(ex, papers)
        header = f"[세부기술: {sub.name} / {year} / {country}]\n"
        t0 = time.monotonic()
        md = await reducer.gemini_sync.generate(
            instruction, header + body, thinking=reducer.settings.thinking_reduce)
        n, d = density(md)
        print(f"    {label} · {sub.name[:22]:<22} {len(ex):>4}건 → {len(md):,}자 "
              f"수치 {n}개({d:.1f}/천자) {time.monotonic()-t0:.0f}s", flush=True)
        out.append((sub.name, md))
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="반도체")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--country", default="KR")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()

    db = SessionLocal()
    field = db.query(Field).filter(Field.name.like(a.field + "%")).one()
    goals = parse_goals(db.query(Roadmap)
                        .filter(Roadmap.field_id == field.id).one().content_md)
    if a.limit:
        goals = goals[:a.limit]
    print(f"{field.name} {a.year} {a.country} — 목표 {len(goals)}행\n")

    arms = {}
    print("── 보고서 생성 (같은 코드, 지시문만 다름) ─────────────")
    for label, instr in (("현행", REDUCE_INSTRUCTION), ("수치보존", numeric_variant())):
        arms[label] = await build_reports(db, field.id, a.year, a.country, instr, label)
    db.close()

    out = {"분야": field.name, "연도": a.year, "목표 행": len(goals)}
    print("\n── ⓪ 보고서 자체의 수치 밀도 ─────────────────")
    for label, reps in arms.items():
        allmd = "\n".join(md for _, md in reps)
        n, d = density(allmd)
        out[label] = {"보고서 자수": len(allmd), "수치 개수": n, "수치/천자": round(d, 2)}
        print(f"  {label:<8} {len(reps)}건 · {len(allmd):,}자 · 수치 {n}개 "
              f"({d:.2f}/천자)")

    got = {}
    print("\n── 판정 ──────────────────────────────────────")
    for label, reps in arms.items():
        ctx = "\n\n".join(f"## {n}\n{md}" for n, md in reps)
        runs, reasons = [], None
        for k in range(a.runs):
            t0 = time.monotonic()
            v, w, _ = await b2.judge(goals, ctx, a.concurrency)
            runs.append(v)
            reasons = reasons or w
            print(f"  {label:<8} {k+1}회차 {len(v)}/{len(goals)}행 "
                  f"{time.monotonic()-t0:.0f}s", flush=True)
        got[label] = (runs, reasons)

    print("\n── ① 자기 일치 ───────────────────────────────")
    for label, (runs, _) in got.items():
        if len(runs) >= 2:
            c, n = b2.agree(runs[0], runs[1])
            out[label]["자기 일치"] = round(c / n, 3) if n else None
            print(f"  {label:<8} {c}/{n} = {c/n:.3f}")

    print("\n── ② 판정 분포 ───────────────────────────────")
    print(f"  {'판정':<14}" + "".join(f"{k:>12}" for k in got))
    for v in b2.VERDICTS:
        print(f"  {v:<14}" + "".join(
            f"{Counter(r[0][0].values())[v]:>12}" for r in got.values()))
    for label, (runs, _) in got.items():
        out[label]["분포"] = dict(Counter(runs[0].values()))

    print("\n── ③ 두 방식 간 일치 ─────────────────────────")
    ks = list(got)
    c, n = b2.agree(got[ks[0]][0][0], got[ks[1]][0][0])
    out["방식 간 일치"] = round(c / n, 3) if n else None
    print(f"  {c}/{n} = {c/n:.3f}")

    print("\n── ④ 근거의 구체성 (수치+단위 포함 비율) ─────")
    for label, (_, reasons) in got.items():
        h, t = b2.concreteness(reasons, goals)
        out[label]["수치 포함 근거"] = f"{h}/{t}"
        print(f"  {label:<8} {h}/{t} = {h/t:.1%}" if t else f"  {label}: —")

    for label, (runs, reasons) in got.items():
        out[label]["원자료"] = [{str(i): v for i, v in r.items()} for r in runs]
        out[label]["근거"] = {str(i): w for i, w in reasons.items()}
    out["수치보존"]["보고서 표본"] = arms["수치보존"][0][1][:1500]
    out["현행"]["보고서 표본"] = arms["현행"][0][1][:1500]
    p = REPO / "bench/results" / f"reduce-numeric-ab-{a.field}-{a.year}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {p}")


if __name__ == "__main__":
    asyncio.run(main())
