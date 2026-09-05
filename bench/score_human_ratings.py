#!/usr/bin/env python3
"""사람 판정 기록지를 채점한다 — 사람 대 각 방식·각 모델.

**이 스크립트는 판정을 다 마친 뒤에 돌린다.** 모델 답은 여기서 처음 열린다.
기록지(`make_rating_sheet.py`)는 맹검이고, 그 상태가 유지돼야 측정이 성립한다.

무엇을 재는가 — 그리고 재지 못하는 것.

  ○ 사람 대 모델의 **일치도**(원일치율 + Cohen's κ). κ를 함께 내는 이유는 원일치율이
    우연 일치를 포함하기 때문이다 — 3지에서 한쪽이 `데이터 없음`만 찍어도 60%가 나온다.
  ○ **★(헷갈림) 행과 모델 불일치 행의 겹침.** 사람이 어려워한 자리에서 모델도 갈렸다면
    **모델 문제가 아니라 척도 문제**다. 이 구분이 후속 조치를 정한다.
  ○ **정확도 상한 부등식.** 정답이 하나면 둘 다 맞을 때 반드시 일치하므로
    acc(A)+acc(B) ≤ 1+α다. 사람 라벨이 정답이 아니어도 성립한다.

  ✗ **"누가 옳은가"는 재지 못한다.** 평가자가 한 명이면 그 판정도 하나의 의견이다.
    두 번째 평가자가 있어야 사람끼리의 κ(=상한)가 나온다.

    PYTHONPATH=backend backend/.venv/bin/python bench/score_human_ratings.py \
        bench/판정기록지-반도체-2026-입력서식.txt
"""
import argparse
import json
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RES = REPO / "bench" / "results"

ABBR = {"확인": "관련 연구 확인", "부분": "부분 관련", "없음": "데이터 없음",
        "범위밖": "분석 범위 밖"}
VERDICTS = list(ABBR.values())


