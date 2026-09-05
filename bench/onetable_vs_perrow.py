#!/usr/bin/env python3
"""같은 모델·같은 입력에서 **65행을 한 번에 만들 때와 행마다 물을 때**가 다른가.

문서 5.3.3절이 제기한 문제의 재검증이다. 초판(운영 경로 = 한 표)은 단계별 확인율이
감소로, 패널 실행(행 단위)은 증가로 나왔다. 그 둘 사이에는 방식 말고도 여러 차이가
있었으므로(파서 버그·프롬프트 문구·판정 척도) **이 스크립트는 방식 하나만 남기고
전부 같게 맞춘다.**

  같게 맞추는 것 : 모델 · temperature=0 · thinking · (B) 컨텍스트 · 판정 척도 ·
                   판정 정의 문구 · 고쳐진 파서
  유일한 차이    : 65행을 **한 콜**로 만드는가, **65콜**로 만드는가

두 방식 모두 2회씩 돌려 **자기 일치**를 먼저 잰다. 이것이 없으면 "방식 차이"와
"그 방식의 자체 변동"을 구분할 수 없다(자매 문서 5.5절이 정리한 함정).

    PYTHONPATH=backend backend/.venv/bin/python bench/onetable_vs_perrow.py --limit 20
    PYTHONPATH=backend backend/.venv/bin/python bench/onetable_vs_perrow.py
"""
import argparse
import asyncio
import importlib.util
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
_spec = importlib.util.spec_from_file_location("rp", REPO / "bench" / "roadmap_panel.py")
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

MODEL = "gemini-3.1-flash-lite"

# 한 표 방식 — 행 단위와 **같은 판정 정의**를 쓰고 전수 강제만 덧붙인다.
# 전수 강제(goal_count 주입)는 이 방식에만 필요하다. 없으면 65행이 19행으로 뭉개진다
# (운영 실측). 행 단위에는 원리적으로 축약이 없으므로 이 규칙 자체가 방식의 일부다.
ONETABLE_TMPL = """당신은 국가전략기술 로드맵의 이행 상황을 점검하는 과학기술 분석가입니다.

입력은 두 부분입니다.
(A) 전략기술로드맵의 기술적 목표 목록 — 총 {n}개
(B) 해당 분야에서 실제로 발표된 논문을 분석한 세부기술별 성과 보고서

(B)의 근거만으로 (A)의 목표 하나하나를 판정하세요.

## 절대 규칙
1. (A)에는 목표가 **정확히 {n}개** 있습니다. 당신의 출력도 **정확히 {n}개 항목**이어야
   합니다. 여러 목표를 하나로 합치거나, 근거가 없다는 이유로 건너뛰는 것을 금지합니다.
   각 항목의 `id`는 (A)에 적힌 번호를 그대로 씁니다.
2. (B)에 근거가 없으면 반드시 `데이터 없음`으로 판정하세요. 추측하거나 일반 상식으로
   채우지 마세요. 근거 없는 목표가 많은 것은 정상이며, 그것을 그대로 드러내는 것이
   이 점검의 목적입니다.
3. 판정 근거로 (B)의 어느 서술을 보았는지 함께 적으세요. `데이터 없음`이면 무엇을
   찾았으나 없었는지 적으세요.
4. 논문 성과는 "연구가 진행되고 있다"는 신호일 뿐 목표 달성의 증거가 아닙니다.
   달성률 같은 숫자를 만들어내지 마세요.

## 판정은 다음 중 하나만
{verdicts}

한국어로 답하세요."""


def onetable_schema(verdicts):
    return {
        "type": "object",
        "properties": {
            "판정목록": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "판정": {"type": "string", "enum": verdicts},
                        "근거": {"type": "string"},
                    },
                    "required": ["id", "판정", "근거"],
                },
            }
        },
        "required": ["판정목록"],
    }


def goals_block(goals) -> str:
    out = []
    for g in goals:
        t = f" / {g['시기']}" if g["시기"] else ""
        out.append(f"{g['id']}. [{g['중점기술']} · {g['세부항목']} · {g['단계']}{t}] {g['목표']}")
    return "\n".join(out)


_client = None


def _cli():
    global _client
    from google import genai
    if _client is None:
        _client = genai.Client(api_key=rp.env("GEMINI_API_KEY"))
    return _client


async def one_table(goals, context, verdicts, thinking):
    from google.genai import types
    sysmsg = ONETABLE_TMPL.format(
        n=len(goals), verdicts="\n".join(f"- `{v}`" for v in verdicts))
    user = (f"# (A) 점검할 기술적 목표 {len(goals)}개\n\n{goals_block(goals)}\n\n\n"
            f"# (B) 논문 분석 기반 세부기술별 성과 보고서\n\n{context}")
    cfg = types.GenerateContentConfig(
        system_instruction=sysmsg,
        response_mime_type="application/json",
        response_schema=onetable_schema(verdicts),
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
        max_output_tokens=65536,
    )
    r = await asyncio.to_thread(_cli().models.generate_content,
                                model=MODEL, contents=user, config=cfg)
    rows = json.loads(r.text)["판정목록"]
    return ({int(x["id"]): x["판정"] for x in rows if x.get("판정") in verdicts},
            r.usage_metadata)


