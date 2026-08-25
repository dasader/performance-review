#!/usr/bin/env python3
"""Gemini가 **같은 논문을 두 번 뽑았을 때** 자기 자신과 얼마나 일치하는가.

다른 모델과의 일치율은 이 값 없이는 읽을 수 없다. LLM 추출은 비결정적이라 같은 모델·
같은 프롬프트·같은 초록으로도 매번 다른 문장이 나온다 — "Ollama가 Gemini와 요약
유사도 0.21"이 나빠 보여도, Gemini끼리가 0.25라면 그건 모델 차이가 아니라 원래
있던 샘플링 잡음이다.

표본은 마이그레이션 0021 **이전**의 paper_extractions다. 그때는 캐시 키에 subfield_id가
있어 같은 논문이 세부기술마다 따로 추출됐고(19,904그룹), 그 형제 행들이 곧 "같은 입력을
두 번 돌린 결과"다. 0021이 그 중복을 지웠으므로 운영 DB로는 더 이상 잴 수 없다 —
배포 전 백업을 스크래치 DB에 올려서 쓴다.

    zcat ~/perfrev-extractions-YYYY-MM-DD.sql.gz | \
      docker exec -i performance-review-db-1 psql -U perfrev -d perfrev_base
    PYTHONPATH=backend backend/.venv/bin/python bench/gemini_selfagreement_baseline.py

비교 함수는 ollama_extraction_bench와 **같은 것을 import해서** 쓴다. 따로 구현하면
기준선과 측정치가 다른 자로 잰 값이 되어 비교가 성립하지 않는다.
"""
import argparse
import json
import statistics
from pathlib import Path

import psycopg2

from ollama_extraction_bench import compare  # 같은 자로 잰다

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev_base")
    ap.add_argument("--out", default="bench/results/gemini-selfagreement.json")
    args = ap.parse_args()

    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()
    # 그룹당 가장 오래된 두 행을 짝지어 비교한다(3개 이상인 그룹도 첫 둘만).
    cur.execute("""
        WITH ranked AS (
            SELECT paper_key, model_ver, tech_summary, achievement_type, metrics_json,
                   row_number() OVER (PARTITION BY paper_key, model_ver ORDER BY id) AS rn
            FROM paper_extractions
        )
        SELECT a.tech_summary, a.achievement_type, a.metrics_json,
               b.tech_summary, b.achievement_type, b.metrics_json
        FROM ranked a
        JOIN ranked b USING (paper_key, model_ver)
        WHERE a.rn = 1 AND b.rn = 2
    """)
    rows = cur.fetchall()
    conn.close()

    cmps = []
    for ats, aat, amj, bts, bat, bmj in rows:
        c = compare(
            {"tech_summary": ats, "achievement_type": aat, "metrics": amj or []},
            {"tech_summary": bts, "achievement_type": bat, "metrics": bmj or []},
        )
        if c:
            cmps.append(c)

    n = len(cmps)
    report = {
        "설명": "Gemini 자기 일치율 — 같은 모델이 같은 논문을 두 번 뽑은 결과끼리",
        "쌍 수": n,
        "성과유형 일치": round(sum(c["type_match"] for c in cmps) / n, 3),
        "수치유무 일치": round(sum(c["metrics_presence_match"] for c in cmps) / n, 3),
        "수치 자카드": round(statistics.mean(c["metrics_jaccard"] for c in cmps), 3),
        "요약 유사도": round(statistics.mean(c["summary_sim"] for c in cmps), 3),
    }
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    for k, v in report.items():
        print(f"{k:<16} {v}")
    print(f"\n{args.out}")


if __name__ == "__main__":
    main()
