from app.config import settings
from app.models.analysis import Analysis
from app.models.field import Subfield
from app.models.paper import Paper, PaperExtraction
from app.services import reducer


class _FakeDb:
    """reduce_subfield는 세부기술명을 얻으려고 db.get(Subfield, ...) 한 번만 부른다."""

    def get(self, model, pk):
        return Subfield(id=pk, field_id=1, name="테스트 세부기술", query="q")


_ANALYSIS = Analysis(subfield_id=1, year=2026, query_hash="h")


class _FakeGenerate:
    """gemini_sync.generate 대역. 호출 여부/인자를 기록하고 실제 API는 부르지 않는다."""

    def __init__(self, return_value: str = "generated"):
        self.calls: list[tuple[str, str, str]] = []
        self.return_value = return_value

    async def __call__(self, system, user, *, thinking, **kwargs):
        self.calls.append((system, user, thinking))
        return self.return_value


def _ext(key, atype, approach="", improvement=""):
    return PaperExtraction(paper_key=key, subfield_id=1, tech_summary=f"{key} 성과",
                           achievement_type=atype, metrics_json=[], model_ver="m",
                           approach=approach, improvement=improvement)


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


def test_format_includes_approach_and_improvement_when_present():
    papers = {"k1": Paper(paper_key="k1", title="HBM 논문", year=2025, journal="J",
                          abstract="A", source="openalex", citations=4)}
    ext = _ext("k1", "공정", approach="저온 본딩 공정 적용", improvement="기존 대비 피치 절반 축소")
    text = reducer.format_extractions([ext], papers)
    assert "접근: 저온 본딩 공정 적용" in text
    assert "개선점: 기존 대비 피치 절반 축소" in text


def test_format_omits_empty_approach_and_improvement():
    """빈 approach/improvement는 토큰 낭비이므로 줄에 아예 나오면 안 된다."""
    papers = {"k1": Paper(paper_key="k1", title="HBM 논문", year=2025, journal="J",
                          abstract="A", source="openalex", citations=4)}
    text = reducer.format_extractions([_ext("k1", "공정")], papers)
    assert "접근:" not in text
    assert "개선점:" not in text


async def test_reduce_subfield_skips_llm_when_body_empty_single_group(monkeypatch):
    """추출은 있지만 papers_by_key 매칭 실패로 본문이 빈 단일 그룹 경로 — LLM 호출 금지."""
    fake = _FakeGenerate()
    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)
    ext = [_ext("missing", "공정")]

    result, _ = await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, ext, {})

    assert fake.calls == []
    assert result == "분석 대상 논문이 없어 성과를 정리할 수 없습니다."


async def test_reduce_subfield_skips_llm_when_all_groups_empty_three_tier(monkeypatch):
    """3단 경로에서 모든 그룹의 본문이 비면 최종 통합 호출도 하지 않는다."""
    monkeypatch.setattr(settings, "reduce_group_threshold", 1)
    fake = _FakeGenerate()
    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)
    ext = [_ext("m1", "공정"), _ext("m2", "알고리즘")]

    result, _ = await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, ext, {})

    assert fake.calls == []
    assert result == "분석 대상 논문이 없어 성과를 정리할 수 없습니다."


async def test_reduce_subfield_calls_llm_for_normal_input(monkeypatch):
    """본문이 실제로 채워지면 가드가 과하게 막지 않고 LLM을 호출한다."""
    fake = _FakeGenerate(return_value="report")
    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)
    papers = {"k1": Paper(paper_key="k1", title="HBM 논문", year=2025, journal="J",
                          abstract="A", source="openalex", citations=0)}
    ext = [_ext("k1", "공정")]

    result, _ = await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, ext, papers)

    assert len(fake.calls) == 1
    assert result == "report"
    assert "[세부기술: 테스트 세부기술 / 2026]" in fake.calls[0][1]


