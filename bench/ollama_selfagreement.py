#!/usr/bin/env python3
"""후보 모델을 같은 논문에 **두 번** 돌려 자기 일치율을 잰다.

왜 필요한가. `ollama_extraction_bench.py`가 재는 "vs Gemini 일치"는 **품질이 아니라
교체 가능성**이다. Gemini는 정답이 아니다 — `achievement_type`에는 정답 라벨이 없고
Gemini 자신도 같은 논문에 17% 어긋난다.

교차 일치는 두 가지를 섞는다: ① 후보 모델의 샘플링 노이즈, ② 두 모델의 **체계적
라벨 차이**. 자기 일치는 ①만 뽑아낸다. 이 구분이 실질적인 이유:

    두 모델의 라벨 분포가 같으면 교차 일치 = Gemini 자기 일치(0.830)가 된다.
    따라서 교차 0.561은 "분포가 다르다"까지만 말하고 "더 나쁘다"는 말하지 않는다.
    항상 아키텍처를 알고리즘이라 부르는 모델은 자기 자신과 100% 일관되면서도
    교차 일치가 낮다 — 그건 노이즈가 아니라 다른 분류 체계다.

    자기 일치가 0.830보다 **높으면** 후보가 더 안정적인 분류기라는 뜻이고,
    그때 낮은 교차 일치는 "품질 미달"이 아니라 "기존 20.7만 건과의 불연속"이라는
    전혀 다른(그리고 일회성인) 문제로 성격이 바뀐다.

    PYTHONPATH=backend:bench backend/.venv/bin/python bench/ollama_selfagreement.py \\
        --model deepseek-v4-flash:0731-cloud --n 60
"""
import argparse
import asyncio
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bench"))
from ollama_extraction_bench import compare, env, fetch_papers, run_condition  # noqa: E402


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((c - m) / d, 3), round((c + m) / d, 3)


def agreement(a_runs, b_runs, papers=None, key="gemini"):
    """두 결과 목록(또는 결과 목록 하나 + 논문의 Gemini 라벨)을 짝지어 비교한다."""
    cmps = []
    for i, a in enumerate(a_runs):
        if not (a["ok"] and a["schema_ok"]):
            continue
        if b_runs is not None:
            b = b_runs[i]
            if not (b["ok"] and b["schema_ok"]):
                continue
            other = b["parsed"]
        else:
            other = papers[i][key]
        c = compare(a["parsed"], other)
        if c:
            cmps.append(c)
    if not cmps:
        return None
    k = sum(c["type_match"] for c in cmps)
    n = len(cmps)
    lo, hi = wilson(k, n)
    return {
        "성과유형 일치": f"{k}/{n}",
        "비율": round(k / n, 3),
        "95%CI": [lo, hi],
        "수치 자카드": round(statistics.mean(c["metrics_jaccard"] for c in cmps), 3),
        "요약 유사도": round(statistics.mean(c["summary_sim"] for c in cmps), 3),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--think", default="none", help="none | low")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--temperature", type=float, default=None,
                    help="명시하지 않으면 제공자 기본값(Gemini 1.0 / Ollama 서버 0.8)")
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    think = False if args.think == "none" else args.think
    conc = args.concurrency or int(env("OLLAMA_CONCURRENCY", "2"))
    papers = fetch_papers(args.n, args.dsn)
    temp = "제공자 기본값" if args.temperature is None else args.temperature
    print(f"모델 {args.model} · think={args.think} · temperature={temp} · "
          f"동시 {conc} · 논문 {len(papers)}건 × 2회\n")

    runs = []
    for i in (1, 2):
        r, el = await run_condition(papers, args.model, think, conc, f"{i}회차",
                                    temperature=args.temperature)
        runs.append(r)
        print(f"  [{i}회차] {len(papers)}건 완료 — {el:.1f}초")

    self_ag = agreement(runs[0], runs[1])
    vs_gem = [agreement(r, None, papers) for r in runs]

    # 체계적 차이인지 노이즈인지: 두 모델의 라벨 분포를 나란히 본다.
    def dist(rs):
        return Counter(r["parsed"].get("achievement_type")
                       for r in rs if r["ok"] and r["schema_ok"])
    d_model = dist(runs[0]) + dist(runs[1])
    d_gem = Counter(p["gemini"]["achievement_type"] for p in papers)

    # 탈락 사유를 나눠 센다 — 분모로만 암시되면 "표본이 왜 줄었는지"를 나중에 알 수 없다.
    def drops(rs):
        return {"HTTP·네트워크 실패": sum(1 for r in rs if not r["ok"]),
                "JSON 아님 또는 필수키 누락": sum(1 for r in rs if r["ok"] and not r["schema_ok"])}

    report = {
        "model": args.model, "think": args.think, "temperature": args.temperature,
        "papers": len(papers),
        "탈락 (1회차)": drops(runs[0]), "탈락 (2회차)": drops(runs[1]),
        "자기 일치 (1회차↔2회차)": self_ag,
        "vs Gemini (1회차)": vs_gem[0],
        "vs Gemini (2회차)": vs_gem[1],
        "Gemini 자기 일치 기준선": 0.830,
        "라벨 분포 (후보, 2회 합산)": dict(d_model.most_common()),
        "라벨 분포 (Gemini)": dict(d_gem.most_common()),
    }
    out = REPO / (args.out or f"bench/results/selfagreement-{args.model.split(':')[0]}-{args.think}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"\n{'':<22}{'성과유형 일치':>16}{'95%CI':>18}{'수치 자카드':>12}")
    print("-" * 70)
    for label, v in (("자기 일치", self_ag), ("vs Gemini 1회", vs_gem[0]), ("vs Gemini 2회", vs_gem[1])):
        if v:
            print(f"{label:<22}{v['성과유형 일치']:>10} {v['비율']:>5}"
                  f"{str(v['95%CI']):>18}{v['수치 자카드']:>12}")
    print(f"{'Gemini 자기 일치':<22}{'':>10} {0.830:>5}{'[0.825, 0.835]':>18}")
    print(f"\n라벨 분포 — 후보: {dict(d_model.most_common(5))}")
    print(f"           Gemini: {dict(d_gem.most_common(5))}")
    print(f"\n{out.relative_to(REPO)}")


if __name__ == "__main__":
    asyncio.run(main())
