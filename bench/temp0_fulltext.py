#!/usr/bin/env python3
"""temperature=0이 **분류 밖의 결과물**을 어떻게 바꾸는가.

5.6절에서 Gemini가 temperature 0에서 achievement_type을 200/200 완전 재현한다는 것을
확인했다. 그러나 **결정성은 품질이 아니다.** greedy 디코딩은 긴 서술에서 반복·퇴화를
일으키는 것으로 알려져 있고, 우리 추출물의 4/5는 자유 서술(tech_summary·approach·
improvement)과 수치 목록(metrics)이다. 분류만 보고 채택하면 그쪽을 놓친다.

그래서 같은 논문에 세 조건을 돌려 **모든 필드**를 비교한다.

    Gemini temp 1.0   — 현행(운영이 지정하지 않아 API 기본값으로 도는 상태)
    Gemini temp 0.0   — 채택 후보
    deepseek-v4-pro:0813 temp 0.0 — 제공자 축의 대조군

핵심 지표는 셋이다.

1. **퇴화**: tech_summary의 3-gram 반복률. greedy가 무너지면 여기서 먼저 보인다.
2. **수치 환각**: metrics[].value가 초록에 실제로 존재하는가. 기계적으로 검사할 수
   있는 몇 안 되는 정확도 지표다 — 값이 원문에 없으면 모델이 지어낸 것이다.
3. **정보량**: 서술 길이·metrics 건수·빈 필드 비율. temp 0이 "짧고 안전한" 답으로
   수렴해 정보를 잃는지 본다.

    PYTHONPATH=backend:bench backend/.venv/bin/python bench/temp0_fulltext.py --n 60
"""
import argparse
import asyncio
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "bench"))
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from app.prompts import MAP_INSTRUCTION, MAP_SCHEMA, map_user_text  # noqa: E402
from ollama_extraction_bench import _similarity, env, fetch_papers, run_condition  # noqa: E402

MODEL = "gemini-3.1-flash-lite"
THINKING = "low"


def repetition(text: str) -> float:
    """3-gram 반복률 — greedy 퇴화의 조기 신호. 0이면 모든 3-gram이 유일하다."""
    w = text.split()
    if len(w) < 4:
        return 0.0
    grams = [tuple(w[i:i + 3]) for i in range(len(w) - 2)]
    return round(1 - len(set(grams)) / len(grams), 3)


_NUM = re.compile(r"\d+(?:\.\d+)?")


def value_in_abstract(value: str, abstract: str) -> bool | None:
    """metrics[].value의 숫자가 초록에 있는가. 숫자가 없는 값은 판정하지 않는다(None).

    표기 차이를 흡수하려고 숫자만 뽑아 비교한다 — "12.5%"와 "12.5 %"를 다른 값으로
    세면 환각률이 아니라 표기 습관을 재게 된다."""
    nums = _NUM.findall(value or "")
    if not nums:
        return None
    return any(n in abstract for n in nums)


def profile(recs, papers) -> dict:
    ok = [(r, p) for r, p in zip(recs, papers) if isinstance(r, dict) and "tech_summary" in r]
    if not ok:
        return {"유효": 0}
    ts = [str(r.get("tech_summary") or "") for r, _ in ok]
    ap = [str(r.get("approach") or "") for r, _ in ok]
    im = [str(r.get("improvement") or "") for r, _ in ok]
    mcounts, grounded, ungrounded, unjudged, blank_name = [], 0, 0, 0, 0
    for r, p in ok:
        ms = r.get("metrics") or []
        mcounts.append(len(ms))
        for m in ms:
            if not isinstance(m, dict):
                continue
            if not str(m.get("name") or "").strip():
                blank_name += 1
            v = value_in_abstract(str(m.get("value") or ""), p["abstract"])
            if v is None:
                unjudged += 1
            elif v:
                grounded += 1
            else:
                ungrounded += 1
    judged = grounded + ungrounded
    return {
        "유효": len(ok),
        "요약 길이 평균": round(statistics.mean(len(x) for x in ts)),
        "요약 3-gram 반복률": round(statistics.mean(repetition(x) for x in ts), 3),
        "요약 최대 반복률": round(max(repetition(x) for x in ts), 3),
        "approach 채움": f"{sum(1 for x in ap if x.strip())}/{len(ok)}",
        "improvement 채움": f"{sum(1 for x in im if x.strip())}/{len(ok)}",
        "metrics 건수 평균": round(statistics.mean(mcounts), 2),
        "metrics 0건 논문": f"{sum(1 for c in mcounts if c == 0)}/{len(ok)}",
        "metrics 빈 이름": blank_name,
        "수치 근거 있음": f"{grounded}/{judged}" if judged else "-",
        "수치 근거율": round(grounded / judged, 3) if judged else None,
        "수치 판정불가(숫자 없음)": unjudged,
        "성과유형 분포": dict(Counter(r.get("achievement_type") for r, _ in ok).most_common()),
    }


