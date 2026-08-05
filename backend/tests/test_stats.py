from datetime import datetime

from app.models.paper import Paper, PaperExtraction
from app.services import stats


def _p(key, **kw):
    defaults = dict(paper_key=key, title="T", abstract="A", year=2025, journal="J",
                    authors_json=["김"], institutions_json=["KAIST"], countries_json=["KR"],
                    citations=0, source="openalex", lead_countries_json=[])
    defaults.update(kw)
    return Paper(**defaults)


def test_counts_separate_searched_from_analyzed():
    papers = [_p("a"), _p("b", abstract=""), _p("c")]
    ext = [PaperExtraction(paper_key="a", subfield_id=1, tech_summary="x", model_ver="m")]
    s = stats.compute(papers, ext, snapshot_at=datetime(2026, 7, 18))
    assert s["searched_count"] == 3
    assert s["no_abstract_count"] == 1
    assert s["analyzed_count"] == 1


def test_by_year_and_source_counts():
    papers = [_p("a", year=2024), _p("b", year=2025), _p("c", year=2025, source="kci")]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 7, 18))
    assert s["by_year"] == {2024: 1, 2025: 2}
    assert s["by_source"] == {"openalex": 2, "kci": 1}


def test_by_source_distinguishes_both_from_single_source():
    """I10: 양쪽 소스에서 발견된 논문(source="both")과 한쪽에서만 발견된 논문이
    by_source에서 구분돼야 "KCI에 있는 논문 수"(kci+both)와 "국제지에만 있는 논문
    수"(openalex only)를 각각 계산할 수 있다."""
    papers = [
        _p("a", source="both"), _p("b", source="both"),
        _p("c", source="openalex"), _p("d", source="kci"),
    ]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 7, 18))
    assert s["by_source"] == {"both": 2, "openalex": 1, "kci": 1}


def test_international_collaboration_ratio_and_partners():
    papers = [
        _p("a", countries_json=["KR"]),
        _p("b", countries_json=["KR", "US"]),
        _p("c", countries_json=["KR", "US", "JP"]),
    ]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 7, 18))
    assert s["intl_collab_ratio"] == round(2 / 3, 4)
    assert s["top_partner_countries"][0] == ("US", 2)


def test_no_country_papers_excluded_from_ratio_denominator_and_counted():
    papers = [
        _p("a", countries_json=["KR"]),          # 국내 단독
        _p("b", countries_json=["KR", "US"]),    # 국제공동
        _p("c", countries_json=[]),              # 소속 정보 없음
        _p("d", countries_json=[]),              # 소속 정보 없음
    ]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 7, 18))
    # 분모는 countries_json이 비어있지 않은 논문(2건)만: 1/2
    assert s["intl_collab_ratio"] == round(1 / 2, 4)
    assert s["no_country_count"] == 2


def test_intl_collab_ratio_zero_when_all_papers_missing_country():
    papers = [_p("a", countries_json=[]), _p("b", countries_json=[])]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 7, 18))
    assert s["intl_collab_ratio"] == 0.0
    assert s["no_country_count"] == 2


def test_citation_distribution_uses_median_and_p90():
    papers = [_p(str(i), citations=c) for i, c in enumerate([0, 1, 2, 3, 100])]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 7, 18))
    assert s["citations"]["median"] == 2
    assert s["citations"]["p90"] == 100
    assert s["top_cited"][0]["citations"] == 100


def test_achievement_type_distribution():
    ext = [
        PaperExtraction(paper_key="a", subfield_id=1, tech_summary="x",
                        achievement_type="공정", model_ver="m"),
        PaperExtraction(paper_key="b", subfield_id=1, tech_summary="y",
                        achievement_type="공정", model_ver="m"),
        PaperExtraction(paper_key="c", subfield_id=1, tech_summary="z",
                        achievement_type="알고리즘", model_ver="m"),
    ]
    s = stats.compute([_p("a"), _p("b"), _p("c")], ext, snapshot_at=datetime(2026, 7, 18))
    assert s["by_achievement_type"] == {"공정": 2, "알고리즘": 1}


