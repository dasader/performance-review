#!/usr/bin/env python3
"""자기 일치 결과를 한 표로 모은다 — 제공자 비교의 결론이 나오는 곳.

왜 이 표가 따로 필요한가. `compare_results.py`가 모으는 "vs Gemini 일치"는 **품질이
아니라 교체 가능성**이다. Gemini는 정답이 아니고 자기 자신과도 17% 어긋난다.
어느 모델이 더 나은 분류기인지는 **각자의 자기 일치**로만 답할 수 있고, 그 값들은
temperature·탈락률까지 함께 봐야 읽힌다 — 셋을 한 줄에 놓는 것이 이 스크립트다.

    PYTHONPATH=backend:bench backend/.venv/bin/python bench/selfagreement_table.py
"""
import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(d: Path) -> list[dict]:
    rows = []
    for f in sorted(d.glob("selfagreement-*.json")):
        r = json.loads(f.read_text())
        sa = r.get("자기 일치 (1회차↔2회차)") or {}
        if not sa:
            continue
        n_pair = int(sa["성과유형 일치"].split("/")[1])
        papers = r.get("papers", 0)
        rows.append({
            "모델": r["model"],
            "temperature": "기본" if r.get("temperature") is None else r["temperature"],
            "자기 일치": sa["비율"],
            "95%CI": sa["95%CI"],
            "유효 쌍": f"{n_pair}/{papers}",
            "탈락률": round(1 - n_pair / papers, 3) if papers else None,
            "vs Gemini": r.get("vs Gemini (1회차)", {}).get("비율"),
            "수치 자카드": sa.get("수치 자카드"),
        })
    # Gemini 쪽은 A/B 하네스가 남긴 형식이 달라 따로 읽는다.
    for f in sorted(d.glob("gemini-selfagreement-t*.json")):
        r = json.loads(f.read_text())
        v = (r.get("variants") or {}).get("A_현행") or {}
        if not v:
            continue
        pair = v["자기 일치"]                      # "200/200"
        n_pair, papers = (int(x) for x in pair.split("/"))
        rows.append({
            "모델": r.get("model", "gemini"),
            "temperature": "기본" if r.get("temperature") is None else r["temperature"],
            "자기 일치": v["자기 일치율"],
            "95%CI": v["95% 신뢰구간"],
            "유효 쌍": pair,
            "탈락률": round(1 - n_pair / papers, 3) if papers else None,
            "vs Gemini": None,
            "수치 자카드": None,
        })
    return sorted(rows, key=lambda r: -(r["자기 일치"] or 0))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="bench/results")
    args = ap.parse_args()
    rows = load(REPO / args.dir)
    if not rows:
        raise SystemExit("자기 일치 결과 JSON이 없습니다.")
    h = f"{'모델':<30}{'temp':>7}{'자기일치':>10}{'95%CI':>18}{'유효 쌍':>12}{'탈락률':>8}{'vsGem':>8}"
    print(h)
    print("-" * len(h))
    for r in rows:
        print(f"{r['모델']:<30}{str(r['temperature']):>7}{str(r['자기 일치']):>10}"
              f"{str(r['95%CI']):>18}{str(r['유효 쌍']):>12}"
              f"{str(r['탈락률']) if r['탈락률'] is not None else '-':>8}"
              f"{str(r['vs Gemini']) if r['vs Gemini'] is not None else '-':>8}")


if __name__ == "__main__":
    main()
