"""0010 시드 데이터가 개정안 체계(10대 분야 55개 중점기술)와 어긋나지 않는지 고정한다.

검색식에 콤마·파이프가 섞이면 OpenAlex filter DSL의 절 구분자로 먹혀
`_sanitize_query`가 공백으로 지워버린다 — 검색식이 조용히 망가지는 경로라 여기서 막는다.
"""
import importlib.util
from pathlib import Path

def _load(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / "alembic/versions" / filename
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


seed = _load("0010_national_strategic_tech_2026.py", "seed_0010")
narrow = _load("0011_narrow_sensing_query.py", "seed_0011")


def test_counts():
    assert len(seed.FIELDS) == 10
    assert len(seed.SUBFIELDS) == 55


def test_queries_survive_sanitization():
    from app.clients.openalex import _sanitize_query

    slugs = {s for _, s in seed.FIELDS}
    queries = [(n, q) for _, n, q in seed.SUBFIELDS]
    queries.append((narrow.SUBFIELD_NAME, narrow.NEW_QUERY))

    for slug, name, _ in seed.SUBFIELDS:
        assert slug in slugs, name
    for name, query in queries:
        assert _sanitize_query(query) == query, f"{name}: 콤마/파이프가 섞였다"
        assert query.count("(") == query.count(")"), f"{name}: 괄호 불균형"


def test_names_unique_within_field():
    seen = set()
    for slug, name, _ in seed.SUBFIELDS:
        assert (slug, name) not in seen
        seen.add((slug, name))
