from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    gemini_api_key: str
    gemini_model: str = "gemini-3.1-flash-lite"
    # thinking_level 허용값은 모델 세대별로 다르다(minimal/low/medium/high 중 일부).
    # API가 값을 거부하면 .env에서 조정한다 — 코드 변경 불필요.
    thinking_map: str = "low"
    thinking_reduce: str = "high"

    openalex_api_key: str
    openalex_per_page: int = 100
    openalex_daily_budget_usd: float = 0.5
    openalex_search_cost_usd: float = 0.001  # search 계열 요청 1건 단가
    kci_api_key: str = ""
    kci_page_size: int = 100
    kci_max_pages: int = 20

    admin_key: str
    database_url: str

    batch_max_enqueued_tokens: int = 5_000_000  # Tier1 한도 10M의 절반 — 타 작업과 공유 여지
    batch_max_concurrent_jobs: int = 2
    batch_max_requests_per_file: int = 1000
    sync_rpm: int = 60

    # Gemini batch(map) 단가. 정확한 단가는 확인되지 않았다 — Gemini 3.1 Flash Lite의
    # 공식 batch 요금표를 찾지 못해 같은 세대 flash-lite 계열의 공개 단가를 보수적으로
    # (실제보다 비싸게) 반영했다. 확인되는 대로 .env에서 조정할 것.
    gemini_batch_input_usd_per_1m: float = 0.0375
    gemini_batch_output_usd_per_1m: float = 0.15
    # 미리보기 시점엔 실제 논문 title/abstract가 없어 길이를 알 수 없으므로 쓰는
    # 논문당 평균 토큰 근사치. 실측 분포가 쌓이면 .env에서 조정한다.
    gemini_avg_input_tokens_per_paper: int = 700
    gemini_avg_output_tokens_per_paper: int = 200

    max_papers_per_analysis: int = 5000
    max_extract_attempts: int = 3
    max_search_attempts: int = 3
    reduce_group_threshold: int = 500
    default_year_range: int = 3
    loop_interval_seconds: int = 30

    http_max_attempts: int = 5
    http_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
