#!/usr/bin/env python3
"""(B-1) 세부기술 보고서만 대 (B-1 + B-2) 논문별 추출까지 — 어느 쪽이 나은가.

문제의식: 세부기술 보고서는 **LLM이 논문 수백 건을 이미 한 번 압축한 결과**라
로드맵이 요구하는 구체적 사양(10나노 이하급 · 1,000단 NAND · 10fJ)이 그 과정에서
날아간다. 논문별 추출은 그보다 구체적이다.

**RAG를 쓰지 않는 이유**: 전량이 컨텍스트에 들어간다(반도체 KR 2026 추출 526KB ≈ 13만
토큰, 가장 큰 미래에너지도 33만). 그리고 검색을 끼우면 **검색 실패가 `데이터 없음`에
섞여** "진짜 없음"과 구분되지 않는데, 그 숫자가 이 연구의 주 결과 후보다.

재는 것 넷.
  ① 각 방식의 **자기 일치** — 컨텍스트를 6배로 늘리면 결정성이 깨지는가
  ② 판정 분포 이동 — 근거를 더 찾아 `데이터 없음`이 줄어드는가
  ③ 두 방식 간 일치
  ④ **근거의 구체성** — 근거 문장에 수치·단위가 실제로 들어가는가 (이 실험의 가설)

    PYTHONPATH=backend backend/.venv/bin/python bench/context_b2_ab.py --limit 8
"""
import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from app.services.roadmap_parse import parse_goals   # noqa: E402

MODEL = "gemini-3.1-flash-lite"
VERDICTS = ["관련 연구 확인", "부분 관련", "데이터 없음"]
SCHEMA = {"type": "object",
          "properties": {"판정": {"type": "string", "enum": VERDICTS},
                         "근거": {"type": "string"}},
          "required": ["판정", "근거"]}

SYSTEM = """당신은 국가전략기술 로드맵의 이행 상황을 점검하는 과학기술 분석가입니다.

입력은 두 부분입니다.
(A) 전략기술로드맵의 **기술적 목표 한 개**
(B) 해당 분야에서 실제로 발표된 논문의 분석 자료

(B)의 근거만으로 (A)의 목표가 어디까지 연구되고 있는지 판정하세요.

## 절대 규칙
1. (B)에 근거가 없으면 반드시 `데이터 없음`으로 판정하세요. 추측하거나 일반 상식으로
   채우지 마세요. 근거 없는 목표가 많은 것은 정상이며, 그것을 그대로 드러내는 것이
   이 점검의 목적입니다.
2. 근거 칸에는 (B)의 어느 서술을 보았는지 적으세요. **수치·단위가 (B)에 있으면 그대로
   인용하세요.** `데이터 없음`이면 무엇을 찾았으나 없었는지 적습니다. 한 문장으로 짧게.
3. 논문 성과는 "연구가 진행되고 있다"는 신호일 뿐 목표 달성의 증거가 아닙니다.

## 판정
- `관련 연구 확인` — 목표와 직접 맞닿는 연구 성과가 (B)에 있음
- `부분 관련` — 인접 주제 연구는 있으나 목표가 요구하는 수준·대상과는 어긋남
- `데이터 없음` — (B)에 근거가 없음

JSON 객체 하나만 출력하세요. 한국어로 답하세요."""

_cli = None


def cli():
    global _cli
    from google import genai
    if _cli is None:
        env = dict(l.split("=", 1) for l in (REPO / ".env").read_text().splitlines()
                   if "=" in l and not l.startswith("#"))
        _cli = genai.Client(api_key=env["GEMINI_API_KEY"].strip())
    return _cli


