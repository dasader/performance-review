from app.clients.openalex import _parse_work, reconstruct_abstract
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
    assert p["korea_flag"] is True
    assert p["countries"] == ["KR", "US"]
    assert p["institutions"] == ["KAIST", "MIT"]
    assert p["authors"] == ["Kim", "Smith"]
    assert p["abstract"] == "a"
    assert p["source"] == "openalex"


def test_parse_work_without_doi_uses_openalex_id():
    work = {"id": "https://openalex.org/W2", "title": "T", "authorships": []}
    assert _parse_work(work)["paper_key"] == "openalex:W2"