def test_empty_input_does_not_crash():
    s = stats.compute([], [], snapshot_at=datetime(2026, 7, 18))
    assert s["searched_count"] == 0
    assert s["intl_collab_ratio"] == 0.0
    assert s["citations"]["median"] == 0
    assert s["no_country_count"] == 0
    assert s["no_year_count"] == 0
    assert s["no_journal_count"] == 0
    assert s["top_institutions"] == []
    assert s["top_journals"] == []
    assert s["top_authors"] == []
    assert s["top_partner_countries"] == []
    assert s["top_cited"] == []


def test_tied_rankings_break_ties_by_name_ascending():
    papers = [
        _p("a", institutions_json=["B대학"], journal="B저널",
           authors_json=["B연구원"], countries_json=["KR", "B국"]),
        _p("b", institutions_json=["A대학"], journal="A저널",
           authors_json=["A연구원"], countries_json=["KR", "A국"]),
    ]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 7, 18))
    assert s["top_institutions"][:2] == [("A대학", 1), ("B대학", 1)]
    assert s["top_journals"][:2] == [("A저널", 1), ("B저널", 1)]
    assert s["top_authors"][:2] == [("A연구원", 1), ("B연구원", 1)]
    assert s["top_partner_countries"][:2] == [("A국", 1), ("B국", 1)]


def test_top_cited_tie_breaks_by_title_ascending():
    papers = [
        _p("a", title="Z제목", citations=5),
        _p("b", title="A제목", citations=5),
    ]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 7, 18))
    titles = [t["title"] for t in s["top_cited"][:2]]
    assert titles == ["A제목", "Z제목"]


def test_missing_year_and_journal_counted():
    papers = [
        _p("a", year=None),
        _p("b", year=2025),
        _p("c", journal=""),
        _p("d", journal="J"),
    ]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 7, 18))
    assert s["no_year_count"] == 1
    assert s["no_journal_count"] == 1


def _e(key, metrics, subfield_id=1):
    return PaperExtraction(paper_key=key, subfield_id=subfield_id, tech_summary="x",
                           model_ver="m", metrics_json=metrics)


def test_metric_groups_merge_on_parenthetical_difference():
    """괄호 안 약어만 다른 같은 지표는 한 그룹으로 묶인다."""
    ext = [
        _e("a", [{"name": "전력 변환 효율 (PCE)", "value": "18.4", "unit": "%"}]),
        _e("b", [{"name": "전력 변환 효율", "value": "20.0", "unit": "%"}]),
        _e("c", [{"name": "전력  변환/효율", "value": "22.0", "unit": "%"}]),
    ]
    agg = stats.aggregate_metrics(ext)
    assert len(agg["top_metrics"]) == 1
    row = agg["top_metrics"][0]
    assert row["count"] == 3
    assert row["unit"] == "%"
    assert row["median"] == 20.0
    assert row["max"] == 22.0


def test_metric_groups_do_not_merge_across_units():
    """단위가 다르면 환산하지 않고 별도 그룹으로 둔다 — 잘못 합치면 1000배 오차가 난다."""
    ext = [
        _e("a", [{"name": "개방전압", "value": "1.2", "unit": "V"},
                 {"name": "개방전압", "value": "1.3", "unit": "V"}]),
        _e("b", [{"name": "개방전압", "value": "800", "unit": "mV"},
                 {"name": "개방전압", "value": "820", "unit": "mV"}]),
    ]
    agg = stats.aggregate_metrics(ext)
    units = {r["unit"] for r in agg["top_metrics"]}
    assert units == {"V", "mV"}


def test_metric_value_parsing_and_unparsed_are_counted_not_hidden():
    """숫자를 못 뽑은 값은 집계에서 빼되 metrics_total에는 남겨 분모를 속이지 않는다."""
    ext = [_e("a", [
        {"name": "효율", "value": "~14", "unit": "%"},
        {"name": "효율", "value": "1,200", "unit": "%"},
        {"name": "효율", "value": "측정 불가", "unit": "%"},
    ])]
    agg = stats.aggregate_metrics(ext)
    assert agg["metrics_total"] == 3
    assert agg["metrics_parsed"] == 2
    assert agg["top_metrics"][0]["count"] == 2
    assert agg["top_metrics"][0]["max"] == 1200.0


