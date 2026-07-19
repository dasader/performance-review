from app.config import Settings


def test_defaults_match_spec():
    s = Settings(gemini_api_key="k", openalex_api_key="k", admin_key="a",
                 database_url="postgresql://x/y")
    assert s.gemini_model == "gemini-3.1-flash-lite"
    assert s.thinking_map == "low"
    assert s.thinking_reduce == "high"
    assert s.openalex_per_page == 100
    assert s.openalex_daily_budget_usd == 0.5
    assert s.openalex_search_cost_usd == 0.001
    assert s.kci_page_size == 100
    assert s.kci_max_pages == 20
    assert s.max_papers_per_analysis == 5000
    assert s.reduce_group_threshold == 500
    assert s.default_year_range == 3
    assert s.http_max_attempts == 5
    assert s.http_timeout_seconds == 60.0
