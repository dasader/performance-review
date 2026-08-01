"""통계는 전부 코드로 집계한다 — 숫자를 LLM에 맡기면 틀린다.

검색된 전체 모집단(papers)과 실제 LLM 분석 대상(extractions)의 크기가 다르므로
searched_count / analyzed_count / no_abstract_count를 모두 노출해 보고서에서 구분한다.

같은 원칙을 결측치에도 적용한다: 소속(국가)/연도/저널 정보가 없는 논문을 조용히
분모나 집계에서 빼지 않고 no_country_count / no_year_count / no_journal_count로
드러낸다. 특히 intl_collab_ratio는 countries_json이 비어 있는(소속 정보 없음) 논문을
"국내 단독"으로 오인하지 않도록 분모에서 제외한다.
"""

import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime

from app.models.paper import Paper, PaperExtraction

TOP_N = 20
METRIC_TOP_N = 20


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[idx]


def _ranked(counter: Counter, n: int) -> list[tuple[str, int]]:
    """건수 내림차순 → 이름 오름차순으로 결정적 정렬 후 상위 n개."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


# 괄호 안에는 약어(PCE)나 조건(85°C)이 들어와 같은 지표를 쪼갠다 — 묶음 키에서는 떼어낸다.
_PAREN_RE = re.compile(r"\([^)]*\)")
_SEP_RE = re.compile(r"[\s_/·,]+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _metric_key(name: str) -> str:
    """지표명을 묶음 키로 정규화한다. 표시에는 쓰지 않는다(원본 표기를 따로 보존)."""
    return _SEP_RE.sub(" ", _PAREN_RE.sub(" ", name)).strip().lower()


def _metric_value(raw: object) -> float | None:
    """값 문자열에서 첫 숫자를 뽑는다. '~14' '1,200' '18.43'을 처리하고,
    숫자가 없으면 None — 집계에서 빠지되 metrics_total에는 남는다."""
    text = raw if isinstance(raw, str) else str(raw or "")
    match = _NUM_RE.search(text.replace(",", ""))
    return float(match.group()) if match else None


def aggregate_metrics(extractions: list[PaperExtraction]) -> dict:
    """추출된 정량 지표를 (지표명, 단위)로 묶어 분포를 낸다.

    이 모듈 첫 줄의 원칙("통계는 전부 코드로 집계한다")을 metrics에도 적용하는 것이다.
    LLM 보고서의 정량 표는 논문 수와 무관하게 11~12행에서 포화하므로(실측), 수치를
    서술에 맡기면 500건 이상에서 98.8%가 소실된다.

    단위가 다르면 환산하지 않고 별도 그룹으로 둔다 — μA/cm2와 A/cm2를 잘못 합치면
    1000배 어긋나고, 그 오류는 표에서 드러나지 않는다.
    """
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    labels: dict[tuple[str, str], Counter] = defaultdict(Counter)
    total = parsed = papers = 0

    for extraction in extractions:
        metrics = extraction.metrics_json or []
        if metrics:
            papers += 1
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            total += 1
            name = (metric.get("name") or "").strip()
            key_name = _metric_key(name)
            value = _metric_value(metric.get("value"))
            if not key_name or value is None:
                continue
            parsed += 1
            key = (key_name, (metric.get("unit") or "").strip())
            values[key].append(value)
            labels[key][name] += 1

    top = [
        {
            "name": labels[key].most_common(1)[0][0],
            "unit": key[1],
            "count": len(nums),
            "median": round(statistics.median(nums), 4),
            "p90": round(_percentile(nums, 0.9), 4),
            "max": round(max(nums), 4),
        }
        for key, nums in values.items()
        if len(nums) > 1  # 1회성 지표는 분포가 없다 — metrics_unique로 존재만 남긴다.
    ]
    top.sort(key=lambda row: (-row["count"], row["name"]))

    return {
        "metrics_total": total,
        "metrics_parsed": parsed,
        "metrics_papers": papers,
        "metrics_unique": sum(1 for nums in values.values() if len(nums) == 1),
        "top_metrics": top[:METRIC_TOP_N],
    }


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
            "p90": int(_percentile(citations, 0.9)),
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
        **aggregate_metrics(extractions),
        "snapshot_at": snapshot_at.isoformat(),
    }