def test_single_occurrence_metrics_are_excluded_but_counted():
    """1회성 지표는 평균 낼 상대가 없어 표에서 빼되, 몇 종인지는 드러낸다."""
    ext = [_e("a", [
        {"name": "MED 프로세스 LCOW 증가율", "value": "17", "unit": "%"},
        {"name": "효율", "value": "10", "unit": "%"},
        {"name": "효율", "value": "20", "unit": "%"},
    ])]
    agg = stats.aggregate_metrics(ext)
    assert [r["name"] for r in agg["top_metrics"]] == ["효율"]
    assert agg["metrics_unique"] == 1


def test_metric_display_name_is_most_common_original():
    """표시 이름은 그룹에서 가장 많이 쓰인 원본 표기를 쓴다(소문자 키가 아니라)."""
    ext = [_e("a", [
        {"name": "전력변환효율(PCE)", "value": "1", "unit": "%"},
        {"name": "전력변환효율(PCE)", "value": "2", "unit": "%"},
        {"name": "Power Conversion Efficiency", "value": "3", "unit": "%"},
    ])]
    agg = stats.aggregate_metrics(ext)
    names = {r["name"] for r in agg["top_metrics"]}
    assert "전력변환효율(PCE)" in names


def test_metrics_papers_counts_papers_not_metrics():
    ext = [
        _e("a", [{"name": "효율", "value": "1", "unit": "%"},
                 {"name": "효율", "value": "2", "unit": "%"}]),
        _e("b", []),
    ]
    agg = stats.aggregate_metrics(ext)
    assert agg["metrics_papers"] == 1
    assert agg["metrics_total"] == 2


def test_aggregate_metrics_tolerates_malformed_rows():
    """LLM 출력이 스키마를 벗어나도 예외를 던지지 않는다."""
    ext = [_e("a", ["문자열", {"value": "1"}, {"name": "", "value": "2"}, None])]
    agg = stats.aggregate_metrics(ext)
    assert agg["top_metrics"] == []


def test_compute_includes_metric_aggregate():
    papers = [_p("a"), _p("b")]
    ext = [
        PaperExtraction(paper_key="a", subfield_id=1, tech_summary="x", model_ver="m",
                        metrics_json=[{"name": "효율", "value": "10", "unit": "%"}]),
        PaperExtraction(paper_key="b", subfield_id=1, tech_summary="y", model_ver="m",
                        metrics_json=[{"name": "효율", "value": "30", "unit": "%"}]),
    ]
    s = stats.compute(papers, ext, snapshot_at=datetime(2026, 8, 1))
    assert s["metrics_total"] == 2
    assert s["metrics_papers"] == 2
    assert s["top_metrics"][0]["name"] == "효율"
    assert s["top_metrics"][0]["median"] == 20.0


def test_compute_with_no_metrics_still_returns_metric_keys():
    """지표가 하나도 없어도 키는 항상 존재해야 화면이 분기하지 않는다."""
    s = stats.compute([_p("a")], [], snapshot_at=datetime(2026, 8, 1))
    assert s["metrics_total"] == 0
    assert s["top_metrics"] == []


def test_metric_row_reports_min_median_max_range():
    """분포는 최소~최대 범위로 보여준다.

    p90은 표본이 작으면 최대값과 같아진다 — _percentile의 인덱스가 int(n*0.9)
    내림이라 n<=10이면 항상 마지막 원소를 가리킨다. 실측으로 저장된 지표 행
    1,687개 중 1,523개(90.3%)가 p90 == max였다(같은 숫자가 두 열에 나왔다).
    """
    ext = [_e("a", [{"name": "효율", "value": str(v), "unit": "%"} for v in [3, 1, 5, 2, 4]])]
    row = stats.aggregate_metrics(ext)["top_metrics"][0]
    assert row["min"] == 1.0
    assert row["median"] == 3.0
    assert row["max"] == 5.0
    assert "p90" not in row


def test_metric_range_is_meaningful_even_for_two_papers():
    """표본이 둘뿐이어도 범위는 성립한다 — p90과 달리 하한을 둘 필요가 없다."""
    ext = [_e("a", [{"name": "효율", "value": "10", "unit": "%"},
                    {"name": "효율", "value": "30", "unit": "%"}])]
    row = stats.aggregate_metrics(ext)["top_metrics"][0]
    assert (row["min"], row["median"], row["max"]) == (10.0, 20.0, 30.0)


