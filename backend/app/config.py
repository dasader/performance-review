from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    gemini_api_key: str
    gemini_model: str = "gemini-3.1-flash-lite"
    # 보고서 합성(reduce·rollup·로드맵 점검·국가 비교) 전용 모델. **비우면 gemini_model을
    # 그대로 쓴다** — 설정을 추가해도 기존 동작이 바뀌지 않아야 하므로 기본값이 빈 문자열이다.
    #
    # 추출(batch)과 나눠 놓은 이유는 캐시다. gemini_model은 mapper.model_ver()에 들어가
    # 바꾸는 순간 추출 캐시 22,000여 건이 통째로 무효화된다(재추출 약 $6). 보고서는 캐시
    # 키가 analyses 행 자체라 모델을 바꿔도 재추출이 일어나지 않는다 — 그래서 "보고서만
    # 더 좋은 모델로" 같은 판단을 추출 비용 없이 내릴 수 있어야 한다.
    gemini_model_reduce: str = ""
    # thinking_level 허용값은 모델 세대별로 다르다(minimal/low/medium/high 중 일부).
    # API가 값을 거부하면 .env에서 조정한다 — 코드 변경 불필요.
    thinking_map: str = "low"
    thinking_reduce: str = "high"
    # 모델 기본 출력 상한에 기대지 않고 명시한다. reduce는 깊이 있는 서술형 보고서를
    # 요구하므로(주제별 다단락 서술 + 표 여러 개) 넉넉히 잡는다. thinking 토큰도 이
    # 상한에 포함되어 과금/차감된다.
    gemini_max_output_tokens: int = 16000

    openalex_api_key: str
    openalex_per_page: int = 100
    openalex_daily_budget_usd: float = 0.5
    openalex_search_cost_usd: float = 0.001  # search 계열 요청 1건 단가
    kci_api_key: str = ""
    kci_page_size: int = 100
    kci_max_pages: int = 20

    # OpenAlex 결측 초록을 ScienceDirect Article Retrieval로 회수한다(결측의 66.8%가
    # Elsevier 게재분, 회수율 실측 88%). **비어 있으면 회수 단계를 통째로 건너뛴다** —
    # 키 없이도 기존 동작이 그대로여야 하므로 Gemini 클라이언트 지연 생성과 같은 원칙이다.
    elsevier_api_key: str = ""
    # 실측: 동시성 1→3.0 req/s, 3→9.5(429 없음), 5→14.6(429 발생).
    # 문서상 한도 10 req/s에 가장 근접하면서 429가 나지 않는 값.
    elsevier_concurrency: int = 3

    admin_key: str
    database_url: str

    batch_max_enqueued_tokens: int = 5_000_000  # Tier1 한도 10M의 절반 — 타 작업과 공유 여지
    batch_max_concurrent_jobs: int = 2
    batch_max_requests_per_file: int = 1000
    sync_rpm: int = 60

    # Gemini batch(map) 단가 — ai.google.dev/gemini-api/docs/pricing 의
    # gemini-3.1-flash-lite 항목에서 확인(2026-07 기준). Batch는 표준가의 50%.
    # 표준: 입력 $0.25 / 출력 $1.50 → batch: 입력 $0.125 / 출력 $0.75.
    # 출력 단가는 "including thinking tokens" — thinking 토큰이 출력에 포함돼 과금된다.
    # 산문·판정 생성의 seed. 값 자체는 무의미하고 고정돼 있다는 것이 중요하다 —
    # top_k=1과 함께 있을 때만 재현에 기여한다(단독은 무효, gemini_sync.generate 참조).
    gemini_seed: int = 20260905
    gemini_batch_input_usd_per_1m: float = 0.125
    gemini_batch_output_usd_per_1m: float = 0.75
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

    # 월간 자동 분석 스케줄러 — 기존 30초 잡 루프(runner.loop) 안에서 매 틱마다
    # "지금이 실행 시각인가"를 확인하는 방식이라 별도 컨테이너/스케줄러 라이브러리가 없다.
    schedule_enabled: bool = True
    schedule_day: int = 10  # 1~3일은 다른 서비스가 같은 OpenAlex 키를 쓰므로 피한다.
    schedule_hour: int = 3  # KST 새벽 3시대
    schedule_timezone: str = "Asia/Seoul"
    schedule_years_back: int = 1  # 0=당해연도만, 1=당해+직전연도

    http_max_attempts: int = 5
    http_timeout_seconds: float = 60.0
    # 429의 Retry-After가 이 값을 넘으면 대기하지 않고 크레딧 소진(permanent)으로
    # 처리한다. runner.loop()가 활성 분석을 순차로 await하므로 한 요청의 긴 대기가
    # 잡 루프 전체를 멈춘다 — 실측으로 OpenAlex가 43,579초(약 12시간)를 요구해
    # 재추출 110건이 통째로 정지했다. 5분을 넘겨 기다릴 이유가 없다: permanent로
    # 올리면 analysis가 paused로 내려가고 resume_paused가 자정에 자동 재개한다.
    http_max_retry_after_seconds: float = 300.0

    # M16: 관리자 인증이 정적 헤더 하나뿐이라 CORS를 "*"로 열면 방어선이 사실상 없다.
    # 쉼표 구분 오리진 목록. 기본값은 web(nginx) 컨테이너가 서비스되는 오리진으로 좁힌다.
    cors_origins: str = "http://localhost:8103"

    # 푸터에 표시할 도메인명. 빌드 타임이 아니라 .env로 배포 시점에 주입 — 비어 있으면
    # 프론트가 window.location.host로 대체한다.
    site_domain: str = ""
    # 방문자 식별 해시(sha256(ip+user_agent+salt+날짜))에 쓰는 솔트. 원본 IP/UA를
    # 복원하지 못하게 막는 값이므로 운영 배포에서는 반드시 임의의 긴 문자열로 바꿀 것.
    visitor_salt: str = "change-me-in-prod"

    @property
    def reduce_model(self) -> str:
        """보고서 합성에 실제로 쓰는 모델. 미설정이면 추출과 같은 모델."""
        return self.gemini_model_reduce or self.gemini_model

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