async def per_row(goals, context, verdicts, thinking, conc):
    from google.genai import types
    cfg = types.GenerateContentConfig(
        system_instruction=rp.SYSTEM,
        response_mime_type="application/json",
        response_schema={k: v for k, v in rp.make_schema(verdicts).items()
                         if k != "additionalProperties"},
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    sem = asyncio.Semaphore(conc)
    tok = [0, 0]

    async def one(g):
        async with sem:
            for att in range(3):
                try:
                    r = await asyncio.to_thread(
                        _cli().models.generate_content, model=MODEL,
                        contents=rp.user_text(g, context), config=cfg)
                    um = r.usage_metadata
                    tok[0] += um.prompt_token_count or 0
                    tok[1] += (um.candidates_token_count or 0) + (um.thoughts_token_count or 0)
                    return g["id"], json.loads(r.text)["판정"]
                except Exception:
                    if att == 2:
                        return g["id"], None
                    await asyncio.sleep(2 ** att * 2)

    pairs = await asyncio.gather(*[one(g) for g in goals])
    return {i: v for i, v in pairs if v}, tok


def agree(a, b):
    both = [i for i in a if i in b]
    return sum(1 for i in both if a[i] == b[i]), len(both)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="반도체")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--country", default="KR")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cur = psycopg2.connect(a.dsn).cursor()
    cur.execute("SELECT id, name FROM fields WHERE name LIKE %s", (a.field + "%",))
    fid, fname = cur.fetchone()
    cur.execute("SELECT content_md FROM roadmaps WHERE field_id=%s", (fid,))
    goals = rp.parse_goals(cur.fetchone()[0])
    cur.execute("""SELECT s.name, a.report_md FROM analyses a
                   JOIN subfields s ON s.id=a.subfield_id
                   WHERE s.field_id=%s AND a.year=%s AND a.country=%s AND a.status='done'
                     AND a.report_md IS NOT NULL AND a.report_md<>'' ORDER BY s.name""",
                (fid, a.year, a.country))
    reports = cur.fetchall()
    cur.execute("SELECT name FROM subfields WHERE field_id=%s", (fid,))
    missing = [r[0] for r in cur.fetchall() if r[0] not in [n for n, _ in reports]]

    verdicts = rp.active_verdicts(not missing)
    rp.VERDICTS_ACTIVE = verdicts
    rp.SYSTEM = rp.build_system([n for n, _ in reports], not missing)
    context = "\n\n".join(f"## {n}\n{md}" for n, md in reports)
    if a.limit:
        goals = goals[:a.limit]
    thinking = rp.env("THINKING_REDUCE", "high")

    print(f"{fname} {a.year} {a.country} — 목표 {len(goals)}행 · 보고서 {len(reports)}건")
    print(f"  모델 {MODEL} · temperature=0 · thinking={thinking} · 판정 {len(verdicts)}지")
    print("  두 방식만 다르고 나머지는 전부 같습니다.\n")

    res = {"한 표": [], "행 단위": []}
    for run in (1, 2):
        t0 = time.monotonic()
        tbl, um = await one_table(goals, context, verdicts, thinking)
        res["한 표"].append(tbl)
        print(f"  한 표    {run}회차  {len(tbl)}/{len(goals)}행  {time.monotonic()-t0:.0f}s "
              f"(in {um.prompt_token_count:,} / out "
              f"{(um.candidates_token_count or 0)+(um.thoughts_token_count or 0):,})", flush=True)
    for run in (1, 2):
        t0 = time.monotonic()
        pr, tok = await per_row(goals, context, verdicts, thinking, a.concurrency)
        res["행 단위"].append(pr)
        print(f"  행 단위  {run}회차  {len(pr)}/{len(goals)}행  {time.monotonic()-t0:.0f}s "
              f"(in {tok[0]:,} / out {tok[1]:,})", flush=True)

    out = {"분야": fname, "연도": a.year, "국가": a.country, "목표 행": len(goals),
           "모델": MODEL, "thinking": thinking, "판정 척도": verdicts}

    print("\n── 자기 일치 (방식별 1회차↔2회차) ──────────────")
    for k, runs in res.items():
        c, n = agree(runs[0], runs[1])
        out[f"{k} 자기 일치"] = {"일치": f"{c}/{n}", "비율": round(c/n, 3) if n else None}
        print(f"  {k:<8} {c}/{n} = {c/n:.3f}" if n else f"  {k}: —")

    print("\n── 방식 간 일치 (같은 회차끼리) ────────────────")
    cross = []
    for i in (0, 1):
        c, n = agree(res["한 표"][i], res["행 단위"][i])
        cross.append(round(c/n, 3) if n else None)
        print(f"  {i+1}회차  {c}/{n} = {c/n:.3f}" if n else "  —")
    out["방식 간 일치"] = cross

    print("\n── 판정 분포 ──────────────────────────────────")
    for k, runs in res.items():
        d = dict(Counter(runs[0].values()))
        out[f"{k} 분포"] = d
        print(f"  {k:<8} {d}")

    stg = {g["id"]: g["단계"] for g in goals if g["단계축"]}
    print(f"\n── 단계별 `관련 연구 확인` (단계축 {len(stg)}/{len(goals)}행) ──")
    st_out = {}
    for k, runs in res.items():
        t = defaultdict(lambda: [0, 0])
        for i, v in runs[0].items():
            if i in stg:
                t[stg[i]][1] += 1
                t[stg[i]][0] += v == "관련 연구 확인"
        cells = {s: f"{t[s][0]}/{t[s][1]}" for s in sorted(t)}
        st_out[k] = cells
        line = "  ".join(f"{s} {t[s][0]}/{t[s][1]}={t[s][0]/t[s][1]:.0%}"
                         for s in sorted(t) if t[s][1])
        print(f"  {k:<8} {line}")
    out["단계별"] = st_out

    p = Path(a.out or REPO / "bench" / "results" /
             f"onetable-vs-perrow-{a.field}-{a.year}.json")
    out["원자료"] = {k: [{str(i): v for i, v in r.items()} for r in runs]
                     for k, runs in res.items()}
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {p}")


if __name__ == "__main__":
    asyncio.run(main())
