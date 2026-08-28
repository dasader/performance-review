#!/usr/bin/env python3
"""사고수준을 올리면 pro:0813의 **수치 누락**이 줄어드는가.

n=400 실측에서 deepseek-v4-pro:0813(think=none)은 초록에 명시된 값을 28건(하한)
비웠다. 같은 자리에서 Gemini는 0건이다. 지표 이름과 단위는 맞게 뽑고 값만 비우므로
"무엇을 재야 하는지는 알지만 읽어오지 않는" 모양이고, 그렇다면 **추론 예산을 주면
개선될 여지**가 있다.

기존 결과(`temp0-fulltext-n400.json`)의 Gemini 레코드를 신탁으로 재사용한다 —
표본이 `md5` 순 결정론이라 같은 논문이고, Gemini는 temperature 0에서 결정적이라
다시 돌려도 같은 값이 나온다. 즉 **재실행 비용 없이 짝지은 비교**가 된다.

    PYTHONPATH=backend:bench backend/.venv/bin/python bench/ollama_think_sweep.py \\
        --n 200 --think none,low,high
"""
import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "bench"))
from blank_metric_audit import same_metric  # noqa: E402
from ollama_extraction_bench import env, fetch_papers, run_condition  # noqa: E402
from temp0_fulltext import exponent_kept, repetition, value_in_abstract  # noqa: E402


def audit(recs, papers, oracle):
    """빈 행을 행 단위로 판정한다 — 동료(oracle)가 같은 지표를 채웠으면 누락."""
    ok = [(r, p, o) for r, p, o in zip(recs, papers, oracle) if isinstance(r, dict)]
    blank = miss = filled = exp_need = exp_kept = 0
    sums, mcounts = [], []
    for r, p, o in ok:
        ab = p["abstract"]
        peers = [m for m in ((o or {}).get("metrics") or [])
                 if isinstance(m, dict)
                 and value_in_abstract(str(m.get("value") or ""), ab) is True]
        ms = [m for m in (r.get("metrics") or []) if isinstance(m, dict)]
        mcounts.append(len(ms))
        sums.append(str(r.get("tech_summary") or ""))
        for m in ms:
            val = str(m.get("value") or "")
            if value_in_abstract(val, ab) is None:
                blank += 1
                if any(same_metric(str(m.get("name") or ""), str(q.get("name") or ""),
                                   str(m.get("unit") or ""), str(q.get("unit") or ""))
                       for q in peers):
                    miss += 1
            else:
                filled += 1
            e = exponent_kept(val, str(m.get("unit") or ""), ab)
            if e is not None:
                exp_need += 1
                exp_kept += 1 if e else 0
    return {
        "유효": len(ok),
        "metrics 건수 평균": round(statistics.mean(mcounts), 2) if mcounts else 0,
        "값 채운 행": filled,
        "빈 행": blank,
        "└ 누락(하한)": miss,
        "요약 길이 평균": round(statistics.mean(len(s) for s in sums)) if sums else 0,
        "요약 반복률 최대": round(max((repetition(s) for s in sums), default=0), 3),
        "지수 보존": f"{exp_kept}/{exp_need}" if exp_need else "-",
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--model", default="deepseek-v4-pro:0813")
    ap.add_argument("--think", default="none,low,high")
    ap.add_argument("--oracle", default="bench/results/temp0-fulltext-n400.json")
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--out", default="bench/results/think-sweep-pro0813.json")
    args = ap.parse_args()

    src = json.loads((REPO / args.oracle).read_text())
    okey = next(k for k in src["원시"][0] if k.startswith("gemini") and "0.0" in k)
    papers = fetch_papers(args.n, args.dsn)
    titles = {p["title"] for p in papers}
    oracle = [r[okey] for r in src["원시"] if r["title"] in titles][:args.n]
    if len(oracle) != len(papers):
        raise SystemExit(f"신탁 레코드 부족: {len(oracle)} != {len(papers)}")

    conc = int(env("OLLAMA_CONCURRENCY", "2"))
    print(f"{args.model} · 논문 {len(papers)}건 · 동시 {conc} · 신탁 {okey}\n")

    report = {"model": args.model, "papers": len(papers), "oracle": okey, "조건별": {}}
    # 기준선: 신탁 자신도 같은 잣대로 잰다 — 0이 나와야 판정이 옳다(자기 자신이 동료).
    report["조건별"][okey] = audit(oracle, papers, oracle)
    for th in args.think.split(","):
        think = False if th == "none" else th
        raw, el = await run_condition(papers, args.model, think, conc, f"think={th}",
                                      temperature=0.0)
        recs = [r.get("parsed") if r.get("ok") and r.get("schema_ok") else None for r in raw]
        a = audit(recs, papers, oracle)
        a["총 소요(초)"] = round(el, 1)
        a["논문/분"] = round(len(papers) / el * 60, 1)
        report["조건별"][f"think={th}"] = a
        # 원시 레코드를 남긴다 — 집계만 두면 "이 값이 올바른 지표에 붙었나" 같은
        # 사후 질문에 매번 다시 돌려야 한다(실측으로 한 번 겪었다).
        report.setdefault("원시", {})[f"think={th}"] = recs

    (REPO / args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    cols = list(report["조건별"])
    w = 18
    keys = list(report["조건별"][cols[-1]])
    print(f"\n{'지표':<20}" + "".join(f"{c:>{w}}" for c in cols))
    print("-" * (20 + w * len(cols)))
    for k in keys:
        print(f"{k:<20}" + "".join(f"{str(report['조건별'][c].get(k, '-')):>{w}}" for c in cols))
    print(f"\n{args.out}")


if __name__ == "__main__":
    asyncio.run(main())