def paper_block(cur, field_id: int, year: int, country: str) -> tuple[str, int]:
    """논문별 추출을 한 줄씩. 제목·요약·지표·개선점만 남긴다(approach는 길이 대비 효용이 낮다)."""
    cur.execute("""
        -- json 타입은 DISTINCT를 못 걸어(등호 연산자 없음) paper_key로 먼저 좁힌다.
        SELECT p.title, e.tech_summary, e.metrics_json, e.improvement
        FROM (
            SELECT DISTINCT p.paper_key
            FROM analyses a
            JOIN subfields s ON s.id = a.subfield_id
            JOIN analysis_papers ap ON ap.analysis_id = a.id
            JOIN papers p ON p.id = ap.paper_id
            WHERE s.field_id = %s AND a.year = %s AND a.country = %s
        ) k
        JOIN papers p ON p.paper_key = k.paper_key
        JOIN paper_extractions e ON e.paper_key = k.paper_key
          AND e.model_ver = 'gemini-3.1-flash-lite/low/v3'
        ORDER BY p.title
    """, (field_id, year, country))
    lines = []
    for title, summary, metrics, improvement in cur.fetchall():
        m = ""
        if metrics:
            parts = [f"{x.get('name','')} {x.get('value','')}{x.get('unit','')}"
                     f"({x.get('target','')})".strip()
                     for x in (metrics if isinstance(metrics, list) else [])][:6]
            m = " | 지표: " + "; ".join(p for p in parts if p.strip("()| ")) if parts else ""
        imp = f" | 개선: {improvement}" if improvement else ""
        lines.append(f"- {title}\n  {summary or ''}{m}{imp}")
    return "\n".join(lines), len(lines)


async def judge(goals, context, conc):
    from google.genai import types
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM, response_mime_type="application/json",
        response_schema=SCHEMA, temperature=0,
        thinking_config=types.ThinkingConfig(thinking_level="high"))
    sem = asyncio.Semaphore(conc)
    tok = [0, 0]

    async def one(g):
        user = (f"# (A) 점검할 기술적 목표\n\n- 중점기술: {g['중점기술']}\n"
                f"- 세부 항목: {g['세부항목']}\n- 단계·구분: {g['단계']}\n"
                f"- 기술적 목표: {g['목표']}\n\n\n# (B) 논문 분석 자료\n\n{context}")
        async with sem:
            for att in range(4):
                try:
                    r = await asyncio.to_thread(cli().models.generate_content,
                                                model=MODEL, contents=user, config=cfg)
                    um = r.usage_metadata
                    tok[0] += um.prompt_token_count or 0
                    tok[1] += (um.candidates_token_count or 0) + (um.thoughts_token_count or 0)
                    d = json.loads(r.text)
                    return g["id"], d["판정"], d.get("근거", "")
                except Exception as e:
                    if att == 3:
                        print(f"    !{g['id']} {str(e)[:70]}", flush=True)
                        return g["id"], None, ""
                    await asyncio.sleep(2 ** att * 2)

    res = await asyncio.gather(*[one(g) for g in goals])
    return ({i: v for i, v, _ in res if v}, {i: w for i, v, w in res if v}, tok)


_NUM = re.compile(r"\d[\d,.]*\s*(nm|㎚|μm|um|fJ|ns|ps|%|배|단|TOPS|W/mK|mA|μA|V|K|GHz|nF|Ω|층|건)")


def _nums(t: str) -> set[str]:
    return {m.group(0).replace(" ", "") for m in _NUM.finditer(t or "")}


def concreteness(reasons: dict[int, str], goals: list[dict] | None = None) -> tuple[int, int]:
    """근거에 **(B)에서 가져온** 수치+단위가 있는가 — 이 실험의 가설이 걸린 지표.

    목표 문구에 있는 수치는 뺀다. 안 빼면 `데이터 없음` 근거("10nm급 DRAM 연구는 없음")가
    목표의 수치를 되풀이한 것까지 "구체적"으로 세어 지표가 오염된다 — 실측(2026-09-05)
    에서 그 오염 때문에 첫 두 실험의 ④가 무의미했다.
    """
    goal_nums = {g["id"]: _nums(g["목표"]) for g in (goals or [])}
    hit = sum(1 for i, t in reasons.items() if _nums(t) - goal_nums.get(i, set()))
    return hit, len(reasons)


