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
