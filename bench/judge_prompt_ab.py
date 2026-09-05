#!/usr/bin/env python3
"""판정 프롬프트 A/B — 자유 판정(현행) 대 절차 판정(개선).

입력 실험 두 건(6.8-b 논문 전량, 6.8-c 수치 보존)이 모두 판정 분포를 못 움직였다.
운영 판정 65건의 근거를 해부해 보니 병목은 입력이 아니라 **판정 단계**였다.

  ① (B)의 수치를 근거로 옮긴 행이 65건 중 2건 — "인용하라"는 지시가 있는데도
  ② 복합 목표(하위 목표 2개 이상)가 52/65행인데 "일부만 근거 있으면?" 규칙이 없음
  ③ 같은 근거 구조("X는 있으나 Y는 없음")에 `데이터 없음`과 `부분 관련`이 임의로 갈림
     — `데이터 없음` 41건 중 21건은 목표 핵심어가 (B)에 있는데도 그렇게 판정됨

개선안은 판정을 **절차**로 바꾼다: 목표 분해 → 하위 목표별 (B) 발췌 → 하위 판정 →
행 판정은 **규칙**으로 도출. 근거가 먼저 나오고 판정이 뒤따르므로 사후 합리화가 어렵다.
발췌를 "(B)의 문장 그대로"로 강제하면 **모델이 지어냈는지 코드로 검증**할 수 있다(⑤).

    PYTHONPATH=backend backend/.venv/bin/python bench/judge_prompt_ab.py --limit 6 --runs 1
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

import psycopg2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from app.services.roadmap_parse import parse_goals   # noqa: E402

_spec = importlib.util.spec_from_file_location("b2", REPO / "bench" / "context_b2_ab.py")
b2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b2)

MODEL = "gemini-3.1-flash-lite"
VERDICTS = ["관련 연구 확인", "부분 관련", "데이터 없음"]
SUB = ["직접", "인접", "없음"]

SYSTEM_V2 = """당신은 국가전략기술 로드맵의 이행 상황을 점검하는 과학기술 분석가입니다.

입력은 두 부분입니다.
(A) 전략기술로드맵의 **기술적 목표 한 개**
(B) 해당 분야에서 실제로 발표된 논문을 분석한 세부기술별 성과 보고서

## 절차 — 이 순서를 지키세요

**1. 목표 분해.** (A)에 서로 다른 개발 항목이 여럿이면(쉼표·마침표·가운뎃점으로 나뉜
"X 개발, Y 확보" 꼴) 하위 목표로 나누세요. 하나뿐이면 그대로 하나입니다.

**2. 근거 발췌.** 하위 목표마다 (B)에서 관련 문장을 찾아 **한 글자도 바꾸지 말고 그대로**
옮기세요. 어느 세부기술 보고서에서 가져왔는지 적으세요. 그 문장에 수치·단위가 있으면
`수치` 칸에 따로 옮기세요(예: "10ns → 1ns", "4nm", "1,000단"). 관련 문장이 없으면 발췌
목록을 비워 두세요 — **지어내지 마세요.**

**3. 하위 목표 판정.** 발췌를 근거로 셋 중 하나:
- `직접` — 발췌 문장이 하위 목표의 **대상과 수준을 그대로** 다룸
- `인접` — 하위 목표의 핵심어(소재·소자·공정·구조명)가 (B)에 있으나 **요구 수준·대상이 다름**
- `없음` — 핵심어조차 (B)에 없음. 발췌가 비어 있으면 반드시 이것

**4. 행 판정 — 규칙으로 정합니다. 재량으로 바꾸지 마세요.**
- `직접`이 하나라도 있으면 → `관련 연구 확인`
- `직접`은 없고 `인접`이 하나라도 있으면 → `부분 관련`
- 전부 `없음` → `데이터 없음`

## 절대 규칙
- 발췌는 (B)에 실제로 있는 문장만. 요약하거나 바꿔 쓰지 마세요.
- 논문 성과는 "연구가 진행되고 있다"는 신호일 뿐 목표 달성의 증거가 아닙니다.
  달성률 같은 숫자를 만들어내지 마세요.
