#!/usr/bin/env python3
"""같은 논문을 여러 번 추출해 지표를 합치면 누락이 회수되는가.

**이 수법이 성립하는 조건은 비용 구조다.** Ollama Cloud는 월 구독이라 재추출의
한계비용이 0이다. Gemini는 batch 실측 $0.000393/건이라 2패스는 곧 2배 청구서다
(172,220건 기준 $67.6 → $135). 즉 이것은 **구독 제공자만 쓸 수 있는 처방**이다.

성립할 만한 근거: pro:0813은 temperature 0에서도 자기 일치가 0.925다(5.6.2절).
서빙 단 비결정성이 남아 있으므로 **누락이 논문 고유가 아니라 확률적**이라면,
독립된 패스가 서로 다른 지표를 놓칠 것이고 합집합이 그 차이를 메운다.

반대로 누락이 논문 고유라면(그 초록의 그 수치를 늘 놓친다) 합집합은 아무것도
회수하지 못한다 — **그 자체가 유익한 음성 결과**다. 원인이 확률이 아니라 능력임을
말해주고, 그러면 think 수준이나 프롬프트로 가야 한다.

판정은 `blank_metric_audit`과 같다: 같은 논문에서 Gemini가 같은 지표를 근거 있는
값으로 채웠으면 그 수치는 초록에 있었던 것이고, 비운 것은 누락이다.

    PYTHONPATH=backend:bench backend/.venv/bin/python bench/multipass_union.py \\
        --n 200 --passes 3
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "bench"))
from blank_metric_audit import norm, same_metric  # noqa: E402
from ollama_extraction_bench import env, fetch_papers, run_condition  # noqa: E402
from temp0_fulltext import value_in_abstract  # noqa: E402


def merged_metrics(passes, i):
    """i번째 논문의 지표를 패스들에 걸쳐 합친다.

    **접는 기준은 정규화한 (이름, 단위, 값)의 완전 일치뿐이다.** 유사도로 접으면
    안 된다 — 모델 자기 출력 안에서는 비슷한 이름이 서로 다른 지표인 일이 흔하다
    (`평문 모델 정확도` / `암호문 추론 정확도`). 실측으로 유사도 병합은 값까지 보는
    회수율을 0.622 → 0.520으로 **떨어뜨렸다**: 서로 다른 지표를 한 행으로 접으면서
    살아남은 값이 엉뚱한 지표에 붙었기 때문이다. 정확 일치로 바꾸니 0.693이 됐다.

    유사도(`same_metric`)는 **신탁과 대조할 때만** 쓴다. 그쪽은 번역 표기가 갈리는
    서로 다른 모델의 출력을 맞추는 일이라 성격이 반대다.
    """
    seen, out = set(), []
    for rec in passes:
        r = rec[i]
        if not isinstance(r, dict):
            continue
        for m in (r.get("metrics") or []):
            if not isinstance(m, dict):
                continue
            key = (norm(str(m.get("name") or "")), norm(str(m.get("unit") or "")),
                   str(m.get("value") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            out.append(m)
    return out


def recall(get_metrics, papers, oracle):
    """신탁이 **근거 있는 값으로** 채운 지표를 몇 개나 되찾았는가.

    빈 행을 세는 방식(`audit`)은 합집합에서 왜곡된다 — 병합이 중복 행을 접으면서
    분모가 줄기 때문이다(실측: 합집합의 '값 채운 행'이 개별 패스보다 적게 나왔다).
    회수율은 **신탁의 지표 집합을 고정 분모로** 쓰므로 병합 방식에 영향받지 않는다."""
    total = hit = 0
    for i, (p, o) in enumerate(zip(papers, oracle)):
        ab = p["abstract"]
        truth = [m for m in ((o or {}).get("metrics") or [])
                 if isinstance(m, dict)
                 and value_in_abstract(str(m.get("value") or ""), ab) is True]
        mine = [m for m in get_metrics(i)
                if str(m.get("value") or "").strip()
                and value_in_abstract(str(m.get("value") or ""), ab) is not None]
        for q in truth:
            total += 1
            if any(same_metric(str(m.get("name") or ""), str(q.get("name") or ""),
                               str(m.get("unit") or ""), str(q.get("unit") or ""))
                   for m in mine):
                hit += 1
    return {"신탁 지표": total, "회수": hit,
            "회수율": round(hit / total, 3) if total else None}


_NUM = re.compile(r"\d+(?:\.\d+)?")


def recall_strict(get_metrics, papers, oracle):
    """이름뿐 아니라 **값의 숫자까지** 겹쳐야 회수로 센다.

    이름만 보면 병합이 지표를 뭉갤 때 회수율이 오히려 오른다(한 행이 여러 신탁
    지표의 이름에 걸리므로). 값까지 보면 그 왜곡이 드러난다 — 병합 규칙을 고를 때
    이 지표가 판정했다."""
    total = hit = 0
    for i, (p, o) in enumerate(zip(papers, oracle)):
        ab = p["abstract"]
        truth = [m for m in ((o or {}).get("metrics") or [])
                 if isinstance(m, dict)
                 and value_in_abstract(str(m.get("value") or ""), ab) is True]
        mine = [m for m in get_metrics(i) if str(m.get("value") or "").strip()]
        for q in truth:
            total += 1
            want = set(_NUM.findall(str(q.get("value") or "")))
            for m in mine:
                if not same_metric(str(m.get("name") or ""), str(q.get("name") or ""),
                                   str(m.get("unit") or ""), str(q.get("unit") or "")):
                    continue
                if set(_NUM.findall(str(m.get("value") or ""))) & want:
                    hit += 1
                    break
    return {"값까지 회수": hit, "값까지 회수율": round(hit / total, 3) if total else None}


def audit(get_metrics, papers, oracle):
    blank = miss = filled = 0
    for i, (p, o) in enumerate(zip(papers, oracle)):
        ab = p["abstract"]
        peers = [m for m in ((o or {}).get("metrics") or [])
                 if isinstance(m, dict)
                 and value_in_abstract(str(m.get("value") or ""), ab) is True]
        for m in get_metrics(i):
            if value_in_abstract(str(m.get("value") or ""), ab) is None:
                blank += 1
                if any(same_metric(str(m.get("name") or ""), str(q.get("name") or ""),
                                   str(m.get("unit") or ""), str(q.get("unit") or ""))
                       for q in peers):
                    miss += 1
            else:
                filled += 1
    return {"값 채운 행": filled, "빈 행": blank, "누락(하한)": miss}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--model", default="deepseek-v4-pro:0813")
    ap.add_argument("--think", default="none")
    ap.add_argument("--oracle", default="bench/results/temp0-fulltext-n400.json")
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--out", default="bench/results/multipass-union-pro0813.json")
    args = ap.parse_args()

    src = json.loads((REPO / args.oracle).read_text())
    okey = next(k for k in src["원시"][0] if k.startswith("gemini") and "0.0" in k)
    papers = fetch_papers(args.n, args.dsn)
    titles = {p["title"] for p in papers}
    oracle = [r[okey] for r in src["원시"] if r["title"] in titles][:args.n]
    if len(oracle) != len(papers):
        raise SystemExit(f"신탁 레코드 부족: {len(oracle)} != {len(papers)}")

    think = False if args.think == "none" else args.think
    conc = int(env("OLLAMA_CONCURRENCY", "2"))
    print(f"{args.model} · think={args.think} · 논문 {len(papers)}건 · "
          f"{args.passes}패스 · 동시 {conc} · 신탁 {okey}\n")

    passes, secs = [], []
    for k in range(args.passes):
        raw, el = await run_condition(papers, args.model, think, conc, f"{k + 1}패스",
                                      temperature=0.0)
        passes.append([r.get("parsed") if r.get("ok") and r.get("schema_ok") else None
                       for r in raw])
        secs.append(round(el, 1))

    report = {"model": args.model, "think": args.think, "papers": len(papers),
              "passes": args.passes, "패스별 소요(초)": secs, "결과": {}}
    def add(label, getter):
        report["결과"][label] = {**audit(getter, papers, oracle),
                                 **recall(getter, papers, oracle),
                                 **recall_strict(getter, papers, oracle)}

    for k in range(args.passes):
        add(f"{k + 1}패스 단독",
            lambda i, k=k: [m for m in ((passes[k][i] or {}).get("metrics") or [])
                            if isinstance(m, dict)])
    for k in range(2, args.passes + 1):
        add(f"합집합 1~{k}", lambda i, k=k: merged_metrics(passes[:k], i))
    add("신탁(gemini t=0.0)",
        lambda i: [m for m in ((oracle[i] or {}).get("metrics") or []) if isinstance(m, dict)])
    # 원시 레코드를 남긴다 — 집계만 두면 병합이 무엇을 뭉갰는지 사후에 볼 수 없다.
    report["원시"] = [{"title": p["title"],
                       "패스": [pa[i] for pa in passes],
                       "합집합": merged_metrics(passes, i)}
                      for i, p in enumerate(papers)]

    (REPO / args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    cols = list(report["결과"])
    keys = ["값 채운 행", "빈 행", "누락(하한)", "신탁 지표", "회수율", "값까지 회수율"]
    w = 16
    print(f"\n{'조건':<20}" + "".join(f"{k:>{w}}" for k in keys))
    print("-" * (20 + w * len(keys)))
    for c in cols:
        print(f"{c:<20}" + "".join(f"{report['결과'][c][k]:>{w}}" for k in keys))
    print(f"\n{args.out}")


if __name__ == "__main__":
    asyncio.run(main())
