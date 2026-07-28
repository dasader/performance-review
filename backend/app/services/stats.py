"""통계는 전부 코드로 집계한다 — 숫자를 LLM에 맡기면 틀린다.

검색된 전체 모집단(papers)과 실제 LLM 분석 대상(extractions)의 크기가 다르므로
searched_count / analyzed_count / no_abstract_count를 모두 노출해 보고서에서 구분한다.

같은 원칙을 결측치에도 적용한다: 소속(국가)/연도/저널 정보가 없는 논문을 조용히
분모나 집계에서 빼지 않고 no_country_count / no_year_count / no_journal_count로
드러낸다. 특히 intl_collab_ratio는 countries_json이 비어 있는(소속 정보 없음) 논문을
"국내 단독"으로 오인하지 않도록 분모에서 제외한다.
"""

import statistics
from collections import Counter
from datetime import datetime

from app.models.paper import Paper, PaperExtraction

TOP_N = 20


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[idx]


def _ranked(counter: Counter, n: int) -> list[tuple[str, int]]:
    """건수 내림차순 → 이름 오름차순으로 결정적 정렬 후 상위 n개."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def compute(
    papers: list[Paper],
    extractions: list[PaperExtraction],
    *,
    snapshot_at: datetime,
) -> dict:
    citations = [p.citations or 0 for p in papers]
    partner_counter: Counter = Counter()
    intl = 0
    with_country = 0
    for p in papers:
        countries = p.countries_json or []
        if not countries:
            continue
        with_country += 1
        others = [c for c in countries if c != "KR"]
        if others:
            intl += 1
            partner_counter.update(others)

    top_cited = sorted(
        papers, key=lambda p: (-(p.citations or 0), p.title or "")
    )[:10]

    return {
        "searched_count": len(papers),
        "analyzed_count": len(extractions),
        "no_abstract_count": sum(1 for p in papers if not p.abstract),
        "no_year_count": sum(1 for p in papers if not p.year),
        "no_journal_count": sum(1 for p in papers if not p.journal),
        "no_country_count": sum(1 for p in papers if not p.countries_json),
        "by_year": dict(sorted(Counter(p.year for p in papers if p.year).items())),
        "by_source": dict(Counter(p.source for p in papers)),
        "top_institutions": _ranked(
            Counter(i for p in papers for i in (p.institutions_json or [])), TOP_N
        ),
        "top_journals": _ranked(
            Counter(p.journal for p in papers if p.journal), TOP_N
        ),
        "top_authors": _ranked(
            Counter(a for p in papers for a in (p.authors_json or [])), TOP_N
        ),
        "intl_collab_ratio": round(intl / with_country, 4) if with_country else 0.0,
        "top_partner_countries": _ranked(partner_counter, 10),
        "citations": {
            "median": int(statistics.median(citations)) if citations else 0,
            "p90": _percentile(citations, 0.9),
            "total": sum(citations),
        },
        "top_cited": [
            {"title": p.title, "citations": p.citations or 0, "year": p.year,
             "journal": p.journal, "doi": p.doi}
            for p in top_cited
        ],
        "by_achievement_type": dict(
            Counter(e.achievement_type for e in extractions if e.achievement_type)
        ),
        "snapshot_at": snapshot_at.isoformat(),
    }