def agree(a, b):
    both = [i for i in a if i in b]
    return sum(1 for i in both if a[i] == b[i]), len(both)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="반도체")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--country", default="KR")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    a = ap.parse_args()

    cur = psycopg2.connect(a.dsn).cursor()
    cur.execute("SELECT id, name FROM fields WHERE name LIKE %s", (a.field + "%",))
    fid, fname = cur.fetchone()
    cur.execute("SELECT content_md FROM roadmaps WHERE field_id=%s", (fid,))
    goals = parse_goals(cur.fetchone()[0])
    cur.execute("""SELECT s.name, a.report_md FROM analyses a
                   JOIN subfields s ON s.id=a.subfield_id
                   WHERE s.field_id=%s AND a.year=%s AND a.country=%s AND a.status='done'
                     AND a.report_md IS NOT NULL AND a.report_md<>'' ORDER BY s.name""",
                (fid, a.year, a.country))
    reports = cur.fetchall()
    b1 = "\n\n".join(f"## {n}\n{md}" for n, md in reports)
    papers, npaper = paper_block(cur, fid, a.year, a.country)
    b2 = b1 + f"\n\n\n# 논문별 분석 결과 ({npaper}건)\n\n" + papers
    if a.limit:
        goals = goals[:a.limit]

    print(f"{fname} {a.year} {a.country} — 목표 {len(goals)}행")
    print(f"  B-1 보고서 {len(reports)}건 · {len(b1):,}자")
    print(f"  B-2 논문 {npaper}건 추가 · {len(b2):,}자 ({len(b2)/len(b1):.1f}배)\n")

    out = {"분야": fname, "연도": a.year, "목표 행": len(goals), "논문": npaper,
           "B-1 자수": len(b1), "B-2 자수": len(b2)}
    arms = {"B-1 (보고서만)": b1, "B-2 (보고서+논문)": b2}
    got = {}
    for name, ctx in arms.items():
        runs, reasons, toks = [], None, [0, 0]
        for k in range(a.runs):
            t0 = time.monotonic()
            v, w, tk = await judge(goals, ctx, a.concurrency)
            runs.append(v)
            reasons = reasons or w
            toks = [toks[0] + tk[0], toks[1] + tk[1]]
            print(f"  {name:<20} {k+1}회차 {len(v)}/{len(goals)}행 "
                  f"{time.monotonic()-t0:.0f}s", flush=True)
        got[name] = (runs, reasons)
        cost = toks[0] * 0.125 / 1e6 + toks[1] * 0.75 / 1e6
        out[name] = {"입력 토큰": toks[0], "출력 토큰": toks[1], "Flex USD": round(cost, 3)}

    print("\n── ① 자기 일치 ───────────────────────────────")
    for name, (runs, _) in got.items():
        if len(runs) >= 2:
            c, n = agree(runs[0], runs[1])
            out[name]["자기 일치"] = round(c / n, 3) if n else None
            print(f"  {name:<20} {c}/{n} = {c/n:.3f}")

    print("\n── ② 판정 분포 ───────────────────────────────")
    print(f"  {'판정':<14}" + "".join(f"{k:>22}" for k in got))
    for v in VERDICTS:
        line = "".join(f"{Counter(r[0][0].values())[v]:>22}" for r in got.values())
        print(f"  {v:<14}{line}")
    for name, (runs, _) in got.items():
        out[name]["분포"] = dict(Counter(runs[0].values()))

    print("\n── ③ 두 방식 간 일치 ─────────────────────────")
    ks = list(got)
    c, n = agree(got[ks[0]][0][0], got[ks[1]][0][0])
    out["방식 간 일치"] = round(c / n, 3) if n else None
    print(f"  {c}/{n} = {c/n:.3f}")

    print("\n── ④ 근거의 구체성 (수치+단위 포함 비율) ─────")
    for name, (_, reasons) in got.items():
        h, t = concreteness(reasons, goals)
        out[name]["수치 포함 근거"] = f"{h}/{t}"
        print(f"  {name:<20} {h}/{t} = {h/t:.1%}" if t else f"  {name}: —")

    print("\n── 비용 ─────────────────────────────────────")
    for name in got:
        print(f"  {name:<20} ${out[name]['Flex USD']}  (입력 {out[name]['입력 토큰']:,})")

    p = REPO / "bench/results" / f"context-b2-ab-{a.field}-{a.year}.json"
    for name, (runs, reasons) in got.items():
        out[name]["원자료"] = [{str(i): v for i, v in r.items()} for r in runs]
        out[name]["근거"] = {str(i): w for i, w in reasons.items()}   # 전문 — 사후 재계산용
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {p}")


if __name__ == "__main__":
    asyncio.run(main())
