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
