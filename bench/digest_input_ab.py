#!/usr/bin/env python3
"""⑥-3 — 로드맵 판정의 입력을 LLM 보고서 대신 **코드가 만드는 결정적 요약**으로.

reduce는 파라미터로 재현시킬 수 없다(temperature 0에서 6/10, seed 무효). 판정 경로를
reduce에서 떼어내면 종단 재현성이 구조로 보장된다. 다만 6.8-b가 "추출 전량 주입(8.6배)"은
무효라고 보였으므로 **집계된 소량 입력**이어야 한다.

요약(digest) 구성 — 전부 코드, 정렬 고정: 세부기술마다 성과유형 분포 · 유형별 인용 상위
K편(제목·요약·지표) · 지표명 상위 15. 같은 DB 상태면 바이트까지 같다.

재는 것: ① 요약이 실제로 결정적인가(2회 생성 바이트 비교) ② 요약 입력 판정 대 보고서
입력 판정(운영 2차)의 행 일치 ③ 분포 ④ 발췌가 요약에서 실재하는가 ⑤ 크기.
"""
import asyncio, hashlib, importlib.util, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
import psycopg2
REPO = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(REPO / "backend"))
from app.services.roadmap_parse import parse_goals
sp = importlib.util.spec_from_file_location("jp", REPO / "bench/judge_prompt_ab.py")
jp = importlib.util.module_from_spec(sp); sp.loader.exec_module(jp)
K = 4

def build_digest(cur, field_id):
    cur.execute("""SELECT s.name, a.id, a.stats_json FROM analyses a JOIN subfields s ON s.id=a.subfield_id
                   WHERE s.field_id=%s AND a.year=2026 AND a.country='KR' AND a.status='done' ORDER BY s.name""", (field_id,))
    parts = []
    for name, aid, stats in cur.fetchall():
        cur.execute("""SELECT p.title, p.citations, e.achievement_type, e.tech_summary, e.metrics_json, e.improvement
                       FROM analysis_papers ap JOIN papers p ON p.id=ap.paper_id
                       JOIN paper_extractions e ON e.paper_key=p.paper_key AND e.model_ver='gemini-3.1-flash-lite/low/v3'
                       WHERE ap.analysis_id=%s ORDER BY p.citations DESC NULLS LAST, p.title""", (aid,))
        rows = cur.fetchall()
        if not rows: continue
        by = defaultdict(list)
        for r in rows: by[r[2] or "기타"].append(r)
        mnames = Counter(m.get("name","") for r in rows for m in (r[4] or []) if isinstance(r[4], list))
        out = [f"## {name}", f"논문 {len(rows)}건. 성과유형: " + " · ".join(f"{k} {len(v)}" for k, v in sorted(by.items(), key=lambda x: -len(x[1]))), ""]
        for typ, lst in sorted(by.items(), key=lambda x: -len(x[1])):
            out.append(f"### {typ} ({len(lst)}건) — 인용 상위 {min(K, len(lst))}편")
            for title, cit, _, summ, mets, imp in lst[:K]:
                ms = "; ".join(f"{m.get('name','')} {m.get('value','')}{m.get('unit','') or ''}" + (f"({m['target']})" if m.get('target') else "")
                               for m in (mets or [])[:4] if isinstance(m, dict) and m.get('name'))
                out.append(f"- {title} (인용 {cit or 0}) — {(summ or '')[:160]}" + (f" | 지표: {ms}" if ms else "") + (f" | 개선: {imp[:80]}" if imp else ""))
            out.append("")
        out.append("주요 지표명(건수): " + ", ".join(f"{n}({c})" for n, c in mnames.most_common(15) if n))
        parts.append("\n".join(out))
    return "\n\n".join(parts)

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
            for v in ("관련 연구 확인", "부분 관련", "데이터 없음"):
                if c[2] == f"**{v}**": out[n] = v
    return out

async def main():
    cur = psycopg2.connect("postgresql://perfrev:perfrev@localhost:5403/perfrev").cursor()
    cur.execute("SELECT id FROM fields WHERE name LIKE '반도체%'"); fid = cur.fetchone()[0]
    cur.execute("SELECT content_md FROM roadmaps WHERE field_id=%s", (fid,)); goals = parse_goals(cur.fetchone()[0])
    d1, d2 = build_digest(cur, fid), build_digest(cur, fid)
    h = lambda t: hashlib.sha256(t.encode()).hexdigest()[:12]
    print(f"① 요약 결정성: 2회 생성 {'동일' if d1 == d2 else '다름'} (sha {h(d1)} / {h(d2)}) · {len(d1):,}자")
    cur.execute("""SELECT sum(length(report_md)) FROM analyses a JOIN subfields s ON s.id=a.subfield_id
                   WHERE s.field_id=%s AND a.year=2026 AND a.country='KR'""", (fid,))
    print(f"   (LLM 보고서 10건 합계 {cur.fetchone()[0]:,}자)")
    base = verdicts_from_report([r for r in json.loads((REPO / "bench/results/roadmap-checks-A형식-2차.json").read_text()) if r["field_id"] == fid][0]["report_md"])

    full = await jp.judge_v2(goals, d1, 6)
    v = {i: d["판정"] for i, d in full.items() if d}
    both = [i for i in v if i in base]; same = sum(v[i] == base[i] for i in both)
    print(f"\n② 요약 입력 판정 ↔ 보고서 입력 판정(운영 2차): {same}/{len(both)} = {same/len(both):.3f}")
    print(f"③ 분포  보고서 {dict(Counter(base.values()))}\n        요약   {dict(Counter(v.values()))}")
    ch = Counter(f"{base[i]} → {v[i]}" for i in both if base[i] != v[i])
    for k2, c in ch.most_common(): print(f"     {c:>2}건 {k2}")
    exc = [e["문장"] for d in full.values() if d for s in d["하위목표"] for e in s.get("발췌", [])]
    ok = sum(jp.grounded(e, d1) for e in exc)
    print(f"④ 발췌 {len(exc)}건 중 요약에 실재 {ok}건 = {ok/len(exc):.1%}" if exc else "④ 발췌 없음")
    subs = Counter(s["판정"] for d in full.values() if d for s in d["하위목표"])
    print(f"   하위 판정 {dict(subs)}")
    out = {"요약 자수": len(d1), "요약 sha": h(d1), "결정적": d1 == d2, "일치(vs 보고서)": f"{same}/{len(both)}",
           "분포 보고서": dict(Counter(base.values())), "분포 요약": dict(Counter(v.values())), "이동": dict(ch),
           "발췌 실재": f"{ok}/{len(exc)}", "원자료": {str(i): v[i] for i in v}, "요약 표본": d1[:2500]}
    p = REPO / "bench/results/digest-input-ab-반도체-2026.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"); print(f"\n→ {p}")
asyncio.run(main())