- 근거 없는 목표가 많은 것은 정상이며, 그것을 드러내는 것이 이 점검의 목적입니다.

JSON 객체 하나만 출력하세요. 한국어로 답하세요."""

SCHEMA_V2 = {
    "type": "object",
    "properties": {
        "하위목표": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "항목": {"type": "string"},
                    "발췌": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "세부기술": {"type": "string"},
                                "문장": {"type": "string"},
                                "수치": {"type": "string"},
                            },
                            "required": ["세부기술", "문장"],
                        },
                    },
                    "판정": {"type": "string", "enum": SUB},
                },
                "required": ["항목", "발췌", "판정"],
            },
        },
        "판정": {"type": "string", "enum": VERDICTS},
    },
    "required": ["하위목표", "판정"],
}


def rule_verdict(subs: list[dict]) -> str:
    labs = [s.get("판정") for s in subs]
    if "직접" in labs:
        return "관련 연구 확인"
    if "인접" in labs:
        return "부분 관련"
    return "데이터 없음"


_WS = re.compile(r"\s+")


def grounded(sentence: str, B: str) -> bool:
    """발췌가 (B)에 실제로 있는가. 공백 차이는 무시하고, 20자 이상 연속 일치면 인정."""
    s = _WS.sub("", sentence)
    b = _WS.sub("", B)
    if len(s) < 12:
        return s in b
    return any(s[i:i + 20] in b for i in range(0, max(1, len(s) - 20), 10))


_cli = None


def cli():
    global _cli
    from google import genai
    if _cli is None:
        env = dict(l.split("=", 1) for l in (REPO / ".env").read_text().splitlines()
                   if "=" in l and not l.startswith("#"))
        _cli = genai.Client(api_key=env["GEMINI_API_KEY"].strip())
    return _cli


async def judge_v2(goals, context, conc):
    from google.genai import types
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_V2, response_mime_type="application/json",
        response_schema=SCHEMA_V2, temperature=0,
        thinking_config=types.ThinkingConfig(thinking_level="high"),
        max_output_tokens=8192)
    sem = asyncio.Semaphore(conc)

    async def one(g):
        user = (f"# (A) 점검할 기술적 목표\n\n- 중점기술: {g['중점기술']}\n"
                f"- 세부 항목: {g['세부항목']}\n- 단계·구분: {g['단계']}\n"
                f"- 기술적 목표: {g['목표']}\n\n\n# (B) 논문 분석 기반 세부기술별 성과 보고서\n\n{context}")
        async with sem:
            for att in range(4):
                try:
                    r = await asyncio.to_thread(cli().models.generate_content,
                                                model=MODEL, contents=user, config=cfg)
                    d = json.loads(r.text)
                    return g["id"], d
                except Exception as e:
                    if att == 3:
                        print(f"    !{g['id']} {str(e)[:70]}", flush=True)
                        return g["id"], None
                    await asyncio.sleep(2 ** att * 2)

    return dict(await asyncio.gather(*[one(g) for g in goals]))


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
    B = "\n\n".join(f"## {n}\n{md}" for n, md in reports)
    if a.limit:
        goals = goals[:a.limit]
    print(f"{fname} {a.year} {a.country} — 목표 {len(goals)}행 · 보고서 {len(reports)}건\n")

    out = {"분야": fname, "연도": a.year, "목표 행": len(goals)}

    # ── 현행(자유 판정) ──
    v1_runs, v1_reasons = [], None
    for k in range(a.runs):
        t0 = time.monotonic()
        v, w, _ = await b2.judge(goals, B, a.concurrency)
        v1_runs.append(v); v1_reasons = v1_reasons or w
        print(f"  현행(자유)  {k+1}회차 {len(v)}/{len(goals)}행 {time.monotonic()-t0:.0f}s", flush=True)

    # ── 개선(절차 판정) ──
    v2_runs, v2_full = [], None
    for k in range(a.runs):
        t0 = time.monotonic()
        full = await judge_v2(goals, B, a.concurrency)
        v2_runs.append({i: d["판정"] for i, d in full.items() if d})
        v2_full = v2_full or full
        print(f"  개선(절차)  {k+1}회차 {len(v2_runs[-1])}/{len(goals)}행 {time.monotonic()-t0:.0f}s", flush=True)

    print("\n── ① 자기 일치 ───────────────────────────────")
    for name, runs in (("현행", v1_runs), ("개선", v2_runs)):
        if len(runs) >= 2:
            c, n = b2.agree(runs[0], runs[1])
            out[f"{name} 자기 일치"] = round(c / n, 3) if n else None
            print(f"  {name}  {c}/{n} = {c/n:.3f}")

    print("\n── ② 판정 분포 ───────────────────────────────")
    print(f"  {'판정':<14}{'현행':>8}{'개선':>8}")
    for v in VERDICTS:
        print(f"  {v:<14}{Counter(v1_runs[0].values())[v]:>8}{Counter(v2_runs[0].values())[v]:>8}")
    out["현행 분포"] = dict(Counter(v1_runs[0].values()))
    out["개선 분포"] = dict(Counter(v2_runs[0].values()))

    c, n = b2.agree(v1_runs[0], v2_runs[0])
    out["방식 간 일치"] = round(c / n, 3) if n else None
    print(f"\n── ③ 두 방식 간 일치 ─────────────────────────\n  {c}/{n} = {c/n:.3f}")

    print("\n── ④ 근거의 (B) 유래 수치 ────────────────────")
    h, t = b2.concreteness(v1_reasons, goals)
    print(f"  현행  {h}/{t} = {h/t:.1%}  (근거 문장 기준)")
    out["현행 (B)수치 근거"] = f"{h}/{t}"
    v2_num = sum(1 for d in v2_full.values() if d and any(
        e.get("수치") for s in d["하위목표"] for e in s.get("발췌", [])))
    print(f"  개선  {v2_num}/{len(v2_full)} = {v2_num/len(v2_full):.1%}  (발췌 수치 칸 기준)")
    out["개선 (B)수치 근거"] = f"{v2_num}/{len(v2_full)}"

    print("\n── ⑤ 발췌의 근거 검증 (개선안만 가능) ─────────")
    exc = [(i, e["문장"]) for i, d in v2_full.items() if d
           for s in d["하위목표"] for e in s.get("발췌", [])]
    ok = sum(1 for _, s in exc if grounded(s, B))
    out["발췌 총수"] = len(exc); out["발췌 근거 확인"] = ok
    print(f"  발췌 {len(exc)}건 중 (B)에 실제로 있는 문장 {ok}건 = "
          f"{ok/len(exc):.1%}" if exc else "  발췌 없음")
    print(f"  → 환각(지어낸 발췌) {len(exc)-ok}건")

    print("\n── ⑥ 규칙 준수 — 하위 판정에서 행 판정이 규칙대로 나왔나 ──")
    viol = [(i, d["판정"], rule_verdict(d["하위목표"])) for i, d in v2_full.items()
            if d and d["판정"] != rule_verdict(d["하위목표"])]
    out["규칙 위반"] = len(viol)
    print(f"  위반 {len(viol)}/{len(v2_full)}건")
    for i, got, exp in viol[:5]:
        print(f"    [{i}] 모델={got} 규칙={exp}")

    print("\n── ⑦ 하위 목표 분해 ───────────────────────────")
    ns = [len(d["하위목표"]) for d in v2_full.values() if d]
    print(f"  행당 하위 목표 평균 {sum(ns)/len(ns):.1f}개, 분포 {dict(sorted(Counter(ns).items()))}")
    subl = Counter(s["판정"] for d in v2_full.values() if d for s in d["하위목표"])
    print(f"  하위 판정 분포 {dict(subl)}")
    out["하위 판정 분포"] = dict(subl)

    out["현행 원자료"] = [{str(i): v for i, v in r.items()} for r in v1_runs]
    out["현행 근거"] = {str(i): w for i, w in v1_reasons.items()}
    out["개선 원자료"] = [{str(i): v for i, v in r.items()} for r in v2_runs]
    out["개선 전문"] = {str(i): d for i, d in v2_full.items()}
    p = REPO / "bench/results" / f"judge-prompt-ab-{a.field}-{a.year}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {p}")


if __name__ == "__main__":
    asyncio.run(main())