def read_human(path: Path) -> tuple[dict[int, str], set[int], dict[int, str]]:
    """입력 서식을 읽는다. `번호 | 판정 | ★ | 근거` 형식이고 `#` 줄은 무시한다."""
    v, hard, why = {}, set(), {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [c.strip() for c in s.split("|")]
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        i, lab = int(parts[0]), parts[1]
        if not lab:
            continue                      # 아직 안 채운 행
        if lab not in ABBR and lab not in VERDICTS:
            raise SystemExit(f"{i}행: 판정 '{lab}'을 모르겠습니다. "
                             f"{' / '.join(ABBR)} 중 하나로 적어 주세요.")
        v[i] = ABBR.get(lab, lab)
        if len(parts) > 2 and "★" in parts[2]:
            hard.add(i)
        if len(parts) > 3 and parts[3]:
            why[i] = parts[3]
    return v, hard, why


def kappa(a: dict[int, str], b: dict[int, str]) -> tuple[float, float, int]:
    """Cohen's κ. 원일치율은 우연 일치를 포함하므로 함께 본다."""
    ids = [i for i in a if i in b]
    n = len(ids)
    if n == 0:
        return 0.0, 0.0, 0
    po = sum(1 for i in ids if a[i] == b[i]) / n
    ca, cb = Counter(a[i] for i in ids), Counter(b[i] for i in ids)
    pe = sum(ca[k] / n * cb[k] / n for k in set(ca) | set(cb))
    return po, (po - pe) / (1 - pe) if pe < 1 else 1.0, n


def load_models() -> dict[str, dict[int, str]]:
    """이전 실행 결과에서 방식·모델별 행 단위 판정을 모은다."""
    out: dict[str, dict[int, str]] = {}
    f = RES / "onetable-vs-perrow-반도체-2026.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))["원자료"]
        for k, runs in d.items():
            for i, r in enumerate(runs):
                out[f"{k} {i+1}회차"] = {int(x): y for x, y in r.items()}
    f = RES / "roadmap-before-after-반도체-2026.json"
    if f.exists():
        md = json.loads(f.read_text(encoding="utf-8"))["신규 보고서"]
        rows, n, in_tbl = {}, 0, False
        for line in md.splitlines():
            s = line.strip()
            if not s.startswith("|"):
                in_tbl = False
                continue
            if re.match(r"^\|[\s:|-]+\|$", s):
                in_tbl = True
                continue
            if not in_tbl:
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            hit = [v for v in VERDICTS if any(v == c for c in cells)]
            n += 1
            if len(hit) == 1:
                rows[n] = hit[0]
        if rows:
            out["운영 신규(행 단위)"] = rows
    f = RES / "roadmap-panel-반도체-2026.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))["② 패널"]
        for key in ("만장일치 행", "불일치 행"):
            for r in d.get(key, []):
                for m, lab in r["표"].items():
                    out.setdefault("패널·" + m.split("/")[-1], {})[r["id"]] = lab
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet", type=Path)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    human, hard, why = read_human(a.sheet)
    if not human:
        raise SystemExit("채워진 행이 없습니다. `번호 | 판정 | ★ | 근거` 형식으로 적어 주세요.")
    models = load_models()
    if not models:
        raise SystemExit("비교할 모델 결과가 bench/results에 없습니다.")

    print(f"사람 판정 {len(human)}행" + (f" · 헷갈림 ★ {len(hard)}행" if hard else ""))
    print(f"  분포 {dict(Counter(human.values()))}\n")

    print("── 사람 대 각 방식 ────────────────────────────────────────")
    print(f"  {'대상':<26}{'원일치':>10}{'κ':>8}{'n':>5}")
    rows = []
    for name, mv in models.items():
        po, k, n = kappa(human, mv)
        rows.append((name, po, k, n))
    for name, po, k, n in sorted(rows, key=lambda r: -r[2]):
        print(f"  {name:<26}{po:>9.1%}{k:>8.3f}{n:>5}")

    print("\n── 정확도 상한 (정답이 하나면 둘 다 맞을 때 일치한다) ──────")
    for name, po, k, n in sorted(rows, key=lambda r: -r[1])[:5]:
        print(f"  사람 ↔ {name:<24} α={po:.3f} → 둘 중 하나는 정확도 ≤ {(1+po)/2:.1%}")

    if hard:
        print("\n── ★ 헷갈림 행과 모델 불일치의 겹침 ───────────────────")
        panel = {k: v for k, v in models.items() if k.startswith("패널·")}
        if len(panel) >= 2:
            split = {i for i in human
                     if len({v[i] for v in panel.values() if i in v}) > 1}
            both = hard & split
            print(f"  사람이 헷갈린 행 {len(hard)} · 패널이 갈린 행 {len(split)} · "
                  f"겹침 {len(both)}")
            if hard:
                print(f"  → 사람이 헷갈린 행 중 {len(both)/len(hard):.0%}에서 모델도 갈렸다")
            print("  겹치면 **척도 설계 문제**, 안 겹치면 **모델 능력 문제**에 가깝다.")

    print("\n── 사람과 갈린 행 (운영 신규 기준) ────────────────────────")
    new = models.get("운영 신규(행 단위)", {})
    diff = [(i, human[i], new[i]) for i in sorted(human) if i in new and human[i] != new[i]]
    for i, h, m in diff[:15]:
        star = " ★" if i in hard else "  "
        print(f"  [{i:>2}]{star} 사람={h:<10} 모델={m:<10} {why.get(i,'')[:38]}")
    print(f"  … 총 {len(diff)}행")

    out = {
        "사람 분포": dict(Counter(human.values())),
        "헷갈림 행": sorted(hard),
        "대상별": {n: {"원일치": round(p, 3), "kappa": round(k, 3), "n": c}
                   for n, p, k, c in rows},
        "사람과 갈린 행(운영 신규)": [{"id": i, "사람": h, "모델": m} for i, h, m in diff],
    }
    p = Path(a.out or RES / "human-vs-models-반도체-2026.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {p}")


if __name__ == "__main__":
    main()