def test_citation_p90_is_kept():
    """인용수 p90은 표본이 수백~수천이라 이 문제가 없어 그대로 둔다."""
    papers = [_p(f"k{i}", citations=i) for i in range(1, 6)]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2))
    assert s["citations"]["p90"] == 5


def test_attribution_splits_solo_lead_participant_unknown():
    """참여 기준만 쓰면 JP와 CN의 숫자를 같은 의미로 읽게 된다 — 실측으로 일본 논문의
    47%가 자국이 주도하지 않은 국제공동연구이고 중국은 7.5%뿐이다."""
    papers = [
        _p("solo", countries_json=["KR"], lead_countries_json=["KR"]),
        _p("lead", countries_json=["KR", "US"], lead_countries_json=["KR"]),
        _p("part", countries_json=["KR", "US"], lead_countries_json=["US"]),
        _p("unk",  countries_json=["KR", "US"], lead_countries_json=[]),
    ]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), country="KR")
    assert s["attribution"] == {"단독": 1, "주도": 1, "참여": 1, "주도 미상": 1}


def test_attribution_follows_the_analysis_country():
    papers = [_p("a", countries_json=["US", "KR"], lead_countries_json=["US"])]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), country="US")
    assert s["attribution"]["주도"] == 1


def test_partner_countries_exclude_the_analysis_country():
    papers = [_p("a", countries_json=["CN", "US"], lead_countries_json=["CN"])]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), country="CN")
    assert dict(s["top_partner_countries"]) == {"US": 1}
    assert s["intl_collab_ratio"] == 1.0


def test_sampled_flag_marks_truncated_collections():
    """상한에 걸려 잘린 표본을 전수와 나란히 놓으면 인용수가 구조적으로 부풀려진다 —
    표본임을 반드시 드러낸다."""
    papers = [_p("a")]
    full = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), population_total=1)
    cut = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), population_total=5000)
    assert full["sampled"] is False and full["population_total"] == 1
    assert cut["sampled"] is True and cut["population_total"] == 5000


def test_range_values_use_the_midpoint_not_the_lower_bound():
    """범위 표기("4-6")는 중간값으로 집계한다.

    실측(2026-08-03, v3 추출 42,417개 값): 5.09%(2,159개)가 범위 표기이고, 그중
    35.9%는 상한이 하한의 2배 이상이다(평균 16배). 하한만 취하면 이 5%가 체계적으로
    낮게 잡힌다 — "70-600"을 70으로 세는 식이다.

    중간값을 쓰는 이유: 분포 요약(최소·중앙값·최대)의 대표값으로는 한쪽 끝보다
    중앙이 맞다. 범위를 통째로 버리면 5%를 잃는다.
    """
    ext = [_e("a", [
        {"name": "효율", "value": "4-6", "unit": "%"},
        {"name": "효율", "value": "70~600", "unit": "%"},
        # 전각 대시(–)도 같은 범위 표기다 — 실측 데이터에 섞여 있다.
        {"name": "효율", "value": "40–55", "unit": "%"},
    ])]
    agg = stats.aggregate_metrics(ext)
    row = agg["top_metrics"][0]
    assert row["count"] == 3
    assert row["min"] == 5.0        # (4+6)/2
    assert row["median"] == 47.5    # (40+55)/2
    assert row["max"] == 335.0      # (70+600)/2


def test_negative_lower_bound_range_is_still_a_range():
    """'-20~20' 같은 음수 하한도 범위로 읽는다(중간값 0)."""
    ext = [_e("a", [
        {"name": "가변범위", "value": "-20~20", "unit": "도"},
        {"name": "가변범위", "value": "-10~10", "unit": "도"},
    ])]
    agg = stats.aggregate_metrics(ext)
    assert agg["top_metrics"][0]["median"] == 0.0


