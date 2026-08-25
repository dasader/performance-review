#!/usr/bin/env python3
"""results/*.json을 한 표로 모은다.

손으로 옮겨 적으면 틀린다 — 모델 × 사고수준 × 지표 십수 개라 한 칸만 어긋나도
결론이 뒤집힌다. Gemini 자기 일치 기준선(gemini-selfagreement.json)이 있으면
비교 대상 열에 함께 세워, 어떤 값이 "모델 차이"이고 어떤 값이 "원래 있던 잡음"인지
표에서 바로 읽히게 한다.

    PYTHONPATH=backend backend/.venv/bin/python bench/compare_results.py
"""
import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (표시 이름, 결과 JSON의 키). 기준선과 나란히 놓을 것만 고른다.
ROWS = [
    ("논문/분", "논문/분"),
    ("총 소요(초)", "총 소요(초)"),
    ("처리량(tok/s)", "처리량(tok/s)"),
    ("건당 지연 중앙값(초)", "건당 지연 중앙값(초)"),
    ("건당 입력토큰", "건당 입력토큰 평균"),
    ("건당 출력토큰", "건당 출력토큰 평균"),
    ("thinking 문자", "thinking 문자 평균"),
    ("—형식—", None),
    ("껍데기 없이 순수 JSON", "껍데기 없이 순수 JSON"),
    ("코드펜스로 감쌈", "코드펜스로 감쌈"),
    ("스키마 준수", "스키마 준수"),
    ("metrics 타입위반 논문", "metrics 타입위반 논문"),
    ("빈 이름 지표 채움", "빈 이름 지표를 채운 논문"),
    ("enum 이탈", "achievement_type enum 이탈"),
    ("—내용(vs Gemini)—", None),
    ("성과유형 일치", "vs Gemini 성과유형 일치"),
    ("수치유무 일치", "vs Gemini 수치유무 일치"),
    ("수치 자카드", "vs Gemini 수치 자카드"),
    ("요약 유사도", "vs Gemini 요약 유사도"),
    ("한국어 비율", "한국어 비율 평균"),
    ("—사고수준 영향—", None),
    ("low↔none 성과유형", "low↔none 성과유형 일치"),
    ("low↔none 수치 자카드", "low↔none 수치 자카드"),
]

# 기준선 JSON의 키 → 위 ROWS의 표시 이름
BASE_MAP = {
    "성과유형 일치": "성과유형 일치",
    "수치유무 일치": "수치유무 일치",
    "수치 자카드": "수치 자카드",
    "요약 유사도": "요약 유사도",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="bench/results")
    args = ap.parse_args()
    d = REPO / args.dir

    base = {}
    bp = d / "gemini-selfagreement.json"
    if bp.exists():
        raw = json.loads(bp.read_text())
        base = {BASE_MAP[k]: v for k, v in raw.items() if k in BASE_MAP}

    cols, data = [], {}
    for f in sorted(d.glob("*.json")):
        if f.name == "gemini-selfagreement.json":
            continue
        r = json.loads(f.read_text())
        for think in ("none", "low"):
            key = f"{r['model'].split(':')[0]}\n{think}"
            cols.append(key)
            data[key] = r[f"think_{think}"]

    if not cols:
        raise SystemExit(f"{args.dir}에 결과 JSON이 없습니다.")

    w = 22
    head = f"{'지표':<22}" + "".join(f"{c.replace(chr(10), '/'):>{w}}" for c in cols)
    if base:
        head += f"{'Gemini자기일치':>{w}}"
    print(head)
    print("-" * len(head))
    for label, key in ROWS:
        if key is None:
            print(label)
            continue
        line = f"{label:<22}" + "".join(f"{str(data[c].get(key, '-')):>{w}}" for c in cols)
        if base:
            line += f"{str(base.get(label, '-')):>{w}}"
        print(line)


if __name__ == "__main__":
    main()
