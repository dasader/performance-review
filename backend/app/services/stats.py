"""통계는 전부 코드로 집계한다 — 숫자를 LLM에 맡기면 틀린다.

검색된 전체 모집단(papers)과 실제 LLM 분석 대상(extractions)의 크기가 다르므로
searched_count / analyzed_count / no_abstract_count를 모두 노출해 보고서에서 구분한다.

같은 원칙을 결측치에도 적용한다: 소속(국가)/연도/저널 정보가 없는 논문을 조용히
분모나 집계에서 빼지 않고 no_country_count / no_year_count / no_journal_count로
드러낸다. 특히 intl_collab_ratio는 countries_json이 비어 있는(소속 정보 없음) 논문을
"국내 단독"으로 오인하지 않도록 분모에서 제외한다.
"""

import math
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
# ".5"처럼 정수부가 없는 값도 있다(실측).
_NUM_RE = re.compile(r"-?(?:\d+(?:\.\d+)?|\.\d+)")


def _metric_key(name: str) -> str:
    """지표명을 묶음 키로 정규화한다. 표시에는 쓰지 않는다(원본 표기를 따로 보존)."""
    return _SEP_RE.sub(" ", _PAREN_RE.sub(" ", name)).strip().lower()


# 범위 표기("4-6", "70~600", "40–55", "-20~20"). 하한과 상한을 함께 잡아 중간값을 쓴다.
# 구분자는 하이픈·물결·엔대시·엠대시. 상한의 부호는 받지 않는다 — "-20-10"은 실제로
# "-20 ~ -10"인지 "-20 ~ 10"인지 알 수 없어, 모호한 쪽은 범위로 읽지 않고 첫 숫자만 쓴다.
_RANGE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[-~\u2013\u2014]\s*(\d+(?:\.\d+)?)\s*$")


# 지수 표기 3종. 실측(v3 추출 44,225개 값): 2.34%(1,033건)가 이 형태이고, 지표에 따라
# (예: 내구성) 거의 전부다. 밑수만 읽으면 10^3~10^12가 전부 10이 되어 분포가 무너진다
# — 사용자 신고로 드러났다(내구성 15건이 최소 2 / 중앙값 10 / 최대 1,000으로 나왔다).
#
# 구분자가 ×(U+00D7)·x·X·* 로 섞여 있고 음수 지수도 있다(10^-5, -1.57E-03).
_MANTISSA_EXP_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*[×xX*]\s*10\s*\^?\s*(-?\d+)"
)
_POWER10_RE = re.compile(r"10\s*\^\s*(-?\d+)")
_E_NOTATION_RE = re.compile(r"-?\d+(?:\.\d+)?[eE][-+]?\d+")


# 값이 숫자로 시작하는가. 아니면 집계에서 뺀다.
#
# 첫 숫자를 집는 규칙은 "12.5 %"처럼 단위가 붙은 값을 위한 것인데, 값 자리에 수식이나
# 서술이 들어오면 엉뚱한 수를 집는다 — 실측(v3 추출 44,225개 값): 숫자로도 부호로도
# 시작하지 않는 값이 104건(0.24%)이고 그 대부분이 그런 형태다:
#   "DOE 2025 목표 상회"        → 2025
#   "SS316L > N10003 > C-276"  → 316
#   "log2(n)" · "O(2^n)"        → 2
#   "(nd)^{1/4}/sqrt(ε)"        → 1
#
# 허용하는 선두: 공백, "약", 부호·근사·부등호(-+~<>≤≥≈), 그리고 숫자(또는 .5).
# ±는 넣지 않았다 — "±0.15"는 값이 아니라 폭이라 위치가 없고, 분포에 넣으면 그 지표의
# 수준을 잘못 말한다(실측 42건). 되돌리려면 이 문자열에 ±를 더하면 된다.
_NUMERIC_START_RE = re.compile(r"^\s*(?:약\s*)?[-+~<>≤≥≈]?\s*(?:\d|\.\d)")

# float 상한(~1.8e308)을 넘는 지수는 값이 아니라 오독이다.
_MAX_EXP10 = 300


def _exp10(mantissa: float, exp: int) -> float | None:
    """mantissa × 10^exp. float 범위 밖이면 None — 집계에서 빠지되 분모에는 남는다.

    LLM이 넣는 지수에는 자릿수 제한이 없다. 실측(2026-08-23, 데이터·AI 보안 CN 2026):
    추출값 "10^216742" 하나가 10.0 ** 216742에서 OverflowError를 내
    stats.compute → runner._do_reduce를 통째로 죽였고, 검색 4,321건짜리 분석이
    failed로 끝났다. 그 값은 paper_extractions 캐시에 남으므로 재실행해도 같은
    자리에서 다시 죽는다 — 예외를 삼키는 것이 아니라 값 자체를 거르는 이유다.

    지수 표기 두 분기(10^N, N × 10^M)가 **모두** 이 함수를 통과해야 한다.
    한쪽만 막으면 다른 쪽 표기 하나로 같은 사고가 그대로 재현된다.
    """
    if not -_MAX_EXP10 <= exp <= _MAX_EXP10:
        return None
    return mantissa * 10.0 ** exp