def test_hyphen_in_non_range_values_is_not_treated_as_a_range():
    """범위가 아닌 하이픈은 건드리지 않는다 — 첫 숫자만 뽑던 기존 동작 유지."""
    ext = [_e("a", [
        # 음수 하나. "-5 ~ ..." 형태가 아니므로 범위가 아니다.
        {"name": "온도차", "value": "-5", "unit": "K"},
        {"name": "온도차", "value": "-3", "unit": "K"},
    ])]
    agg = stats.aggregate_metrics(ext)
    assert agg["top_metrics"][0]["min"] == -5.0
    assert agg["top_metrics"][0]["max"] == -3.0


def test_exponent_notation_is_read_as_a_number():
    """지수 표기를 밑수로 읽으면 분포가 통째로 무너진다.

    사용자 신고 + 실측(차세대 메모리반도체 KR 2025, 지표 '내구성'):
    10^11 · 10^12 · 2×10^6 같은 값 15건이 각각 10 · 10 · 2로 읽혀
    최소 2 / 중앙값 10 / 최대 1,000이 나왔다. 실제로는 10^3~10^12 범위다.

    실측 corpus(v3 추출 44,225개 값)에 지수 표기가 2.34%(1,033건) 있고,
    형태는 캐럿·e표기·×10 세 갈래다.
    """
    ext = [_e("a", [
        {"name": "내구성", "value": "10^11", "unit": "cycles"},
        {"name": "내구성", "value": "10^3", "unit": "cycles"},
    ])]
    row = stats.aggregate_metrics(ext)["top_metrics"][0]
    assert row["min"] == 1e3
    assert row["max"] == 1e11


def test_mantissa_times_power_of_ten():
    """2 × 10^6 형태. 구분자는 ×(U+00D7)·x·X·* 가 섞여 있다(실측)."""
    ext = [_e("a", [
        {"name": "내구성", "value": "2 × 10^6", "unit": "cycles"},
        {"name": "내구성", "value": "1 x 10^4", "unit": "cycles"},
        {"name": "내구성", "value": "0.25*10^12", "unit": "cycles"},
    ])]
    row = stats.aggregate_metrics(ext)["top_metrics"][0]
    assert row["min"] == 1e4
    assert row["median"] == 2e6
    assert row["max"] == 0.25e12


def test_e_notation_and_negative_exponents():
    """-1.57E-03, 0.9e6, 10^-5 — 음수 지수와 e표기도 실측 corpus에 있다."""
    ext = [_e("a", [
        {"name": "전류", "value": "-1.57E-03", "unit": "A"},
        {"name": "전류", "value": "0.9e6", "unit": "A"},
        {"name": "전류", "value": "10^-5", "unit": "A"},
    ])]
    row = stats.aggregate_metrics(ext)["top_metrics"][0]
    assert row["min"] == -0.00157
    assert row["max"] == 0.9e6


def test_earliest_number_still_wins():
    """지수 이해가 '앞의 수를 쓴다'는 기존 규칙을 뒤집으면 안 된다 —
    '2.5 GHz, 10^3 cycles'에서 2.5가 앞이면 2.5다."""
    ext = [_e("a", [
        {"name": "주파수", "value": "2.5 GHz, 10^3 cycles", "unit": "GHz"},
        {"name": "주파수", "value": "3.5 GHz", "unit": "GHz"},
    ])]
    row = stats.aggregate_metrics(ext)["top_metrics"][0]
    assert row["min"] == 2.5


def test_garbage_with_caret_does_not_crash():
    """'(nd)^{1/4}/sqrt(ε)' 같은 값도 실제로 있다 — 숫자가 없으면 None이고
    metrics_total에는 남는다(기존 규칙)."""
    ext = [_e("a", [
        {"name": "지수", "value": "(nd)^{1/4}/sqrt(ε)", "unit": ""},
        {"name": "지수", "value": "5", "unit": ""},
    ])]
    agg = stats.aggregate_metrics(ext)
    # 예전부터 "{1/4}"의 1을 첫 숫자로 읽어 왔다. 지수 파싱이 그 동작을 바꾸지
    # 않는다는 것이 여기서 확인하려는 것이다 — 터지지 않고, 지수로 오해하지도 않는다.
    assert agg["metrics_total"] == 2
    assert agg["metrics_parsed"] == 2
    assert agg["top_metrics"][0]["min"] == 1.0
