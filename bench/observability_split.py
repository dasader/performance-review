#!/usr/bin/env python3
"""관측 가능성 분류 — 로드맵 목표가 **논문으로 답할 수 있는 종류인가** (7.1절, 주 결과 후보).

`데이터 없음`에는 성격이 정반대인 둘이 섞여 있다: (가) "웨이퍼당 $500"처럼 논문에 실릴 수
없는 목표, (나) 실릴 수 있는데 실제로 없는 목표. 정책 신호는 (나)뿐이다. 이 분류는 판정
**이전**에 목표 문구만 보고 한다 — 도메인 지식이 아니라 "논문이 무엇을 싣는가"만 필요하다.

산출: 확인율(전체 기준) 대 **확인율(관측 가능 기준)**, 그리고 관측 불가 행이 실제로
`데이터 없음`에 몰려 있는지(5.4절의 기제 검증). 2회 돌려 자기 일치를 함께 낸다.

    PYTHONPATH=backend backend/.venv/bin/python bench/observability_split.py
"""
import asyncio, json, re, sys
from collections import Counter
from pathlib import Path
import psycopg2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from app.services.roadmap_parse import parse_goals  # noqa: E402

MODEL = "gemini-3.1-flash-lite"
KINDS = ["양산·제품화", "가격·경제성", "자급률·수입의존도", "실증·상용화 검증", "인프라·제도", "해당 없음"]
SCHEMA = {"type": "object", "properties": {
    "하위목표": {"type": "array", "items": {"type": "object", "properties": {
        "항목": {"type": "string"},
        "관측": {"type": "string", "enum": ["가능", "불가"]},
        "불가유형": {"type": "string", "enum": KINDS},
        "사유": {"type": "string"}}, "required": ["항목", "관측", "불가유형", "사유"]}},
    "행판정": {"type": "string", "enum": ["가능", "부분", "불가"]}},
    "required": ["하위목표", "행판정"]}

SYSTEM = """당신은 과학기술 정책 문서를 읽는 분석가입니다. 로드맵의 기술적 목표 하나를 받아
**그 목표가 학술 논문(초록)으로 관측될 수 있는 종류인지** 판정하세요. 연구가 실제로
있는지가 아니라, **원리적으로 논문에 실릴 수 있는 내용인지**를 묻는 것입니다.

절차:
1. 목표를 하위 목표로 나누세요(쉼표·마침표로 나뉜 서로 다른 항목).
2. 하위 목표마다:
   - `가능` — 물성·구조·소자·공정·알고리즘·성능 수치 등 연구 성과로 보고될 수 있는 내용
   - `불가` — 논문이 다루지 않는 내용. 유형을 고르세요:
     양산·제품화 / 가격·경제성 / 자급률·수입의존도 / 실증·상용화 검증 / 인프라·제도
   "X 기술 개발 및 **제품화**"처럼 섞여 있으면 기술 개발 부분은 `가능`, 제품화는 `불가`로
   **나눠서** 적으세요.
3. 행판정: 전부 가능 → `가능` / 일부만 가능 → `부분` / 전부 불가 → `불가`

목표 문구만 보고 판정하세요. 지어내지 마세요. JSON 객체 하나만, 한국어로."""