async def test_three_tier_final_call_still_names_the_subfield(monkeypatch):
    """3단 경로의 최종 통합 입력은 중간 요약뿐이라 세부기술명이 사라지기 쉽다 — 그러면
    모델이 H1 제목을 내용만 보고 새로 지어내 목록의 세부기술명과 어긋난다."""
    monkeypatch.setattr(settings, "reduce_group_threshold", 1)
    fake = _FakeGenerate(return_value="report")
    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)
    papers = {
        k: Paper(paper_key=k, title=f"{k} 논문", year=2026, journal="J",
                 abstract="A", source="openalex", citations=0)
        for k in ("k1", "k2")
    }
    ext = [_ext("k1", "공정"), _ext("k2", "알고리즘")]

    await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, ext, papers)

    assert len(fake.calls) == 3  # 그룹 2개 + 최종 통합 1개
    assert "[세부기술: 테스트 세부기술 / 2026]" in fake.calls[-1][1]


async def test_rollup_field_skips_llm_for_empty_input(monkeypatch):
    fake = _FakeGenerate()
    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)

    result = await reducer.rollup_field("분야", [])

    assert fake.calls == []
    assert result == "분석된 세부기술이 없습니다."


async def test_reduce_subfield_returns_partials_for_three_tier(monkeypatch):
    """3단 reduce의 그룹별 중간 보고서를 버리지 않고 함께 돌려준다."""
    monkeypatch.setattr(settings, "reduce_group_threshold", 2)

    async def fake(system, user, *, thinking, **kwargs):
        return "부분보고서" if user.startswith("[성과유형:") else "최종 통합 보고서"

    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)
    ext = [_ext(f"a{i}", "공정") for i in range(3)] + [_ext(f"b{i}", "알고리즘") for i in range(2)]
    papers = {e.paper_key: Paper(paper_key=e.paper_key, title=f"논문 {e.paper_key}",
                                 year=2026, journal="J", abstract="A",
                                 source="openalex", citations=1) for e in ext}

    report, sections = await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, ext, papers)

    assert report == "최종 통합 보고서"
    assert len(sections) >= 2
    assert all(set(s) == {"name", "body"} for s in sections)
    assert all(s["body"] == "부분보고서" for s in sections)
    # 그룹명이 보존돼야 화면이 유형별 제목을 붙일 수 있다.
    assert "알고리즘" in [s["name"] for s in sections]


async def test_reduce_subfield_returns_empty_sections_for_single_call(monkeypatch):
    """단일 reduce는 그룹이 하나뿐이라 세부 보고서가 없다."""
    fake = _FakeGenerate("단일 보고서")
    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)
    ext = [_ext("k1", "공정")]
    papers = {"k1": Paper(paper_key="k1", title="논문", year=2026, journal="J",
                          abstract="A", source="openalex", citations=1)}

    report, sections = await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, ext, papers)

    assert report == "단일 보고서"
    assert sections == []


async def test_reduce_subfield_no_data_returns_empty_sections():
    """추출 0건이면 LLM을 부르지 않고 안내문과 빈 세부 보고서를 돌려준다."""
    report, sections = await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, [], {})

    assert "분석 대상 논문이 없어" in report
    assert sections == []


def test_reduce_instruction_forbids_unreachable_quotas():
    """지켜지지 않는 정량 목표는 남아 있는 것이 해롭다 — 모델이 다른 지시도
    같은 강도로 따르지 않게 된다(실측: 25% 인용 목표가 200건 이상에서 전부 미달)."""
    from app.prompts import REDUCE_INSTRUCTION

    assert "25% 이상" not in REDUCE_INSTRUCTION
    assert "8,000자" not in REDUCE_INSTRUCTION


def test_reduce_instruction_pins_citation_format_and_position():
    """백틱 인용(각주 미인식)과 불릿 나열(번호만 남는 항목)을 실제로 겪었다."""
    from app.prompts import REDUCE_INSTRUCTION

    assert "백틱" in REDUCE_INSTRUCTION
    assert "문장 안" in REDUCE_INSTRUCTION


def test_reduce_instruction_delegates_metric_table_to_code():
    """정량 표는 stats.aggregate_metrics가 코드로 만든다 — LLM이 또 만들면 중복이다."""
    from app.prompts import REDUCE_INSTRUCTION

    assert "## 정량 성과 정리" not in REDUCE_INSTRUCTION