async def gemini_pass(client, sem, papers, temperature, label):
    cfg = types.GenerateContentConfig(
        system_instruction=MAP_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=MAP_SCHEMA,
        thinking_config=types.ThinkingConfig(thinking_level=THINKING),
        max_output_tokens=16000,
        temperature=temperature,
    )

    async def one(p):
        def call():
            return client.models.generate_content(
                model=MODEL, contents=map_user_text(p["title"], p["abstract"]), config=cfg)
        async with sem:
            for attempt in range(4):
                try:
                    r = await asyncio.get_running_loop().run_in_executor(None, call)
                    return json.loads(r.text or "{}")
                except Exception:
                    if attempt == 3:
                        return None
                    await asyncio.sleep(2 ** attempt)

    out = await asyncio.gather(*[one(p) for p in papers])
    print(f"  [{label}] {len(papers)}건 완료", flush=True)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--ollama-model", default="deepseek-v4-pro:0813")
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--out", default="bench/results/temp0-fulltext.json")
    ap.add_argument("--samples", default="bench/results/temp0-samples.md")
    ap.add_argument("--n-samples", type=int, default=8)
    args = ap.parse_args()

    papers = fetch_papers(args.n, args.dsn)
    print(f"논문 {len(papers)}건 · Gemini temp 1.0/0.0 · {args.ollama_model} temp 0.0\n")

    key = next(l.split("=", 1)[1].strip() for l in (REPO / ".env").read_text().splitlines()
               if l.startswith("GEMINI_API_KEY="))
    client = genai.Client(api_key=key)
    sem = asyncio.Semaphore(8)
    g1 = await gemini_pass(client, sem, papers, 1.0, "gemini t=1.0")
    g0 = await gemini_pass(client, sem, papers, 0.0, "gemini t=0.0")

    conc = int(env("OLLAMA_CONCURRENCY", "2"))
    raw, _ = await run_condition(papers, args.ollama_model, False, conc,
                                 f"{args.ollama_model} t=0.0", temperature=0.0)
    d0 = [r.get("parsed") if r.get("ok") and r.get("schema_ok") else None for r in raw]

    conds = {"gemini t=1.0": g1, "gemini t=0.0": g0, f"{args.ollama_model} t=0.0": d0}
    report = {"papers": len(papers), "조건별": {k: profile(v, papers) for k, v in conds.items()}}

    def sim(a, b):
        pairs = [(x, y) for x, y in zip(a, b) if isinstance(x, dict) and isinstance(y, dict)]
        if not pairs:
            return None
        return round(statistics.mean(
            _similarity(str(x.get("tech_summary") or ""), str(y.get("tech_summary") or ""))
            for x, y in pairs), 3)

    def type_match(a, b):
        return sum(1 for x, y in zip(a, b)
                   if isinstance(x, dict) and isinstance(y, dict)
                   and x.get("achievement_type") == y.get("achievement_type"))

    report["요약 유사도"] = {
        "gemini t=0.0 ↔ t=1.0": sim(g0, g1),
        f"gemini t=0.0 ↔ {args.ollama_model} t=0.0": sim(g0, d0),
    }
    report["성과유형 일치"] = {
        "gemini t=0.0 ↔ t=1.0": type_match(g0, g1),
        f"gemini t=0.0 ↔ {args.ollama_model} t=0.0": type_match(g0, d0),
    }

    # 원시 레코드도 남긴다 — 집계만 남기면 "문체가 바뀌었나" 같은 사후 질문에
    # 매번 LLM을 다시 돌려야 한다.
    report["원시"] = [
        {"title": p["title"], **{k: (v[i] if isinstance(v[i], dict) else None)
                                 for k, v in conds.items()}}
        for i, p in enumerate(papers)
    ]
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    # 사람이 읽을 대조표 — 지표는 퇴화를 잡지만 "해석이 달라졌는가"는 못 잡는다.
    lines = ["# temperature=0 대조 표본\n",
             "지표는 반복·환각을 잡지만 **해석이 달라졌는지는 읽어야 안다.**\n"]
    for i, p in enumerate(papers[:args.n_samples]):
        lines.append(f"\n## {i + 1}. {p['title']}\n")
        for name, recs in conds.items():
            r = recs[i]
            if not isinstance(r, dict):
                lines.append(f"### {name}\n\n(실패)\n")
                continue
            ms = "; ".join(
                f"{m.get('name')}={m.get('value')}{m.get('unit') or ''}({m.get('target')})"
                for m in (r.get("metrics") or []) if isinstance(m, dict)) or "(없음)"
            lines.append(
                f"### {name}\n\n"
                f"- **유형**: {r.get('achievement_type')}\n"
                f"- **요약**: {r.get('tech_summary')}\n"
                f"- **접근**: {r.get('approach')}\n"
                f"- **개선**: {r.get('improvement')}\n"
                f"- **지표**: {ms}\n")
    (REPO / args.samples).write_text("\n".join(lines))

    w = 26
    keys = [k for k in report["조건별"]["gemini t=0.0"] if k != "성과유형 분포"]
    print(f"\n{'지표':<26}" + "".join(f"{c:>26}" for c in conds))
    print("-" * (26 + 26 * len(conds)))
    for k in keys:
        print(f"{k:<26}" + "".join(f"{str(report['조건별'][c].get(k, '-')):>{w}}" for c in conds))
    print(f"\n요약 유사도: {report['요약 유사도']}")
    print(f"성과유형 일치(/{len(papers)}): {report['성과유형 일치']}")
    print(f"\n{args.out}\n{args.samples}")


if __name__ == "__main__":
    asyncio.run(main())
