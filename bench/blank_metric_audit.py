#!/usr/bin/env python3
"""값이 빈 `metrics` 행을 **행 단위로** 판정한다 — 정당한 공백인가, 누락인가.

앞선 판정은 **논문 단위**였다: "초록에 단위 붙은 수치가 있으면 그 논문의 빈 행은
전부 누락". 이것이 양방향으로 틀린다.

- **과대**: 초록이 정확도 98%만 말하고 지연시간은 말하지 않는데 모델이 두 행을
  만들면, 지연시간의 빈 값도 누락으로 세어진다. 실제로는 정당한 공백이다.
- **과소**: `5,673`(쉼표)이나 단위 없는 숫자를 정규식이 못 잡아 "초록에 수치 없음"
  으로 보고 정당으로 넣는다. 실측으로 최소 7건이 이렇게 잘못 분류됐다.

**행 단위로 판정하려면 "이 지표의 값이 초록에 있었는가"를 물어야 한다.** 그런데
초록은 영문이고 지표명은 국문 번역이라 키워드 매칭이 성립하지 않는다.

그래서 **동료 모델을 신탁으로 쓴다**: 같은 논문에서 다른 모델이 *같은 지표*를
*근거 있는 값*으로 채웠다면 그 수치는 초록에 있었던 것이고, 비운 쪽이 놓친 것이다.
지표명은 셋 다 국문이라 비교가 성립한다.

이 판정은 **누락의 하한**이다 — 두 모델이 함께 놓친 수치는 잡히지 않는다.
과대 계상하지 않는다는 뜻이므로 "적어도 이만큼은 누락"으로 읽으면 된다.

    PYTHONPATH=backend:bench backend/.venv/bin/python bench/blank_metric_audit.py \\
        --in bench/results/temp0-fulltext-n400.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "bench"))
from ollama_extraction_bench import _similarity, fetch_papers  # noqa: E402
from temp0_fulltext import value_in_abstract  # noqa: E402

# 지표명 정규화 — 공백·괄호주석·영문 약어가 표기 차이를 만든다.
# "출력전압 리플" vs "출력 전압 리플", "희토류 산화물(REO) 품위" vs "희토류산화물(REO) 함량"
_PAREN = re.compile(r"[(（][^)）]*[)）]")
_NONWORD = re.compile(r"[^0-9A-Za-z가-힣]+")


def norm(name: str) -> str:
    return _NONWORD.sub("", _PAREN.sub("", name or "")).lower()


def same_metric(a: str, b: str, ua: str, ub: str) -> bool:
    """두 지표명이 같은 것을 가리키는가. 단위가 다르면 다른 지표로 본다."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if norm(ua) and norm(ub) and norm(ua) != norm(ub):
        return False
    if na == nb or na in nb or nb in na:
        return True
    return _similarity(na, nb) >= 0.5


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="bench/results/temp0-fulltext-n400.json")
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--show", type=int, default=6)
    args = ap.parse_args()

    d = json.loads((REPO / args.src).read_text())
    raw = d["원시"]
    conds = [k for k in raw[0] if k != "title"]
    AB = {p["title"]: p["abstract"] for p in fetch_papers(len(raw), args.dsn)}
    ANY_NUM = re.compile(r"\d")

    out = {}
    examples = {c: [] for c in conds}
    for c in conds:
        miss = fair = no_num = 0
        papers_miss = set()
        for r in raw:
            ab = AB[r["title"]]
            rows = [m for m in ((r[c] or {}).get("metrics") or []) if isinstance(m, dict)]
            # 동료가 이 논문에서 근거 있는 값으로 채운 지표들
            peers = [
                m for o in conds if o != c and r[o]
                for m in (r[o].get("metrics") or [])
                if isinstance(m, dict)
                and value_in_abstract(str(m.get("value") or ""), ab) is True
            ]
            for m in rows:
                if value_in_abstract(str(m.get("value") or ""), ab) is not None:
                    continue
                nm, un = str(m.get("name") or ""), str(m.get("unit") or "")
                hit = next((p for p in peers
                            if same_metric(nm, str(p.get("name") or ""), un,
                                           str(p.get("unit") or ""))), None)
                if hit:
                    miss += 1
                    papers_miss.add(r["title"])
                    if len(examples[c]) < args.show:
                        examples[c].append(
                            f"{r['title'][:52]} | {nm}=(빈칸) ← 동료: "
                            f"{hit.get('name')}={hit.get('value')}{hit.get('unit') or ''}")
                else:
                    fair += 1
                    if not ANY_NUM.search(ab):
                        no_num += 1
        out[c] = {"빈 행": miss + fair, "누락(하한)": miss, "누락 논문": len(papers_miss),
                  "정당/미상": fair, "└ 초록에 숫자 자체가 없음": no_num}

    w = 26
    keys = list(out[conds[0]])
    print(f"{'지표':<26}" + "".join(f"{c:>{w}}" for c in conds))
    print("-" * (26 + w * len(conds)))
    for k in keys:
        print(f"{k:<26}" + "".join(f"{out[c][k]:>{w}}" for c in conds))
    print("\n(누락은 하한 — 두 모델이 함께 놓친 수치는 잡히지 않는다)")
    for c in conds:
        if examples[c]:
            print(f"\n[{c}] 누락 예")
            for e in examples[c]:
                print(f"  · {e}")


if __name__ == "__main__":
    main()
