from app.config import settings
from app.models.paper import Paper, PaperExtraction
from app.services import reducer


def _ext(key, atype):
    return PaperExtraction(paper_key=key, subfield_id=1, tech_summary=f"{key} 성과",
                           achievement_type=atype, metrics_json=[], model_ver="m")


def test_group_returns_single_bucket_under_threshold():
    ext = [_ext(f"k{i}", "공정") for i in range(5)]
    groups = reducer.group_for_reduce(ext)
    assert list(groups) == ["전체"]
    assert len(groups["전체"]) == 5


def test_group_splits_by_achievement_type_over_threshold(monkeypatch):
    monkeypatch.setattr(settings, "reduce_group_threshold", 3)
    ext = [_ext(f"a{i}", "공정") for i in range(3)] + [_ext(f"b{i}", "알고리즘") for i in range(2)]
    groups = reducer.group_for_reduce(ext)
    assert set(groups) == {"공정", "알고리즘"}
    assert len(groups["공정"]) == 3


def test_group_resplits_a_type_that_still_exceeds_threshold(monkeypatch):
    """성과유형이 하나뿐이면 유형 분할만으로는 임계값 아래로 내려가지 않는다."""
    monkeypatch.setattr(settings, "reduce_group_threshold", 2)
    ext = [_ext(f"a{i}", "공정") for i in range(5)]
    groups = reducer.group_for_reduce(ext)
    assert len(groups) == 3
    assert all(len(items) <= 2 for items in groups.values())
    assert sum(len(items) for items in groups.values()) == 5


def test_format_includes_title_year_and_summary():
    papers = {"k1": Paper(paper_key="k1", title="HBM 논문", year=2025, journal="J",
                          abstract="A", source="openalex", citations=4)}
    text = reducer.format_extractions([_ext("k1", "공정")], papers)
    assert "HBM 논문" in text
    assert "2025" in text
    assert "k1 성과" in text


def test_format_skips_extractions_without_a_matching_paper():
    text = reducer.format_extractions([_ext("missing", "공정")], {})
    assert text == ""
