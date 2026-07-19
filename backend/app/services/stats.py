"""통계는 전부 코드로 집계한다 — 숫자를 LLM에 맡기면 틀린다.

검색된 전체 모집단(papers)과 실제 LLM 분석 대상(extractions)의 크기가 다르므로
searched_count / analyzed_count / no_abstract_count를 모두 노출해 보고서에서 구분한다.
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


def compute(
    papers: list[Paper],
    extractions: list[PaperExtraction],
    *,
    snapshot_at: datetime,
) -> dict:
    citations = [p.citations or 0 for p in papers]
    partner_counter: Counter = Counter()
    intl = 0
    for p in papers:
        others = [c for c in (p.countries_json or []) if c != "KR"]
        if others:
            intl += 1
            partner_counter.update(others)

    top_cited = sorted(papers, key=lambda p: p.citations or 0, reverse=True)[:10]

    return {
        "searched_count": len(papers),
        "analyzed_count": len(extractions),
        "no_abstract_count": sum(1 for p in papers if not p.abstract),
        "by_year": dict(sorted(Counter(p.year for p in papers if p.year).items())),
        "by_source": dict(Counter(p.source for p in papers)),
        "top_institutions": Counter(
            i for p in papers for i in (p.institutions_json or [])
        ).most_common(TOP_N),
        "top_journals": Counter(p.journal for p in papers if p.journal).most_common(TOP_N),
        "top_authors": Counter(
            a for p in papers for a in (p.authors_json or [])
        ).most_common(TOP_N),
        "intl_collab_ratio": round(intl / len(papers), 4) if papers else 0.0,
        "top_partner_countries": partner_counter.most_common(10),
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
