#!/usr/bin/env python3
"""achievement_type이 어디서 흔들리는가 — Gemini가 자기 자신과 어긋나는 지점을 센다.

왜 이걸 보는가. `achievement_type`은 9지 택일 강제 분류인데 **Gemini가 같은 논문·같은
프롬프트로 자기 자신과 17% 어긋난다**(자기 일치 0.830). 이 정도 자기 불일치는 모델이
흔들리는 것이 아니라 **범주 정의가 서로 겹친다**는 뜻이다 — `MAP_INSTRUCTION`은 아홉
개를 나열할 뿐 무엇으로 가르는지 규칙도 예시도 주지 않는다.

이 값은 그냥 두면 안 되는 것이, `reducer.group_for_reduce`의 3단 reduce 그룹 분할 키이자
`stats.by_achievement_type`의 집계 축이라 **보고서의 구조와 통계가 같이 흔들린다.**
제공자를 바꾸든 안 바꾸든 손볼 값어치가 있는 이유다.

표본은 마이그레이션 `0021` **이전**의 `paper_extractions`다. 그때는 캐시 키에
subfield_id가 있어 같은 논문이 세부기술마다 따로 추출됐고, 그 형제 행이 곧 "같은 입력을
두 번 돌린 결과"다. 추출 프롬프트에 세부기술이 없으므로 두 결과의 차이는 순수한
샘플링 잡음이고, 여기서 나오는 혼동은 전적으로 범주 정의 탓이다.

    zcat ~/perfrev-extractions-2026-08-25.sql.gz \
      | docker exec -i performance-review-db-1 psql -U perfrev -d perfrev_base
    PYTHONPATH=backend backend/.venv/bin/python bench/achievement_type_confusion.py
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from app.prompts import MAP_SCHEMA  # noqa: E402

TYPES = MAP_SCHEMA["properties"]["achievement_type"]["enum"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev_base")
    ap.add_argument("--out", default="bench/results/achievement-type-confusion.json")
    args = ap.parse_args()

    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()
    cur.execute("""
        WITH ranked AS (
            SELECT paper_key, model_ver, achievement_type,
                   row_number() OVER (PARTITION BY paper_key, model_ver ORDER BY id) AS rn
            FROM paper_extractions
        )
        SELECT a.achievement_type, b.achievement_type
        FROM ranked a JOIN ranked b USING (paper_key, model_ver)
        WHERE a.rn = 1 AND b.rn = 2
    """)
    pairs = cur.fetchall()
    conn.close()

    # 두 행의 순서는 id 순일 뿐 의미가 없다 — 무순서 쌍으로 센다.
    unordered = Counter()
    appear = Counter()          # 각 유형이 쌍에 등장한 총 횟수(분모)
    agree = Counter()           # 그중 양쪽이 같았던 횟수
    for x, y in pairs:
        x = x or "(없음)"
        y = y or "(없음)"
        appear[x] += 1
        appear[y] += 1
        if x == y:
            agree[x] += 2
        else:
            unordered[tuple(sorted((x, y)))] += 1

    n = len(pairs)
    n_agree = sum(1 for x, y in pairs if x == y)

    # 유형별 안정도: 이 유형이 등장한 쌍 중 상대도 같았던 비율.
    stability = {
        t: {
            "등장": appear[t],
            "일치": agree[t],
            "안정도": round(agree[t] / appear[t], 3) if appear[t] else None,
        }
        for t in sorted(appear, key=lambda t: -appear[t])
    }

    # 각 유형이 가장 자주 혼동되는 상대
    partner = defaultdict(Counter)
    for (a, b), c in unordered.items():
        partner[a][b] += c
        partner[b][a] += c

    report = {
        "쌍 수": n,
        "일치": n_agree,
        "일치율": round(n_agree / n, 3),
        "유형별 안정도": stability,
        "혼동 쌍 상위": [
            {"쌍": f"{a} ↔ {b}", "건수": c, "전체 불일치 중": round(c / (n - n_agree), 3)}
            for (a, b), c in unordered.most_common(12)
        ],
        "유형별 최다 혼동 상대": {
            t: [f"{o}({c})" for o, c in partner[t].most_common(3)]
            for t in sorted(appear, key=lambda t: -appear[t])
        },
    }

    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"쌍 {n:,}건 · 일치 {n_agree:,} ({report['일치율']})\n")
    print(f"{'유형':<12}{'등장':>9}{'안정도':>9}   최다 혼동 상대")
    print("-" * 74)
    for t, v in stability.items():
        mates = ", ".join(report["유형별 최다 혼동 상대"][t])
        print(f"{t:<12}{v['등장']:>9,}{str(v['안정도']):>9}   {mates}")
    print(f"\n불일치 {n - n_agree:,}건의 구성 — 상위 혼동 쌍")
    print("-" * 74)
    for r in report["혼동 쌍 상위"]:
        print(f"  {r['쌍']:<28}{r['건수']:>7,}   전체 불일치의 {r['전체 불일치 중']:>6.1%}")
    print(f"\n{args.out}")


if __name__ == "__main__":
    main()