def _metric_value(raw: object) -> float | None:
    """값 문자열에서 대표 숫자를 뽑는다. 숫자가 없거나 값이 숫자로 시작하지 않으면
    None — 집계에서 빠지되 metrics_total에는 남아 분모를 속이지 않는다.

    **가장 앞에 나오는 표기를 쓴다.** 지수를 이해하되 "앞의 수를 쓴다"는 기존 규칙을
    뒤집지 않기 위해서다 — "2.5 GHz, 10^3 cycles"는 2.5다. 각 표기의 위치를 재서
    제일 앞선 것을 고른다.

    범위 표기는 중간값을 쓴다. 실측: 5.09%가 범위이고 그중 35.9%는 상한이 하한의
    2배 이상이라(평균 16배) 하한만 취하면 체계적으로 낮게 잡힌다. 다만 지수를 낀
    범위("0.8 x 10^-9 ~ 13.2 x 10^-9")는 하한을 쓴다 — 지수 안의 음수 부호와 범위
    구분자를 구별할 수 없어, 잘못 쪼개느니 앞의 값을 쓰는 편이 안전하다.
    """
    text = raw if isinstance(raw, str) else str(raw or "")
    text = text.replace(",", "")
    if not _NUMERIC_START_RE.match(text):
        return None

    # 지수 표기부터. 위치가 가장 앞선 것을 고른다.
    # 범위를 벗어난 지수는 후보에서 빼는 것이 아니라 **None인 채로** 넣는다 —
    # 그래야 아래 "앞의 수" 규칙이 그대로 돌면서도, 그 지수가 승자일 때 값 전체를
    # 버릴 수 있다. 그냥 빼면 _NUM_RE가 "10^216742"의 앞 "10"을 집어 10.0을 값으로
    # 삼는다: 터지지 않을 뿐, 분포를 조용히 망가뜨리는 더 나쁜 결과다.
    candidates: list[tuple[int, float | None]] = []
    m = _MANTISSA_EXP_RE.search(text)
    if m:
        candidates.append((m.start(), _exp10(float(m.group(1)), int(m.group(2)))))
    m = _POWER10_RE.search(text)
    if m:
        candidates.append((m.start(), _exp10(1.0, int(m.group(1)))))
    m = _E_NOTATION_RE.search(text)
    if m:
        # float("1e999")는 예외가 아니라 inf를 준다. 그대로 두면 stats_json에 실려
        # json.dumps가 표준이 아닌 Infinity를 뱉는다 — _exp10과 같은 이유로 버린다.
        e_value = float(m.group(0))
        candidates.append((m.start(), e_value if math.isfinite(e_value) else None))

    plain = _NUM_RE.search(text)
    if candidates:
        # 값에 None이 섞이므로 위치로만 비교한다(튜플 비교는 start가 같을 때 터진다).
        start, value = min(candidates, key=lambda c: c[0])
        # 평범한 숫자가 더 앞에 있으면 그것이 답이다(위 docstring의 "앞의 수" 규칙).
        # 단 그 숫자가 지수 표기의 일부라면(2 × 10^6의 2) 지수 쪽이 맞다.
        if plain and plain.start() < start:
            return float(plain.group())
        return value

    span = _RANGE_RE.match(text)
    if span:
        return (float(span.group(1)) + float(span.group(2))) / 2
    return float(plain.group()) if plain else None


def _round4(x: float) -> float:
    """유효숫자 4자리로 반올림한다.

    round(x, 4)를 쓰면 작은 값이 통째로 0이 된다 — 1e-9 → 0.0. 지수 표기를 제대로
    읽기 시작하면서 그런 값이 실제로 들어온다(실측: 10^-4 이하가 244건). 자릿수가
    아니라 유효숫자로 잘라야 크기를 보존한다.
    """
    return float(f"{x:.4g}")


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
            # 분포는 최소~중앙값~최대 범위로 보여준다. p90은 쓰지 않는다 —
            # _percentile의 인덱스가 int(n*0.9) 내림이라 n<=10이면 항상 마지막 원소를
            # 가리켜 최대값과 **같은 값**이 된다. 실측(저장된 지표 행 1,687개 중
            # 1,523개 = 90.3%)에서 p90 == max였고, 같은 숫자가 두 열에 나와 서로 다른
            # 통계인 것처럼 보였다. 범위는 표본이 둘이어도 성립해 하한을 둘 필요가 없다.
            # 인용수 p90(compute의 citations)은 표본이 수백~수천이라 문제가 없어 그대로 둔다.
            "min": _round4(min(nums)),
            "median": _round4(statistics.median(nums)),
            "max": _round4(max(nums)),
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
    country: str = "KR",
    population_total: int | None = None,
) -> dict:
    citations = [p.citations or 0 for p in papers]
    partner_counter: Counter = Counter()
    attribution: Counter = Counter()
    intl = 0
    with_country = 0
    for p in papers:
        countries = p.countries_json or []
        if not countries:
            continue
        with_country += 1
        others = [c for c in countries if c != country]
        if others:
            intl += 1
            partner_counter.update(others)

        # 참여 기준으로 수집하되 주도 여부를 병기한다. 둘을 구분하지 않으면 국가별
        # 숫자를 같은 의미로 오독한다 — 실측으로 일본 논문의 47%가 자국이 주도하지
        # 않은 국제공동연구이고 중국은 7.5%뿐이다.
        leads = p.lead_countries_json or []
        if not others:
            attribution["단독"] += 1
        elif not leads:
            attribution["주도 미상"] += 1   # is_corresponding 미보유 6~9%
        elif country in leads:
            attribution["주도"] += 1
        else:
            attribution["참여"] += 1

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
        "attribution": dict(attribution),
        # 상한에 걸려 잘렸는지. 표본과 전수를 나란히 놓으면 인용수가 구조적으로
        # 부풀려지므로 반드시 드러낸다(국가 비교 보고서가 이 값을 읽어 경고한다).
        "population_total": population_total if population_total is not None else len(papers),
        "sampled": bool(population_total is not None and population_total > len(papers)),
        **aggregate_metrics(extractions),
        "snapshot_at": snapshot_at.isoformat(),
    }