_cli = None
def cli():
    global _cli
    from google import genai
    if _cli is None:
        env = dict(l.split("=", 1) for l in (REPO / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
        _cli = genai.Client(api_key=env["GEMINI_API_KEY"].strip())
    return _cli

async def classify(goals, conc=6):
    from google.genai import types
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM, response_mime_type="application/json",
                                      response_schema=SCHEMA, temperature=0,
                                      thinking_config=types.ThinkingConfig(thinking_level="high"))
    sem = asyncio.Semaphore(conc)
    async def one(g):
        u = f"- 중점기술: {g['중점기술']}\n- 세부 항목: {g['세부항목']}\n- 단계·구분: {g['단계']}\n- 기술적 목표: {g['목표']}"
        async with sem:
            for att in range(4):
                try:
                    r = await asyncio.to_thread(cli().models.generate_content, model=MODEL, contents=u, config=cfg)
                    return g["id"], json.loads(r.text)
                except Exception as e:
                    if att == 3: print(f"  !{g['id']} {str(e)[:60]}"); return g["id"], None
                    await asyncio.sleep(2 ** att * 2)
    return dict(await asyncio.gather(*[one(g) for g in goals]))

def verdicts_from_report(md):
    out, n, t = {}, 0, False
    for l in md.splitlines():
        s = l.strip()
        if not s.startswith("|"): t = False; continue
        if re.match(r"^\|[\s:|-]+\|$", s): t = True; continue
        if not t: continue
        c = [x.strip() for x in s.strip("|").split("|")]
        if len(c) >= 4:
            n += 1
            for v in ("관련 연구 확인", "부분 관련", "데이터 없음", "분석 범위 밖"):
                if c[2] == f"**{v}**": out[n] = v
    return out

async def main():
    field = sys.argv[1] if len(sys.argv) > 1 else "반도체"
    cur = psycopg2.connect("postgresql://perfrev:perfrev@localhost:5403/perfrev").cursor()
    cur.execute("SELECT id, name FROM fields WHERE name LIKE %s", (field + "%",))
    fid, fname = cur.fetchone()
    cur.execute("SELECT content_md FROM roadmaps WHERE field_id=%s", (fid,))
    goals = parse_goals(cur.fetchone()[0])
    cur.execute("SELECT report_md FROM roadmap_checks WHERE field_id=%s AND year=2026", (fid,))
    ver = verdicts_from_report(cur.fetchone()[0])
    print(f"{fname} — 목표 {len(goals)}행 · 운영 판정 {len(ver)}행\n")

    runs = [await classify(goals) for _ in range(2)]
    a, b = runs
    same = sum(1 for i in a if a[i] and b.get(i) and a[i]["행판정"] == b[i]["행판정"])
    print(f"자기 일치 {same}/{len(a)} = {same/len(a):.3f}")
    rowc = Counter(a[i]["행판정"] for i in a if a[i])
    print(f"행판정 분포 {dict(rowc)}")
    subs = [s for i in a if a[i] for s in a[i]["하위목표"]]
    print(f"하위 목표 {len(subs)}개 — 가능 {sum(s['관측']=='가능' for s in subs)} · 불가 {sum(s['관측']=='불가' for s in subs)}")
    print("불가 유형:", dict(Counter(s["불가유형"] for s in subs if s["관측"] == "불가")))

    print("\n── 관측 가능성 × 운영 판정 (5.4절 기제 검증) ──")
    print(f"  {'관측':<6}{'확인':>6}{'부분':>6}{'없음':>6}{'계':>6}")
    tab = {}
    for k in ("가능", "부분", "불가"):
        ids = [i for i in a if a[i] and a[i]["행판정"] == k]
        c = Counter(ver.get(i) for i in ids)
        tab[k] = c
        print(f"  {k:<6}{c['관련 연구 확인']:>6}{c['부분 관련']:>6}{c['데이터 없음']:>6}{len(ids):>6}")

    conf = sum(1 for v in ver.values() if v == "관련 연구 확인")
    n_all = len(ver)
    obs = [i for i in a if a[i] and a[i]["행판정"] != "불가"]
    obs_strict = [i for i in a if a[i] and a[i]["행판정"] == "가능"]
    print("\n── 확인율 ────────────────────────────────")
    print(f"  전체 기준            {conf}/{n_all} = {conf/n_all:.1%}")
    c1 = sum(1 for i in obs if ver.get(i) == "관련 연구 확인")
    print(f"  관측 가능 기준(부분 포함) {c1}/{len(obs)} = {c1/len(obs):.1%}")
    c2 = sum(1 for i in obs_strict if ver.get(i) == "관련 연구 확인")
    print(f"  관측 가능 기준(전부 가능만) {c2}/{len(obs_strict)} = {c2/len(obs_strict):.1%}" if obs_strict else "")
    unobs_none = tab["불가"]["데이터 없음"]; unobs_n = sum(tab["불가"].values())
    print(f"\n  관측 불가 행 중 `데이터 없음`: {unobs_none}/{unobs_n}" if unobs_n else "\n  관측 불가 행 없음")
    print(f"  `데이터 없음` {sum(1 for v in ver.values() if v=='데이터 없음')}건 중 관측 불가 행: {unobs_none}건 → "
          f"**진짜 연구 공백 후보 {sum(1 for v in ver.values() if v=='데이터 없음')-unobs_none}건**")

    out = {"분야": fname, "자기 일치": same/len(a), "행판정 분포": dict(rowc),
           "교차표": {k: dict(v) for k, v in tab.items()},
           "확인율 전체": f"{conf}/{n_all}", "확인율 관측가능(부분포함)": f"{c1}/{len(obs)}",
           "확인율 관측가능(엄격)": f"{c2}/{len(obs_strict)}",
           "원자료": {str(i): a[i] for i in a}}
    p = REPO / "bench/results" / f"observability-{field}-2026.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {p}")

asyncio.run(main())
