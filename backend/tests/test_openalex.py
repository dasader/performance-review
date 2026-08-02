import pytest

from app.clients import openalex
from app.clients.openalex import _filter_expr, _parse_work, reconstruct_abstract
from app.clients._doi import strip_doi_prefix


def test_reconstruct_abstract_orders_words_by_position():
    inv = {"hello": [0, 2], "world": [1]}
    assert reconstruct_abstract(inv) == "hello world hello"


def test_reconstruct_abstract_handles_missing():
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""


def test_strip_doi_prefix():
    assert strip_doi_prefix("https://doi.org/10.1/x") == "10.1/x"
    assert strip_doi_prefix("10.1/x") == "10.1/x"
    assert strip_doi_prefix(None) is None


def test_parse_work_flags_korea_and_collects_countries():
    work = {
        "id": "https://openalex.org/W1",
        "title": "T",
        "publication_year": 2025,
        "cited_by_count": 7,
        "doi": "https://doi.org/10.1/x",
        "abstract_inverted_index": {"a": [0]},
        "primary_location": {"source": {"display_name": "J"}},
        "authorships": [
            {"author": {"display_name": "Kim"},
             "institutions": [{"display_name": "KAIST", "country_code": "KR"}]},
            {"author": {"display_name": "Smith"},
             "institutions": [{"display_name": "MIT", "country_code": "US"}]},
        ],
    }
    p = _parse_work(work)
    assert p["paper_key"] == "10.1/x"          # DOI가 있으면 DOI가 키
    assert p["countries"] == ["KR", "US"]
    assert p["institutions"] == ["KAIST", "MIT"]
    assert p["authors"] == ["Kim", "Smith"]
    assert p["abstract"] == "a"
    assert p["source"] == "openalex"


def test_parse_work_without_doi_uses_openalex_id():
    work = {"id": "https://openalex.org/W2", "title": "T", "authorships": []}
    assert _parse_work(work)["paper_key"] == "openalex:W2"


def test_parse_work_strips_html_tags_from_title_abstract_and_journal():
    """실측: OpenAlex가 `Hf <sub>0.5</sub> Zr <sub>0.5</sub> O <sub>2</sub>`처럼 태그를
    섞어 보낸다. 각주 매칭(LLM이 벗긴 제목을 씀)과 화면 표시 둘 다 깨끗한 제목이 필요하다."""
    work = {
        "id": "https://openalex.org/W3",
        "title": "Hf <sub>0.5</sub> Zr <sub>0.5</sub> O <sub>2</sub> Film",
        "abstract_inverted_index": {"<i>in": [0], "situ</i>": [1], "ALD": [2]},
        "primary_location": {"source": {"display_name": "J. <i>Applied</i> Physics"}},
        "authorships": [],
    }
    p = _parse_work(work)
    assert p["title"] == "Hf 0.5 Zr 0.5 O 2 Film"
    assert p["abstract"] == "in situ ALD"
    assert p["journal"] == "J. Applied Physics"


def test_filter_expr_strips_comma_to_avoid_extra_and_clause():
    # 콤마는 OpenAlex filter DSL에서 AND 절 구분자라 그대로 넣으면 검색식이 쪼개진다
    expr = _filter_expr("quantum computing, error correction", 2022, 2024)
    assert expr.count(",") == 2  # year/KR 절 구분자 2개만 있어야 함
    assert "quantum computing" in expr and "error correction" in expr


def test_filter_expr_strips_pipe_to_avoid_unintended_or():
    # 파이프는 OpenAlex filter DSL에서 OR 구분자라 그대로 넣으면 검색식이 쪼개진다
    expr = _filter_expr("quantum|classical computing", 2022, 2024)
    assert "|" not in expr
    assert "quantum classical computing" in expr


def test_filter_expr_keeps_plain_query_and_appends_year_and_kr():
    expr = _filter_expr("quantum computing", 2022, 2024)
    assert expr == (
        "title_and_abstract.search:quantum computing,"
        "publication_year:2022-2024,"
        "authorships.institutions.country_code:KR"
    )


class _FakeResponse:
    def __init__(self, data, headers=None):
        self._data = data
        self.headers = headers or {}

    def json(self):
        return self._data


async def test_search_attaches_partial_cost_to_exception_on_mid_page_failure(monkeypatch):
    """I6: 페이지 중간에 실패해도 그때까지 이미 과금된 비용을 알 수 있어야
    호출자(search.collect)가 예산 행에 반영할 수 있다."""
    calls = {"n": 0}

    async def fake_get_with_retry(url, *, client, params, service_name, context):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse({
                "meta": {"cost_usd": 0.01, "count": 500, "next_cursor": "abc"},
                "results": [{"id": "https://openalex.org/W1", "title": "T", "authorships": []}],
            })
        raise RuntimeError("OpenAlex 오류 500")

    monkeypatch.setattr(openalex, "get_with_retry", fake_get_with_retry)

    with pytest.raises(RuntimeError) as exc_info:
        await openalex.search("q", 2024, 2024, client=None, limit=1000)

    assert exc_info.value.cost_usd == pytest.approx(0.01)


def test_filter_expression_uses_given_country():
    expr = openalex._filter_expr("q", 2025, 2025, "CN")
    assert "authorships.institutions.country_code:CN" in expr
    assert ":KR" not in expr


def test_filter_expression_defaults_to_kr():
    assert "country_code:KR" in openalex._filter_expr("q", 2025, 2025)


def test_parse_work_extracts_lead_countries_from_corresponding_authors():
    """참여국(countries)과 주도국(lead_countries)은 다른 값이다 — 실측으로 일본 논문의
    47%가 자국이 주도하지 않은 국제공동연구다."""
    work = {
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x",
        "title": "T", "publication_year": 2026, "cited_by_count": 3,
        "authorships": [
            {"author": {"display_name": "A"}, "is_corresponding": True,
             "institutions": [{"display_name": "KAIST", "country_code": "KR"}]},
            {"author": {"display_name": "B"}, "is_corresponding": False,
             "institutions": [{"display_name": "MIT", "country_code": "US"}]},
        ],
    }
    p = openalex._parse_work(work)
    assert set(p["countries"]) == {"KR", "US"}
    assert p["lead_countries"] == ["KR"]
    assert "korea_flag" not in p


def test_parse_work_leaves_lead_countries_empty_when_no_corresponding_flag():
    """is_corresponding 보유율은 91~94%라 없는 경우가 있다 — 빈 리스트로 두고
    stats가 '주도 미상'으로 센다. 없는 정보를 추측해 채우지 않는다."""
    work = {
        "id": "https://openalex.org/W2", "title": "T", "publication_year": 2026,
        "authorships": [
            {"author": {"display_name": "A"},
             "institutions": [{"display_name": "KAIST", "country_code": "KR"}]},
        ],
    }
    assert openalex._parse_work(work)["lead_countries"] == []


async def test_search_sorts_by_citations_and_passes_country(monkeypatch):
    """상한에 걸려 잘릴 때 무엇이 남는지를 정하려면 정렬이 필요하다 —
    기본 정렬(relevance_score)은 불투명해 국가 간 비교의 기준선으로 쓸 수 없다."""
    seen = {}

    async def fake_get_with_retry(url, *, client, params, service_name, context):
        seen.update(params)
        return _FakeResponse({"meta": {"cost_usd": 0.001, "count": 1}, "results": []})

    monkeypatch.setattr(openalex, "get_with_retry", fake_get_with_retry)
    await openalex.search("q", 2025, 2025, client=None, limit=100, country="CN")

    assert seen["sort"] == "cited_by_count:desc"
    assert "country_code:CN" in seen["filter"]
