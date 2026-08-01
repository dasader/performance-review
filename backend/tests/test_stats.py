from datetime import datetime

from app.models.paper import Paper, PaperExtraction
from app.services import stats


def _p(key, **kw):
    defaults = dict(paper_key=key, title="T", abstract="A", year=2025, journal="J",
                    authors_json=["김"], institutions_json=["KAIST"], countries_json=["KR"],
                    citations=0, source="openalex", korea_flag=True)
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
