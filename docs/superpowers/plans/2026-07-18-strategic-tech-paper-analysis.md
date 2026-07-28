# 전략기술 논문성과 분석 서비스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전략기술 분야별로 OpenAlex·KCI에서 한국 논문을 검색하고, abstract 기반 map/reduce로 주요 기술적 성과를 정리해 마크다운 보고서와 PDF로 출력하는 서비스를 만든다.

**Architecture:** FastAPI 단일 프로세스가 API와 백그라운드 잡 루프를 함께 호스팅한다. 논문 1건 = Gemini Batch 요청 1건(map, thinking low)으로 성과를 구조화 추출하고, 세부기술 단위로 합성(reduce, thinking high)한다. 잡 상태는 전부 PostgreSQL에 있어 컨테이너 재시작 시 루프가 미완 잡을 이어받는다.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic, httpx, google-genai, PostgreSQL 16, React + TypeScript + Vite + Tailwind + Recharts, Docker Compose

**Spec:** `docs/superpowers/specs/2026-07-18-strategic-tech-paper-analysis-design.md`

## Global Constraints

- Gemini 모델: `gemini-3.1-flash-lite`. map 단계 thinking `low`, reduce 단계 thinking `high`.
- OpenAlex는 **API 키 필수**, 쿼리 파라미터 `api_key=`로 전달. `mailto` 파라미터는 쓰지 않는다(폐지됨).
- OpenAlex 과금은 **요청 건당**. search 계열 $0.001, 순수 메타 filter $0.0001, singleton 무료. 응답 `meta.cost_usd`가 실제 비용이다.
- OpenAlex 일일 예산 $1(UTC 리셋)을 **다른 서비스와 공유**한다. 잔여는 추정하지 않고 응답 `X-RateLimit-*` 헤더 실측값을 쓴다.
- `per_page` 기본 100. basic paging 상한 10,000건 초과 시 cursor 페이징.
- 한국 논문 판정: KCI 전수 + OpenAlex `authorships.institutions.country_code:KR` 포함.
- abstract 없는 논문은 map 대상에서 제외하되, 보고서에 **"검색 M건 / 분석 대상 N건 (abstract 미보유 제외)"** 를 반드시 표기한다.
- 관리자 인증은 `.env`의 `ADMIN_KEY` 단일 값 + `X-Admin-Key` 헤더. 사용자 계정 체계는 만들지 않는다.
- PDF는 서버사이드 생성하지 않는다. `@media print` + `window.print()`.
- 포트: api 8003 / web 8103 / db 5403.
- 사용자 노출 문자열(잡 단계명, 에러 메시지, UI)은 한국어.
- 모든 한도·임계값은 `.env`로 주입하고 코드에 하드코딩하지 않는다.

**참조 코드:** `references/17_Spec-investigation/backend/app/` — 특히 `agents/_doi.py`, `agents/_http_retry.py`, `agents/kci_agent.py`, `agents/openalex_agent.py`의 `_reconstruct_abstract`, `utils.py`의 `run_sync`. 그대로 복사하지 말고 이 플랜의 시그니처에 맞춰 옮긴다. `references/`는 gitignore 대상이라 커밋되지 않는다.

---

## File Structure

```
backend/
  app/
    config.py              # Settings (모든 .env 값)
    database.py            # engine, SessionLocal, get_db
    main.py                # FastAPI app, startup에서 잡 루프 기동
    models/
      field.py             # Field, Subfield
      analysis.py          # Analysis, AnalysisPaper
      paper.py             # Paper, PaperExtraction
      budget.py            # OpenAlexUsage (일일 사용액 기록)
    clients/
      _doi.py              # strip_doi_prefix
      _http.py             # get_with_retry / get_text_with_retry
      openalex.py          # 검색·페이징·abstract 복원·비용 헤더 파싱
      kci.py               # KCI XML 검색
      gemini_batch.py      # Batch JSONL 제출/폴링/파싱
      gemini_sync.py       # 동기 호출 + 토큰버킷 + 백오프
    services/
      budget.py            # OpenAlex 예산 게이트
      search.py            # 소스 병합·중복 제거·papers upsert·검색 캐시
      mapper.py            # map 단계 (batch 제출/수확)
      stats.py             # 통계 집계 (LLM 미사용)
      reducer.py           # reduce + rollup
      runner.py            # 잡 상태머신 + 백그라운드 루프
    routers/
      public.py            # 분야/보고서 조회
      admin.py             # 인증·분야CRUD·미리보기·실행
    prompts.py             # map/reduce/rollup 프롬프트 문자열
  alembic/versions/
  tests/
  requirements.txt
  Dockerfile
frontend/                  # Vite + React + Tailwind
docker-compose.yml
.env.example
```

---

### Task 1: 프로젝트 스캐폴딩 · 설정 · DB 연결

**Files:**
- Create: `backend/requirements.txt`, `backend/Dockerfile`, `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/database.py`, `backend/app/main.py`, `backend/pytest.ini`, `docker-compose.yml`, `.env.example`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `app.config.settings` (Settings 인스턴스), `app.database.SessionLocal`, `app.database.Base`, `app.database.get_db()`

- [ ] **Step 1: requirements.txt 작성**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy==2.0.36
alembic==1.14.0
psycopg2-binary==2.9.10
pydantic-settings==2.7.0
httpx==0.28.1
google-genai==1.0.0
pytest==8.3.4
pytest-asyncio==0.25.0
```

- [ ] **Step 2: 실패하는 설정 테스트 작성**

`backend/tests/test_config.py`:

```python
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
    assert s.max_papers_per_analysis == 5000
    assert s.reduce_group_threshold == 500
    assert s.default_year_range == 3
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 4: config.py 구현**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    gemini_api_key: str
    gemini_model: str = "gemini-3.1-flash-lite"
    thinking_map: str = "low"
    thinking_reduce: str = "high"

    openalex_api_key: str
    openalex_per_page: int = 100
    openalex_daily_budget_usd: float = 0.5
    openalex_search_cost_usd: float = 0.001  # search 계열 요청 1건 단가
    kci_api_key: str = ""
    kci_concurrency: int = 3

    admin_key: str
    database_url: str

    batch_max_enqueued_tokens: int = 5_000_000
    batch_max_concurrent_jobs: int = 2
    batch_max_requests_per_file: int = 1000
    sync_rpm: int = 60
    sync_tpm: int = 1_000_000

    max_papers_per_analysis: int = 5000
    reduce_group_threshold: int = 500
    default_year_range: int = 3
    loop_interval_seconds: int = 30

    http_max_attempts: int = 5
    http_timeout_seconds: float = 60.0

    class Config:
        env_file = ".env"


settings = Settings()
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 6: database.py 구현**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 7: main.py 최소 구현**

```python
import logging

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="전략기술 논문성과 분석")


@app.get("/api/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 8: Dockerfile 작성**

`backend/Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 9: docker-compose.yml 작성**

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-perfrev}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-perfrev}
      POSTGRES_DB: ${POSTGRES_DB:-perfrev}
    ports:
      - "${DB_PORT:-5403}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-perfrev}"]
      interval: 5s
      retries: 10

  api:
    build: ./backend
    env_file: .env
    ports:
      - "${API_PORT:-8003}:8000"
    depends_on:
      db:
        condition: service_healthy

  web:
    build: ./frontend
    ports:
      - "${WEB_PORT:-8103}:80"
    depends_on:
      - api

volumes:
  pgdata:
```

- [ ] **Step 10: .env.example 작성**

```
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.1-flash-lite
THINKING_MAP=low
THINKING_REDUCE=high

OPENALEX_API_KEY=
OPENALEX_PER_PAGE=100
OPENALEX_DAILY_BUDGET_USD=0.5
OPENALEX_SEARCH_COST_USD=0.001
KCI_API_KEY=
KCI_CONCURRENCY=3

ADMIN_KEY=
DATABASE_URL=postgresql://perfrev:perfrev@db:5432/perfrev

BATCH_MAX_ENQUEUED_TOKENS=5000000
BATCH_MAX_CONCURRENT_JOBS=2
BATCH_MAX_REQUESTS_PER_FILE=1000
SYNC_RPM=60
SYNC_TPM=1000000

MAX_PAPERS_PER_ANALYSIS=5000
REDUCE_GROUP_THRESHOLD=500
DEFAULT_YEAR_RANGE=3
LOOP_INTERVAL_SECONDS=30
HTTP_MAX_ATTEMPTS=5
HTTP_TIMEOUT_SECONDS=60

API_PORT=8003
WEB_PORT=8103
DB_PORT=5403
```

- [ ] **Step 11: pytest.ini 작성**

`backend/pytest.ini`:

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

- [ ] **Step 12: 커밋**

```bash
git add backend/requirements.txt backend/Dockerfile backend/pytest.ini \
  backend/app/__init__.py backend/app/config.py backend/app/database.py \
  backend/app/main.py backend/tests/test_config.py docker-compose.yml .env.example
git commit -m "feat: 프로젝트 스캐폴딩 · 설정 · DB 연결"
```

---

### Task 2: 데이터 모델 · 마이그레이션 · 12대 분야 seed

**Files:**
- Create: `backend/alembic.ini` (Step 9의 `alembic init`이 생성), `backend/app/models/__init__.py`, `backend/app/models/field.py`, `backend/app/models/paper.py`, `backend/app/models/analysis.py`, `backend/app/models/budget.py`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/versions/0001_initial.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Consumes: `app.database.Base`
- Produces: `Field(id, name, slug, order_no)`, `Subfield(id, field_id, name, query, query_kci, active)`, `Paper(id, paper_key, title, abstract, year, journal, authors_json, institutions_json, countries_json, citations, source, korea_flag)`, `PaperExtraction(id, paper_key, subfield_id, tech_summary, achievement_type, metrics_json, model_ver)`, `Analysis(id, subfield_id, year, status, report_md, stats_json, snapshot_at, query_hash, searched_count, analyzed_count, batch_job_id, error, sampled)`, `AnalysisPaper(analysis_id, paper_id)`, `OpenAlexUsage(id, usage_date, cost_usd, remaining_reported)`

- [ ] **Step 1: 실패하는 모델 테스트 작성**

`backend/tests/test_models.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.field import Field, Subfield
from app.models.analysis import Analysis


def test_subfield_holds_queries_and_analysis_is_unique_per_year():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    f = Field(name="반도체·디스플레이", slug="semiconductor", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="HBM", query="HBM memory", query_kci=None)
    db.add(sf)
    db.flush()

    db.add(Analysis(subfield_id=sf.id, year=2025, status="pending", query_hash="abc"))
    db.commit()

    assert db.query(Subfield).one().query == "HBM memory"
    assert db.query(Analysis).one().status == "pending"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: field.py 구현**

```python
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Field(Base):
    __tablename__ = "fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    subfields: Mapped[list["Subfield"]] = relationship(back_populates="field")


class Subfield(Base):
    __tablename__ = "subfields"
    __table_args__ = (UniqueConstraint("field_id", "name", name="uq_subfield_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("fields.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_kci: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    field: Mapped[Field] = relationship(back_populates="subfields")

    def kci_query(self) -> str:
        """KCI override가 비어 있으면 공통 검색식을 쓴다."""
        return self.query_kci or self.query
```

- [ ] **Step 4: paper.py 구현**

```python
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database import Base


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    abstract: Mapped[str] = mapped_column(Text, nullable=False, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    journal: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    authors_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    institutions_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    countries_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    citations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # openalex | kci
    korea_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PaperExtraction(Base):
    __tablename__ = "paper_extractions"
    __table_args__ = (
        UniqueConstraint("paper_key", "subfield_id", "model_ver", name="uq_extraction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_key: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    subfield_id: Mapped[int] = mapped_column(ForeignKey("subfields.id"), nullable=False)
    tech_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    achievement_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metrics_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    model_ver: Mapped[str] = mapped_column(String(80), nullable=False)
```

- [ ] **Step 5: analysis.py 구현**

```python
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database import Base


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (UniqueConstraint("subfield_id", "year", name="uq_analysis_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subfield_id: Mapped[int] = mapped_column(ForeignKey("subfields.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # pending | searching | extracting | reducing | done | failed | paused
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    report_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    searched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analyzed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sampled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    batch_job_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnalysisPaper(Base):
    __tablename__ = "analysis_papers"
    __table_args__ = (UniqueConstraint("analysis_id", "paper_id", name="uq_analysis_paper"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), nullable=False, index=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False)
```

- [ ] **Step 6: budget.py 구현**

```python
from datetime import date

from sqlalchemy import Date, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OpenAlexUsage(Base):
    """UTC 날짜별 자체 사용액 누적 + 마지막으로 관측한 서버측 잔여값."""

    __tablename__ = "openalex_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    remaining_reported: Mapped[str | None] = mapped_column(String(50), nullable=True)
```

- [ ] **Step 7: models/__init__.py에 전체 import**

```python
"""모든 모델을 한 곳에서 import — Alembic autogenerate와 FK 해석이 이 파일에 의존한다."""

from app.models.analysis import Analysis, AnalysisPaper  # noqa: F401
from app.models.budget import OpenAlexUsage  # noqa: F401
from app.models.field import Field, Subfield  # noqa: F401
from app.models.paper import Paper, PaperExtraction  # noqa: F401
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 9: Alembic 초기화 후 env.py 수정**

Run: `cd backend && alembic init alembic` 후 `alembic/env.py`의 target_metadata 부분을 교체:

```python
from app.database import Base
from app.config import settings
import app.models  # noqa: F401  — 모든 모델 등록

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", settings.database_url)
```

- [ ] **Step 10: 초기 마이그레이션 생성 + 12대 분야 seed 추가**

Run: `docker compose run --rm api alembic revision --autogenerate -m "initial tables"`

생성된 파일의 `upgrade()` 끝에 seed를 추가한다:

```python
def upgrade() -> None:
    # ... autogenerate가 만든 create_table 들 ...
    fields = sa.table(
        "fields",
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("order_no", sa.Integer),
    )
    op.bulk_insert(fields, [
        {"name": "반도체·디스플레이", "slug": "semiconductor", "order_no": 1},
        {"name": "이차전지", "slug": "battery", "order_no": 2},
        {"name": "첨단 모빌리티", "slug": "mobility", "order_no": 3},
        {"name": "차세대 원자력", "slug": "nuclear", "order_no": 4},
        {"name": "첨단 바이오", "slug": "bio", "order_no": 5},
        {"name": "우주항공·해양", "slug": "space-ocean", "order_no": 6},
        {"name": "수소", "slug": "hydrogen", "order_no": 7},
        {"name": "사이버보안", "slug": "cybersecurity", "order_no": 8},
        {"name": "인공지능", "slug": "ai", "order_no": 9},
        {"name": "차세대 통신", "slug": "communication", "order_no": 10},
        {"name": "첨단로봇·제조", "slug": "robotics", "order_no": 11},
        {"name": "양자", "slug": "quantum", "order_no": 12},
    ])
```

- [ ] **Step 11: 마이그레이션 적용 확인**

Run: `docker compose up -d db && docker compose run --rm api alembic upgrade head`
Expected: `Running upgrade -> 0001, initial tables` 출력, 에러 없음

- [ ] **Step 12: 커밋**

```bash
git add backend/app/models backend/alembic backend/alembic.ini backend/tests/test_models.py
git commit -m "feat: 데이터 모델 · 마이그레이션 · 12대 분야 seed"
```

---

### Task 3: OpenAlex 클라이언트 (abstract 복원 · 페이징 · 비용 관측)

**Files:**
- Create: `backend/app/clients/__init__.py`, `backend/app/clients/_doi.py`, `backend/app/clients/_http.py`, `backend/app/clients/openalex.py`
- Test: `backend/tests/test_openalex.py`

**Interfaces:**
- Consumes: `app.config.settings`
- Produces:
  - `strip_doi_prefix(doi: str | None) -> str | None`
  - `reconstruct_abstract(inv_idx: dict[str, list[int]] | None) -> str`
  - `class OpenAlexResult(NamedTuple): papers: list[dict]; cost_usd: float; remaining: str | None; total_count: int`
  - `async def search(query: str, year_from: int, year_to: int, *, client: httpx.AsyncClient, limit: int) -> OpenAlexResult`
  - `async def count_only(query: str, year_from: int, year_to: int, *, client: httpx.AsyncClient) -> tuple[int, float]`
  - paper dict 키: `paper_key, title, abstract, year, journal, doi, authors, institutions, countries, citations, source, korea_flag`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_openalex.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_openalex.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.clients'`

- [ ] **Step 3: _doi.py 구현**

```python
"""DOI 정규화. KCI/OpenAlex가 서로 다른 URL 형태로 DOI를 주므로 bare `10.x/...`로 통일한다."""

_PREFIXES = (
    "http://dx.doi.org/", "https://dx.doi.org/",
    "http://doi.org/", "https://doi.org/",
    "doi.org/", "dx.doi.org/",
)


def strip_doi_prefix(doi: str | None) -> str | None:
    if not doi:
        return None
    s = doi.strip()
    if not s:
        return None
    for prefix in _PREFIXES:
        if s.startswith(prefix):
            return s[len(prefix):].lower()
    return s.lower()
```

- [ ] **Step 4: _http.py 구현**

```python
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class RateLimited(RuntimeError):
    """429. `permanent`가 True면 그날의 크레딧이 소진된 것이라 백오프해도 풀리지 않는다."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


async def get_with_retry(
    url: str,
    *,
    client: httpx.AsyncClient,
    params: dict | None = None,
    service_name: str,
    context: str = "",
    max_attempts: int | None = None,   # None이면 settings 값 사용
    timeout: float | None = None,
) -> httpx.Response:
    """GET + 지수 백오프. 429는 헤더로 일시/영구를 구분해 RateLimited로 올린다."""
    for attempt in range(max_attempts):
        try:
            response = await client.get(url, params=params, timeout=timeout)
        except httpx.TimeoutException as e:
            raise RuntimeError(f"{service_name} 타임아웃: {context}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"{service_name} 네트워크 오류: {context}") from e

        if response.status_code == 429:
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is not None and _as_float(remaining) <= 0:
                raise RateLimited(f"{service_name} 일일 크레딧 소진", permanent=True)
            retry_after = _as_float(response.headers.get("Retry-After"))
            delay = retry_after if retry_after else 2 ** attempt
            logger.warning("%s 429 (%d/%d), %.1fs 후 재시도", service_name, attempt + 1, max_attempts, delay)
            await asyncio.sleep(delay)
            continue

        if response.status_code >= 400:
            raise RuntimeError(f"{service_name} 오류 {response.status_code}: {context}")
        return response

    raise RateLimited(f"{service_name} 429 재시도 소진: {context}")


def _as_float(value: str | None) -> float:
    try:
        return float(value) if value is not None else 0.0
    except ValueError:
        return 0.0
```

- [ ] **Step 5: openalex.py 구현**

```python
import logging
from typing import NamedTuple

import httpx

from app.clients._doi import strip_doi_prefix
from app.clients._http import get_with_retry
from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.openalex.org/works"
SELECT = (
    "id,doi,title,publication_year,cited_by_count,"
    "abstract_inverted_index,primary_location,authorships"
)


class OpenAlexResult(NamedTuple):
    papers: list[dict]
    cost_usd: float
    remaining: str | None
    total_count: int


def reconstruct_abstract(inv_idx: dict[str, list[int]] | None) -> str:
    """OpenAlex는 abstract를 단어→위치 역색인으로 준다. 위치 순으로 되돌린다."""
    if not inv_idx:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inv_idx.items():
        for idx in idxs:
            positions[idx] = word
    if not positions:
        return ""
    return " ".join(positions[i] for i in sorted(positions))


def _parse_work(work: dict) -> dict:
    doi = strip_doi_prefix(work.get("doi"))
    oa_id = (work.get("id") or "").rsplit("/", 1)[-1]
    authorships = work.get("authorships") or []

    authors, institutions, countries = [], [], []
    for a in authorships:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
        for inst in a.get("institutions") or []:
            if inst.get("display_name"):
                institutions.append(inst["display_name"])
            code = inst.get("country_code")
            if code and code not in countries:
                countries.append(code)

    location = work.get("primary_location") or {}
    return {
        "paper_key": doi or f"openalex:{oa_id}",
        "title": work.get("title") or "",
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "year": work.get("publication_year"),
        "journal": (location.get("source") or {}).get("display_name"),
        "doi": doi,
        "authors": authors,
        "institutions": institutions,
        "countries": countries,
        "citations": int(work.get("cited_by_count") or 0),
        "source": "openalex",
        "korea_flag": "KR" in countries,
    }


def _sanitize_query(query: str) -> str:
    """OpenAlex filter DSL은 콤마를 AND, 파이프를 OR 구분자로 쓰고 이스케이프 수단이 없다.
    검색어에 이 문자가 들어가면 에러 없이 다른 필터로 해석되므로 공백으로 치환한다."""
    return query.replace(",", " ").replace("|", " ")


def _filter_expr(query: str, year_from: int, year_to: int) -> str:
    """연도를 범위로 한 번에 건다 — 연도별 개별 조회 대비 콜수가 1/N이 된다.
    KR 필터를 서버측에 걸어 불필요한 페이지를 받지 않는다."""
    return (
        f"title_and_abstract.search:{_sanitize_query(query)},"
        f"publication_year:{year_from}-{year_to},"
        f"authorships.institutions.country_code:KR"
    )


def _base_params(query: str, year_from: int, year_to: int) -> dict:
    return {
        "filter": _filter_expr(query, year_from, year_to),
        "api_key": settings.openalex_api_key,
    }


async def count_only(
    query: str, year_from: int, year_to: int, *, client: httpx.AsyncClient
) -> tuple[int, float]:
    """검색 건수만 확인한다(미리보기·실행 전 견적용). per_page=1로 1콜."""
    params = {**_base_params(query, year_from, year_to), "per-page": 1, "select": "id"}
    response = await get_with_retry(
        API_URL, client=client, params=params, service_name="OpenAlex", context=query
    )
    data = response.json()
    meta = data.get("meta") or {}
    return int(meta.get("count") or 0), float(meta.get("cost_usd") or 0.0)


async def search(
    query: str, year_from: int, year_to: int, *, client: httpx.AsyncClient, limit: int
) -> OpenAlexResult:
    """cursor 페이징으로 최대 `limit`건 수집. 비용과 잔여 헤더를 누적해 함께 반환한다."""
    papers: list[dict] = []
    cost = 0.0
    remaining: str | None = None
    total = 0
    cursor = "*"

    while cursor and len(papers) < limit:
        params = {
            **_base_params(query, year_from, year_to),
            "per-page": min(settings.openalex_per_page, limit - len(papers)),
            "select": SELECT,
            "cursor": cursor,
        }
        response = await get_with_retry(
            API_URL, client=client, params=params, service_name="OpenAlex", context=query
        )
        data = response.json()
        meta = data.get("meta") or {}
        cost += float(meta.get("cost_usd") or 0.0)
        remaining = response.headers.get("X-RateLimit-Remaining", remaining)
        total = int(meta.get("count") or total)

        results = data.get("results") or []
        if not results:
            break
        papers.extend(_parse_work(w) for w in results)
        cursor = meta.get("next_cursor")

    logger.info(
        "[OpenAlex] query=%r %d-%d total=%d fetched=%d cost=$%.4f",
        query, year_from, year_to, total, len(papers), cost,
    )
    return OpenAlexResult(papers=papers, cost_usd=cost, remaining=remaining, total_count=total)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_openalex.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: 커밋**

```bash
git add backend/app/clients backend/tests/test_openalex.py
git commit -m "feat: OpenAlex 클라이언트 — abstract 복원 · cursor 페이징 · 비용 관측"
```

---

### Task 4: KCI 클라이언트

**Files:**
- Create: `backend/app/clients/kci.py`
- Test: `backend/tests/test_kci.py`

**Interfaces:**
- Consumes: `app.clients._doi.strip_doi_prefix`, `app.clients._http.get_with_retry`
- Produces: `async def search(query: str, year_from: int, year_to: int, *, client: httpx.AsyncClient, limit: int) -> list[dict]` — Task 3과 동일한 paper dict 키를 반환하며 `source="kci"`, `korea_flag=True` 고정

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_kci.py`:

```python
from app.clients.kci import _parse_search_xml

XML = """<MetaData><outputData>
<record>
  <journalInfo><journal-name>한국반도체학회지</journal-name><pub-year>2025</pub-year></journalInfo>
  <articleInfo article-id="ART001">
    <title-group>
      <article-title lang="original">고대역폭 메모리</article-title>
      <article-title lang="english">High Bandwidth Memory</article-title>
    </title-group>
    <abstract-group>
      <abstract lang="english">We present a TSV process.</abstract>
    </abstract-group>
    <doi>http://dx.doi.org/10.1234/abc</doi>
    <citation-count kci="3">3</citation-count>
  </articleInfo>
</record>
<record>
  <journalInfo><journal-name>J</journal-name><pub-year>2025</pub-year></journalInfo>
  <articleInfo article-id="ART002">
    <title-group><article-title lang="english">No Abstract</article-title></title-group>
  </articleInfo>
</record>
</outputData></MetaData>"""


def test_parse_prefers_english_and_flags_korea():
    papers = _parse_search_xml(XML)
    assert len(papers) == 2
    p = papers[0]
    assert p["paper_key"] == "10.1234/abc"
    assert p["title"] == "High Bandwidth Memory"
    assert p["abstract"] == "We present a TSV process."
    assert p["year"] == 2025
    assert p["citations"] == 3
    assert p["journal"] == "한국반도체학회지"
    assert p["korea_flag"] is True
    assert p["countries"] == ["KR"]
    assert p["source"] == "kci"


def test_parse_without_doi_uses_article_id_and_keeps_empty_abstract():
    """abstract 없는 논문도 파싱은 한다 — 검색 건수 통계에 필요하고, 제외는 filter 단계에서 한다."""
    papers = _parse_search_xml(XML)
    assert papers[1]["paper_key"] == "kci:ART002"
    assert papers[1]["abstract"] == ""
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_kci.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.clients.kci'`

- [ ] **Step 3: kci.py 구현**

```python
import logging
import xml.etree.ElementTree as ET

import httpx

from app.clients._doi import strip_doi_prefix
from app.clients._http import get_with_retry
from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://open.kci.go.kr/po/openapi/openApiSearch.kci"
PAGE_SIZE = 100


def _pick_by_lang(elements: list[ET.Element], preferred: str = "english") -> str:
    """KCI는 lang으로 "english" / "original"(보통 한국어) / "foreign"을 쓴다.
    영문 우선, 없으면 첫 비어있지 않은 값."""
    fallback = ""
    for el in elements:
        text = (el.text or "").strip()
        if not text:
            continue
        if el.get("lang") == preferred:
            return text
        if not fallback:
            fallback = text
    return fallback


def _int_or(text: str | None, default: int) -> int:
    try:
        return int((text or "").strip())
    except ValueError:
        return default


def _parse_search_xml(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"KCI XML 파싱 실패: {e}") from e

    papers: list[dict] = []
    for record in root.iter("record"):
        article = record.find("articleInfo")
        if article is None:
            continue
        article_id = article.get("article-id")
        if not article_id:
            continue

        journal_info = record.find("journalInfo")
        journal = year = None
        if journal_info is not None:
            journal_el = journal_info.find("journal-name")
            journal = (journal_el.text or "").strip() if journal_el is not None else None
            year_el = journal_info.find("pub-year")
            year = _int_or(year_el.text if year_el is not None else None, 0) or None

        doi_el = article.find("doi")
        doi = strip_doi_prefix(doi_el.text if doi_el is not None else None)
        citation_el = article.find("citation-count")
        citations = _int_or(citation_el.text if citation_el is not None else None, 0)

        papers.append({
            "paper_key": doi or f"kci:{article_id}",
            "title": _pick_by_lang(list(article.findall("title-group/article-title"))),
            "abstract": _pick_by_lang(list(article.findall("abstract-group/abstract"))),
            "year": year,
            "journal": journal,
            "doi": doi,
            "authors": [],
            "institutions": [],
            "countries": ["KR"],
            "citations": citations,
            "source": "kci",
            "korea_flag": True,
        })
    return papers


async def search(
    query: str, year_from: int, year_to: int, *, client: httpx.AsyncClient, limit: int
) -> list[dict]:
    """KCI 키워드 검색. 키 미설정 시 조용히 빈 리스트(graceful no-op).

    KCI는 연도 필터 파라미터가 없어 응답을 받은 뒤 코드에서 연도를 거른다.
    """
    if not settings.kci_api_key:
        logger.info("[KCI] KCI_API_KEY 미설정 — 건너뜀")
        return []

    collected: list[dict] = []
    page = 1
    while len(collected) < limit:
        params = {
            "apiCode": "articleSearch",
            "key": settings.kci_api_key,
            "keyword": query,
            "displayCount": PAGE_SIZE,
            "page": page,
        }
        response = await get_with_retry(
            API_URL, client=client, params=params, service_name="KCI", context=query
        )
        batch = _parse_search_xml(response.text)
        if not batch:
            break
        collected.extend(p for p in batch if p["year"] and year_from <= p["year"] <= year_to)
        if len(batch) < PAGE_SIZE:
            break
        page += 1

    logger.info("[KCI] query=%r %d-%d fetched=%d", query, year_from, year_to, len(collected))
    return collected[:limit]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_kci.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/clients/kci.py backend/tests/test_kci.py
git commit -m "feat: KCI 클라이언트"
```

---

### Task 5: OpenAlex 예산 게이트

**Files:**
- Create: `backend/app/services/__init__.py`, `backend/app/services/budget.py`
- Test: `backend/tests/test_budget.py`

**Interfaces:**
- Consumes: `app.models.budget.OpenAlexUsage`, `app.config.settings`
- Produces:
  - `class BudgetExceeded(RuntimeError)`
  - `def spent_today(db: Session) -> float`
  - `def check_budget(db: Session, estimated_cost: float) -> None` — 초과 시 `BudgetExceeded`
  - `def record_usage(db: Session, cost_usd: float, remaining: str | None) -> None`
  - `def reset_time_utc() -> datetime` — 다음 UTC 자정

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_budget.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.budget import BudgetExceeded, check_budget, record_usage, spent_today


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_usage_accumulates_within_the_same_day(db):
    record_usage(db, 0.01, "0.9")
    record_usage(db, 0.02, "0.87")
    assert spent_today(db) == pytest.approx(0.03)


def test_check_budget_passes_under_limit(db):
    record_usage(db, 0.1, None)
    check_budget(db, 0.1)  # 0.2 < 0.5 → 통과


def test_check_budget_blocks_when_projected_over_limit(db):
    record_usage(db, 0.45, None)
    with pytest.raises(BudgetExceeded) as e:
        check_budget(db, 0.1)  # 0.55 > 0.5
    assert "예산" in str(e.value)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: budget.py 구현**

```python
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.budget import OpenAlexUsage


class BudgetExceeded(RuntimeError):
    pass


def _today() -> datetime.date:
    return datetime.now(timezone.utc).date()


def reset_time_utc() -> datetime:
    """OpenAlex 예산이 리셋되는 다음 UTC 자정."""
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _row(db: Session) -> OpenAlexUsage:
    row = db.query(OpenAlexUsage).filter(OpenAlexUsage.usage_date == _today()).first()
    if not row:
        row = OpenAlexUsage(usage_date=_today(), cost_usd=0.0)
        db.add(row)
        db.flush()
    return row


def spent_today(db: Session) -> float:
    return _row(db).cost_usd


def check_budget(db: Session, estimated_cost: float) -> None:
    """예상 비용을 더해도 이 서비스 몫을 넘지 않는지 확인한다."""
    projected = spent_today(db) + estimated_cost
    if projected > settings.openalex_daily_budget_usd:
        raise BudgetExceeded(
            f"OpenAlex 일일 예산 초과: 사용 ${spent_today(db):.4f} + 예상 ${estimated_cost:.4f} "
            f"> 한도 ${settings.openalex_daily_budget_usd:.2f}. "
            f"UTC {reset_time_utc():%Y-%m-%d %H:%M} 이후 재시도하세요."
        )


def record_usage(db: Session, cost_usd: float, remaining: str | None) -> None:
    """실제 발생 비용을 누적하고, 서버가 보고한 잔여값을 함께 남긴다.

    remaining은 공유 키를 쓰는 다른 서비스의 소비까지 반영된 실측값이라
    자체 누적치보다 신뢰도가 높다 — 진단용으로 보존한다.
    """
    row = _row(db)
    row.cost_usd += cost_usd
    if remaining is not None:
        row.remaining_reported = remaining
    db.commit()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_budget.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/__init__.py backend/app/services/budget.py backend/tests/test_budget.py
git commit -m "feat: OpenAlex 일일 예산 게이트"
```

---

### Task 6: 검색 오케스트레이션 (병합 · upsert · 검색 캐시)

**Files:**
- Create: `backend/app/services/search.py`
- Test: `backend/tests/test_search.py`

**Interfaces:**
- Consumes: `app.clients.openalex.search`, `app.clients.kci.search`, `app.services.budget`
- Produces:
  - `def query_hash(subfield: Subfield, year_from: int, year_to: int) -> str`
  - `def merge_papers(*sources: list[dict]) -> list[dict]` — `paper_key` 기준 중복 제거, 필드가 더 채워진 쪽 우선
  - `async def collect(db, subfield, year_from, year_to, *, client) -> list[dict]`
  - `def upsert_papers(db: Session, papers: list[dict]) -> list[Paper]`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_search.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.field import Field, Subfield
from app.models.paper import Paper
from app.services.search import merge_papers, query_hash, upsert_papers


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _paper(key, **kw):
    base = {"paper_key": key, "title": "T", "abstract": "", "year": 2025, "journal": None,
            "doi": None, "authors": [], "institutions": [], "countries": [],
            "citations": 0, "source": "openalex", "korea_flag": True}
    base.update(kw)
    return base


def test_merge_prefers_the_record_with_an_abstract():
    oa = [_paper("10.1/x", abstract="", source="openalex")]
    kci = [_paper("10.1/x", abstract="있음", source="kci")]
    merged = merge_papers(oa, kci)
    assert len(merged) == 1
    assert merged[0]["abstract"] == "있음"


def test_merge_keeps_distinct_keys():
    merged = merge_papers([_paper("a")], [_paper("b")])
    assert {p["paper_key"] for p in merged} == {"a", "b"}


def test_query_hash_changes_when_query_changes():
    sf1 = Subfield(field_id=1, name="HBM", query="A", query_kci=None)
    sf2 = Subfield(field_id=1, name="HBM", query="B", query_kci=None)
    assert query_hash(sf1, 2024, 2026) != query_hash(sf2, 2024, 2026)
    assert query_hash(sf1, 2024, 2026) == query_hash(sf1, 2024, 2026)


def test_query_hash_changes_when_kci_override_changes():
    sf1 = Subfield(field_id=1, name="HBM", query="A", query_kci=None)
    sf2 = Subfield(field_id=1, name="HBM", query="A", query_kci="한글")
    assert query_hash(sf1, 2024, 2026) != query_hash(sf2, 2024, 2026)


def test_upsert_is_idempotent_and_fills_missing_abstract(db):
    upsert_papers(db, [_paper("k1", abstract="")])
    upsert_papers(db, [_paper("k1", abstract="채워짐")])
    rows = db.query(Paper).all()
    assert len(rows) == 1
    assert rows[0].abstract == "채워짐"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.search'`

- [ ] **Step 3: search.py 구현**

```python
import hashlib
import logging

import httpx
from sqlalchemy.orm import Session

from app.clients import kci, openalex
from app.config import settings
from app.models.field import Subfield
from app.models.paper import Paper
from app.services import budget

logger = logging.getLogger(__name__)


def query_hash(subfield: Subfield, year_from: int, year_to: int) -> str:
    """검색식이나 연도 범위가 바뀌면 해시가 달라져 해당 분석이 '갱신 필요'로 표시된다."""
    raw = f"{subfield.query}\x00{subfield.query_kci or ''}\x00{year_from}-{year_to}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _score(paper: dict) -> tuple:
    """병합 시 우선순위 — abstract 보유 > 저자 정보 보유 > 인용수."""
    return (bool(paper.get("abstract")), len(paper.get("authors") or []), paper.get("citations") or 0)


def merge_papers(*sources: list[dict]) -> list[dict]:
    """paper_key 기준 중복 제거. 같은 키면 정보가 더 채워진 레코드를 남긴다."""
    best: dict[str, dict] = {}
    for source in sources:
        for paper in source:
            key = paper["paper_key"]
            if key not in best or _score(paper) > _score(best[key]):
                best[key] = paper
    return list(best.values())


async def collect(
    db: Session,
    subfield: Subfield,
    year_from: int,
    year_to: int,
    *,
    client: httpx.AsyncClient,
) -> list[dict]:
    """OpenAlex + KCI 검색 후 병합. OpenAlex 비용은 실측해 예산에 기록한다."""
    count, count_cost = await openalex.count_only(subfield.query, year_from, year_to, client=client)
    pages = max(1, -(-min(count, settings.max_papers_per_analysis) // settings.openalex_per_page))
    budget.check_budget(db, count_cost + pages * count_cost)

    oa = await openalex.search(
        subfield.query, year_from, year_to, client=client, limit=settings.max_papers_per_analysis
    )
    budget.record_usage(db, count_cost + oa.cost_usd, oa.remaining)

    kci_papers = await kci.search(
        subfield.kci_query(), year_from, year_to,
        client=client, limit=settings.max_papers_per_analysis,
    )

    merged = merge_papers(oa.papers, kci_papers)
    logger.info(
        "[검색] %s %d-%d: OpenAlex %d + KCI %d → 병합 %d",
        subfield.name, year_from, year_to, len(oa.papers), len(kci_papers), len(merged),
    )
    return merged


_FIELDS = ("title", "abstract", "year", "journal", "doi", "authors", "institutions",
           "countries", "citations", "source", "korea_flag")
_JSON_MAP = {"authors": "authors_json", "institutions": "institutions_json",
             "countries": "countries_json"}


def upsert_papers(db: Session, papers: list[dict]) -> list[Paper]:
    """paper_key 기준 upsert. 기존 행은 값이 더 채워진 경우에만 덮어쓴다."""
    keys = [p["paper_key"] for p in papers]
    existing = {r.paper_key: r for r in db.query(Paper).filter(Paper.paper_key.in_(keys)).all()}

    rows: list[Paper] = []
    for paper in papers:
        row = existing.get(paper["paper_key"])
        if row is None:
            row = Paper(paper_key=paper["paper_key"])
            db.add(row)
            existing[paper["paper_key"]] = row
        for field in _FIELDS:
            attr = _JSON_MAP.get(field, field)
            new_value = paper.get(field)
            if new_value or not getattr(row, attr, None):
                setattr(row, attr, new_value)
        rows.append(row)

    db.commit()
    return rows
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_search.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/search.py backend/tests/test_search.py
git commit -m "feat: 검색 오케스트레이션 — 소스 병합 · papers upsert · query_hash"
```

---

### Task 7: Gemini Batch map 단계

**Files:**
- Create: `backend/app/prompts.py`, `backend/app/clients/gemini_batch.py`, `backend/app/services/mapper.py`
- Test: `backend/tests/test_mapper.py`

**Interfaces:**
- Consumes: `app.models.paper.Paper`, `app.models.paper.PaperExtraction`, `app.config.settings`
- Produces:
  - `prompts.MAP_INSTRUCTION: str`, `prompts.map_user_text(title: str, abstract: str) -> str`
  - `gemini_batch.submit(requests: list[dict], *, thinking: str) -> str` — batch job name 반환
  - `gemini_batch.poll(job_name: str) -> tuple[str, list[dict] | None]` — `(state, results)`. state는 `running | succeeded | failed`
  - `mapper.model_ver() -> str`
  - `mapper.pending_papers(db, analysis, papers: list[Paper]) -> list[Paper]` — 캐시 히트·abstract 없는 논문 제외
  - `mapper.build_requests(papers: list[Paper]) -> list[dict]`
  - `mapper.save_results(db, analysis, results: list[dict]) -> int`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_mapper.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.models.paper import Paper, PaperExtraction
from app.services import mapper


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    f = Field(name="양자", slug="quantum", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="양자컴퓨팅", query="quantum computing")
    db.add(sf)
    db.flush()
    a = Analysis(subfield_id=sf.id, year=2025, status="extracting", query_hash="h")
    db.add(a)
    db.flush()
    db.commit()
    return db, a


def _paper(db, key, abstract):
    p = Paper(paper_key=key, title="T", abstract=abstract, year=2025, source="openalex",
              korea_flag=True)
    db.add(p)
    db.commit()
    return p


def test_pending_excludes_papers_without_abstract(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    p2 = _paper(db, "k2", "")
    pending = mapper.pending_papers(db, a, [p1, p2])
    assert [p.paper_key for p in pending] == ["k1"]


def test_pending_excludes_cache_hits(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    db.add(PaperExtraction(paper_key="k1", subfield_id=a.subfield_id,
                           tech_summary="이미 있음", model_ver=mapper.model_ver()))
    db.commit()
    assert mapper.pending_papers(db, a, [p1]) == []


def test_pending_ignores_extraction_from_another_subfield(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    db.add(PaperExtraction(paper_key="k1", subfield_id=999,
                           tech_summary="다른 분야", model_ver=mapper.model_ver()))
    db.commit()
    assert [p.paper_key for p in mapper.pending_papers(db, a, [p1])] == ["k1"]


def test_build_requests_carries_paper_key_as_the_request_key(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "초록 본문")
    reqs = mapper.build_requests([p1])
    assert reqs[0]["key"] == "k1"
    assert "초록 본문" in reqs[0]["request"]["contents"][0]["parts"][0]["text"]


def test_save_results_writes_extractions(ctx):
    db, a = ctx
    _paper(db, "k1", "있음")
    saved = mapper.save_results(db, a, [
        {"key": "k1", "tech_summary": "TSV 피치 개선", "achievement_type": "공정",
         "metrics": [{"name": "피치", "value": "20", "unit": "um"}]},
    ])
    assert saved == 1
    row = db.query(PaperExtraction).one()
    assert row.tech_summary == "TSV 피치 개선"
    assert row.achievement_type == "공정"
    assert row.metrics_json[0]["unit"] == "um"


def test_save_results_is_idempotent(ctx):
    db, a = ctx
    _paper(db, "k1", "있음")
    payload = [{"key": "k1", "tech_summary": "A", "achievement_type": "공정", "metrics": []}]
    mapper.save_results(db, a, payload)
    mapper.save_results(db, a, payload)
    assert db.query(PaperExtraction).count() == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_mapper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.mapper'`

- [ ] **Step 3: prompts.py 작성 (map 부분)**

```python
MAP_INSTRUCTION = """당신은 한국 연구성과를 분석하는 과학기술 분석가입니다.
논문 한 편의 제목과 초록만 보고, 그 논문이 달성한 **기술적 성과**를 정리하세요.

규칙:
- 초록에 명시된 내용만 사용하고 추측하지 마세요.
- tech_summary: 무엇을 어떻게 달성했는지 1~2문장. 연구 동기나 배경은 빼고 성과만.
- achievement_type: 신소자, 신소재, 공정, 알고리즘, 아키텍처, 성능향상, 시스템구현, 이론/해석, 기타 중 하나.
- metrics: 초록에 수치가 있을 때만 채우고, 없으면 빈 배열.
- 한국어로 작성하세요."""

MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "tech_summary": {"type": "string"},
        "achievement_type": {"type": "string"},
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                },
                "required": ["name", "value"],
            },
        },
    },
    "required": ["tech_summary", "achievement_type", "metrics"],
}


def map_user_text(title: str, abstract: str) -> str:
    return f"제목: {title}\n\n초록: {abstract}"
```

- [ ] **Step 4: gemini_batch.py 구현**

```python
import json
import logging
import tempfile
from pathlib import Path

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=settings.gemini_api_key)

_TERMINAL_OK = "JOB_STATE_SUCCEEDED"
_TERMINAL_BAD = ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")


def submit(requests: list[dict], *, thinking: str) -> str:
    """JSONL 파일을 업로드해 batch 잡을 만들고 잡 이름을 반환한다.

    inline이 아닌 파일 방식인 이유 — 논문 수천 건이면 inline 페이로드 상한을 넘는다.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        for req in requests:
            fh.write(json.dumps(req, ensure_ascii=False) + "\n")
        path = Path(fh.name)

    try:
        uploaded = _client.files.upload(
            file=str(path), config={"mime_type": "application/jsonl"}
        )
        job = _client.batches.create(
            model=settings.gemini_model,
            src=uploaded.name,
            config=types.CreateBatchJobConfig(display_name=f"map-{len(requests)}"),
        )
    finally:
        path.unlink(missing_ok=True)

    logger.info("[batch] 제출 %d건 → %s (thinking=%s)", len(requests), job.name, thinking)
    return job.name


def poll(job_name: str) -> tuple[str, list[dict] | None]:
    """(state, results). state는 running | succeeded | failed."""
    job = _client.batches.get(name=job_name)
    state = job.state.name if hasattr(job.state, "name") else str(job.state)

    if state in _TERMINAL_BAD:
        logger.error("[batch] %s 실패: %s", job_name, state)
        return "failed", None
    if state != _TERMINAL_OK:
        return "running", None

    return "succeeded", _download_results(job)


def _download_results(job) -> list[dict]:
    """결과 JSONL을 파싱해 [{key, tech_summary, achievement_type, metrics}] 로 정규화한다.

    개별 요청이 실패했거나 JSON이 깨진 건은 조용히 건너뛴다 — 논문 한 편 때문에
    전체 분석을 죽이지 않는다.
    """
    raw = _client.files.download(file=job.dest.file_name)
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw

    results: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            key = record.get("key")
            candidates = record["response"]["candidates"]
            payload = json.loads(candidates[0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.warning("[batch] 결과 1건 파싱 실패: %s", e)
            continue
        if not key:
            continue
        results.append({
            "key": key,
            "tech_summary": payload.get("tech_summary", ""),
            "achievement_type": payload.get("achievement_type"),
            "metrics": payload.get("metrics") or [],
        })

    logger.info("[batch] 결과 수확 %d건", len(results))
    return results
```

- [ ] **Step 5: mapper.py 구현**

```python
import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.models.analysis import Analysis
from app.models.paper import Paper, PaperExtraction
from app.prompts import MAP_INSTRUCTION, MAP_SCHEMA, map_user_text

logger = logging.getLogger(__name__)


def model_ver() -> str:
    """모델이나 thinking 설정이 바뀌면 캐시를 무효화해야 하므로 버전 문자열에 함께 넣는다."""
    return f"{settings.gemini_model}/{settings.thinking_map}"


def pending_papers(db: Session, analysis: Analysis, papers: list[Paper]) -> list[Paper]:
    """abstract가 있고 아직 이 세부기술로 추출되지 않은 논문만 남긴다."""
    with_abstract = [p for p in papers if p.abstract]
    if not with_abstract:
        return []

    keys = [p.paper_key for p in with_abstract]
    cached = {
        row.paper_key
        for row in db.query(PaperExtraction.paper_key).filter(
            PaperExtraction.paper_key.in_(keys),
            PaperExtraction.subfield_id == analysis.subfield_id,
            PaperExtraction.model_ver == model_ver(),
        )
    }
    pending = [p for p in with_abstract if p.paper_key not in cached]
    logger.info(
        "[map] 대상 %d건 (abstract 보유 %d / 캐시 히트 %d)",
        len(pending), len(with_abstract), len(cached),
    )
    return pending


def build_requests(papers: list[Paper]) -> list[dict]:
    """paper_key를 요청 key로 실어 결과를 논문에 되짚을 수 있게 한다."""
    return [
        {
            "key": p.paper_key,
            "request": {
                "contents": [
                    {"role": "user",
                     "parts": [{"text": map_user_text(p.title, p.abstract)}]}
                ],
                "system_instruction": {"parts": [{"text": MAP_INSTRUCTION}]},
                "generation_config": {
                    "response_mime_type": "application/json",
                    "response_schema": MAP_SCHEMA,
                    "thinking_config": {"thinking_level": settings.thinking_map},
                },
            },
        }
        for p in papers
    ]


def chunks(requests: list[dict]) -> list[list[dict]]:
    size = settings.batch_max_requests_per_file
    return [requests[i:i + size] for i in range(0, len(requests), size)]


def estimate_tokens(papers: list[Paper]) -> int:
    """제출 전 게이트 판단용 근사치. 문자수/4 — ±20% 오차면 충분하고,
    논문마다 count_tokens를 부르면 그 호출 자체가 낭비다."""
    instruction_len = len(MAP_INSTRUCTION)
    return sum((len(p.title) + len(p.abstract) + instruction_len) // 4 for p in papers)


def save_results(db: Session, analysis: Analysis, results: list[dict]) -> int:
    """추출 결과를 저장한다. 같은 (paper_key, subfield, model_ver)는 덮어쓴다."""
    saved = 0
    for item in results:
        row = db.query(PaperExtraction).filter(
            PaperExtraction.paper_key == item["key"],
            PaperExtraction.subfield_id == analysis.subfield_id,
            PaperExtraction.model_ver == model_ver(),
        ).first()
        if row is None:
            row = PaperExtraction(
                paper_key=item["key"],
                subfield_id=analysis.subfield_id,
                model_ver=model_ver(),
            )
            db.add(row)
        row.tech_summary = item.get("tech_summary", "")
        row.achievement_type = item.get("achievement_type")
        row.metrics_json = item.get("metrics") or []
        saved += 1
    db.commit()
    return saved
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_mapper.py -v`
Expected: PASS (6 passed)

- [ ] **Step 7: 커밋**

```bash
git add backend/app/prompts.py backend/app/clients/gemini_batch.py \
  backend/app/services/mapper.py backend/tests/test_mapper.py
git commit -m "feat: Gemini Batch map 단계 — 논문단위 추출 · 캐시 제외"
```

---

### Task 8: 통계 집계 (LLM 미사용)

**Files:**
- Create: `backend/app/services/stats.py`
- Test: `backend/tests/test_stats.py`

**Interfaces:**
- Consumes: `app.models.paper.Paper`, `app.models.paper.PaperExtraction`
- Produces: `def compute(papers: list[Paper], extractions: list[PaperExtraction], *, snapshot_at: datetime) -> dict`
  반환 키: `searched_count, analyzed_count, no_abstract_count, by_year, by_source, top_institutions, top_journals, top_authors, intl_collab_ratio, top_partner_countries, citations, top_cited, by_achievement_type, snapshot_at`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_stats.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.stats'`

- [ ] **Step 3: stats.py 구현**

```python
"""통계는 전부 코드로 집계한다 — 숫자를 LLM에 맡기면 틀린다.

검색된 전체 모집단(papers)과 실제 LLM 분석 대상(extractions)의 크기가 다르므로
searched_count / analyzed_count / no_abstract_count를 모두 노출해 보고서에서 구분한다.
"""

import statistics
from collections import Counter
from datetime import datetime

from app.models.paper import Paper, PaperExtraction

TOP_N = 20


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[idx]


def compute(
    papers: list[Paper],
    extractions: list[PaperExtraction],
    *,
    snapshot_at: datetime,
) -> dict:
    citations = [p.citations or 0 for p in papers]
    partner_counter: Counter = Counter()
    intl = 0
    for p in papers:
        others = [c for c in (p.countries_json or []) if c != "KR"]
        if others:
            intl += 1
            partner_counter.update(others)

    top_cited = sorted(papers, key=lambda p: p.citations or 0, reverse=True)[:10]

    return {
        "searched_count": len(papers),
        "analyzed_count": len(extractions),
        "no_abstract_count": sum(1 for p in papers if not p.abstract),
        "by_year": dict(sorted(Counter(p.year for p in papers if p.year).items())),
        "by_source": dict(Counter(p.source for p in papers)),
        "top_institutions": Counter(
            i for p in papers for i in (p.institutions_json or [])
        ).most_common(TOP_N),
        "top_journals": Counter(p.journal for p in papers if p.journal).most_common(TOP_N),
        "top_authors": Counter(
            a for p in papers for a in (p.authors_json or [])
        ).most_common(TOP_N),
        "intl_collab_ratio": round(intl / len(papers), 4) if papers else 0.0,
        "top_partner_countries": partner_counter.most_common(10),
        "citations": {
            "median": int(statistics.median(citations)) if citations else 0,
            "p90": _percentile(citations, 0.9),
            "total": sum(citations),
        },
        "top_cited": [
            {"title": p.title, "citations": p.citations or 0, "year": p.year,
             "journal": p.journal, "doi": p.doi}
            for p in top_cited
        ],
        "by_achievement_type": dict(
            Counter(e.achievement_type for e in extractions if e.achievement_type)
        ),
        "snapshot_at": snapshot_at.isoformat(),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_stats.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/stats.py backend/tests/test_stats.py
git commit -m "feat: 통계 집계 — 코드 기반, 검색/분석 모집단 분리"
```

---

### Task 9: reduce · rollup (동기 호출 + 3단 분기)

**Files:**
- Create: `backend/app/clients/gemini_sync.py`, `backend/app/services/reducer.py`
- Modify: `backend/app/prompts.py` (reduce/rollup 프롬프트 추가)
- Test: `backend/tests/test_reducer.py`

**Interfaces:**
- Consumes: `app.models.paper.PaperExtraction`, `app.config.settings`
- Produces:
  - `gemini_sync.generate(system: str, user: str, *, thinking: str) -> str`
  - `reducer.format_extractions(extractions: list[PaperExtraction], papers_by_key: dict[str, Paper]) -> str`
  - `reducer.group_for_reduce(extractions: list[PaperExtraction]) -> dict[str, list[PaperExtraction]]` — 임계값 초과 시 achievement_type별 분할, 아니면 `{"전체": [...]}`
  - `async def reduce_subfield(db, analysis, extractions, papers_by_key) -> str`
  - `async def rollup_field(field_name: str, subfield_reports: list[tuple[str, str]]) -> str`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_reducer.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_reducer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.reducer'`

- [ ] **Step 3: prompts.py에 reduce/rollup 추가**

기존 `prompts.py` 끝에 append:

```python
REDUCE_INSTRUCTION = """당신은 국가전략기술 성과를 정리하는 과학기술 분석가입니다.
아래는 특정 세부기술 분야의 한국 논문들에서 추출한 기술적 성과 목록입니다.
이를 종합해 마크다운 보고서를 작성하세요.

구성:
## 주요 기술적 성과
연구 주제별로 묶어 서술합니다. 각 성과마다 근거 논문 제목을 괄호로 인용하세요.
기술적 내용 중심으로 쓰고, 정책적·산업적 함의는 쓰지 마세요.

## 주제 클러스터
표로 정리합니다. 열: 주제 | 논문 수 | 대표 성과 | 주요 접근법

규칙:
- 제공된 성과 목록에 없는 내용을 만들어내지 마세요.
- 수치는 목록에 있는 것만 인용하세요.
- 통계 수치(논문 수, 인용수 등)는 별도로 제공되므로 여기서 집계하지 마세요.
- 한국어로 작성하세요."""

ROLLUP_INSTRUCTION = """당신은 국가전략기술 성과를 정리하는 과학기술 분석가입니다.
아래는 한 전략기술 대분류에 속한 세부기술별 보고서입니다.
이를 종합해 대분류 수준의 마크다운 보고서를 작성하세요.

구성:
## 분야 종합
세부기술 전반에서 공통적으로 확인되는 기술적 진전을 3~5문단으로 서술합니다.

## 세부기술별 요약
표로 정리합니다. 열: 세부기술 | 핵심 성과 | 주요 접근법

규칙:
- 제공된 세부기술 보고서에 없는 내용을 만들어내지 마세요.
- 기술적 내용 중심으로 쓰고, 정책 제언은 쓰지 마세요.
- 한국어로 작성하세요."""
```

- [ ] **Step 4: gemini_sync.py 구현**

```python
import asyncio
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=settings.gemini_api_key)
# 동기 전용 SDK를 async 코드에서 부르기 위한 명시적 스레드풀.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini-sync")


class _RequestBucket:
    """RPM 토큰버킷. Semaphore는 동시성 제한이지 rate 제한이 아니므로 시간 기반으로 센다."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._stamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._stamps = [t for t in self._stamps if now - t < 60]
                if len(self._stamps) < self.per_minute:
                    self._stamps.append(now)
                    return
                await asyncio.sleep(60 - (now - self._stamps[0]) + 0.1)


_bucket = _RequestBucket(settings.sync_rpm)


def _is_rate_limit(e: Exception) -> bool:
    return (
        type(e).__name__ in ("ResourceExhausted", "TooManyRequests", "RateLimitError")
        or getattr(e, "status_code", None) == 429
        or getattr(e, "code", None) == 429
    )


async def generate(system: str, user: str, *, thinking: str, max_retries: int = 4) -> str:
    """단일 동기 생성 호출. RPM 버킷 통과 후 발사하고, 429는 지수 백오프."""
    config = types.GenerateContentConfig(
        system_instruction=system,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )

    def _call():
        return _client.models.generate_content(
            model=settings.gemini_model, contents=user, config=config
        )

    for attempt in range(max_retries + 1):
        await _bucket.acquire()
        try:
            response = await asyncio.get_running_loop().run_in_executor(_executor, _call)
            return response.text or ""
        except Exception as e:
            if _is_rate_limit(e) and attempt < max_retries:
                delay = 2 ** attempt + random.uniform(0, 1)
                logger.warning("Gemini 429 (%d/%d), %.1fs 후 재시도", attempt + 1, max_retries, delay)
                await asyncio.sleep(delay)
                continue
            raise
    raise RuntimeError("Gemini 재시도 소진")
```

- [ ] **Step 5: reducer.py 구현**

```python
import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from app.clients import gemini_sync
from app.config import settings
from app.models.analysis import Analysis
from app.models.paper import Paper, PaperExtraction
from app.prompts import REDUCE_INSTRUCTION, ROLLUP_INSTRUCTION

logger = logging.getLogger(__name__)


def format_extractions(
    extractions: list[PaperExtraction], papers_by_key: dict[str, Paper]
) -> str:
    """추출 결과를 reduce 입력용 텍스트로. 논문당 한 줄이라 수백 건도 컨텍스트에 들어간다."""
    lines: list[str] = []
    for e in extractions:
        paper = papers_by_key.get(e.paper_key)
        if paper is None:
            continue
        metrics = ", ".join(
            f"{m.get('name')} {m.get('value')}{m.get('unit', '')}" for m in (e.metrics_json or [])
        )
        line = f"- [{paper.year or '연도미상'}] {paper.title} | {e.achievement_type or '기타'} | {e.tech_summary}"
        if metrics:
            line += f" | 수치: {metrics}"
        lines.append(line)
    return "\n".join(lines)


def group_for_reduce(extractions: list[PaperExtraction]) -> dict[str, list[PaperExtraction]]:
    """임계값 이하면 한 번에 합성하고, 넘으면 성과유형별로 나눠 3단 reduce로 간다."""
    if len(extractions) <= settings.reduce_group_threshold:
        return {"전체": extractions}

    by_type: dict[str, list[PaperExtraction]] = defaultdict(list)
    for e in extractions:
        by_type[e.achievement_type or "기타"].append(e)

    # 한 성과유형에 전부 몰리면 유형 분할만으로는 임계값 아래로 내려가지 않는다.
    # 그런 그룹은 임계값 크기로 다시 쪼갠다.
    size = settings.reduce_group_threshold
    groups: dict[str, list[PaperExtraction]] = {}
    for name, items in by_type.items():
        if len(items) <= size:
            groups[name] = items
            continue
        for i in range(0, len(items), size):
            groups[f"{name} ({i // size + 1})"] = items[i:i + size]

    logger.info("[reduce] %d건 → %d개 그룹으로 분할", len(extractions), len(groups))
    return groups


async def reduce_subfield(
    db: Session,
    analysis: Analysis,
    extractions: list[PaperExtraction],
    papers_by_key: dict[str, Paper],
) -> str:
    """세부기술 보고서 생성. 추출 결과가 0건이면 LLM을 호출하지 않는다 —
    빈 입력으로 부르면 모델이 성과를 통째로 지어낸다."""
    if not extractions:
        return "분석 대상 논문이 없어 성과를 정리할 수 없습니다."

    groups = group_for_reduce(extractions)
    if len(groups) == 1:
        body = format_extractions(next(iter(groups.values())), papers_by_key)
        return await gemini_sync.generate(
            REDUCE_INSTRUCTION, body, thinking=settings.thinking_reduce
        )

    partials: list[str] = []
    for name, items in groups.items():
        body = format_extractions(items, papers_by_key)
        if not body:
            continue
        partial = await gemini_sync.generate(
            REDUCE_INSTRUCTION, f"[성과유형: {name}]\n{body}", thinking=settings.thinking_reduce
        )
        partials.append(f"### {name}\n{partial}")

    return await gemini_sync.generate(
        REDUCE_INSTRUCTION,
        "아래는 성과유형별 중간 정리 결과입니다. 이를 하나의 보고서로 통합하세요.\n\n"
        + "\n\n".join(partials),
        thinking=settings.thinking_reduce,
    )


async def rollup_field(field_name: str, subfield_reports: list[tuple[str, str]]) -> str:
    """대분류 보고서 = 하위 세부기술 보고서 합성 1콜."""
    if not subfield_reports:
        return "분석된 세부기술이 없습니다."

    body = "\n\n".join(f"## {name}\n{report}" for name, report in subfield_reports)
    return await gemini_sync.generate(
        ROLLUP_INSTRUCTION, f"[대분류: {field_name}]\n\n{body}", thinking=settings.thinking_reduce
    )
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_reducer.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7: 커밋**

```bash
git add backend/app/clients/gemini_sync.py backend/app/services/reducer.py \
  backend/app/prompts.py backend/tests/test_reducer.py
git commit -m "feat: reduce/rollup — 임계값 초과 시 3단 분기, 0건이면 LLM 미호출"
```

---

### Task 10: 잡 상태머신 · 백그라운드 루프

**Files:**
- Create: `backend/app/services/runner.py`
- Modify: `backend/app/main.py` (startup에서 루프 기동)
- Test: `backend/tests/test_runner.py`

**Interfaces:**
- Consumes: 앞선 모든 서비스
- Produces:
  - `runner.STEP_LABELS: dict[str, str]` — 상태 → 한국어 표시명
  - `runner.enqueue(db, subfield, year_from, year_to, *, force: bool) -> list[Analysis]`
  - `async def runner.advance(db, analysis) -> None` — 상태 1단계 전진
  - `async def runner.loop() -> None` — 미완 잡을 스캔해 전진시키는 무한 루프

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_runner.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.services import runner


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    f = Field(name="양자", slug="quantum", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="양자컴퓨팅", query="quantum computing")
    db.add(sf)
    db.commit()
    return db, sf


def test_enqueue_creates_one_analysis_per_year(ctx):
    db, sf = ctx
    created = runner.enqueue(db, sf, 2024, 2026, force=False)
    assert {a.year for a in created} == {2024, 2025, 2026}
    assert all(a.status == "pending" for a in created)


def test_enqueue_skips_done_analysis_with_matching_hash(ctx):
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=False)
    assert again == []
    assert db.query(Analysis).count() == 1


def test_enqueue_reruns_when_query_changed(ctx):
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    db.commit()

    sf.query = "quantum error correction"   # 검색식 변경 → query_hash 불일치
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=False)
    assert len(again) == 1
    assert again[0].status == "pending"
    assert db.query(Analysis).count() == 1  # 같은 행을 재사용


def test_enqueue_force_reruns_even_when_fresh(ctx):
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=True)
    assert len(again) == 1


def test_step_labels_are_korean():
    assert runner.STEP_LABELS["searching"] == "논문 검색 중"
    assert runner.STEP_LABELS["extracting"] == "성과 추출 중"
    assert runner.STEP_LABELS["reducing"] == "보고서 작성 중"
    assert runner.STEP_LABELS["done"] == "완료"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.runner'`

- [ ] **Step 3: runner.py 구현**

```python
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.clients import gemini_batch
from app.clients._http import RateLimited
from app.config import settings
from app.database import SessionLocal
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import Subfield
from app.models.paper import Paper, PaperExtraction
from app.services import mapper, reducer, search, stats
from app.services.budget import BudgetExceeded

logger = logging.getLogger(__name__)

STEP_LABELS = {
    "pending": "대기 중",
    "searching": "논문 검색 중",
    "extracting": "성과 추출 중",
    "reducing": "보고서 작성 중",
    "done": "완료",
    "failed": "실패",
    "paused": "일시중지 (예산 소진)",
}

ACTIVE_STATES = ("pending", "searching", "extracting", "reducing")


class AnalysisTooLarge(RuntimeError):
    pass


def enqueue(
    db: Session, subfield: Subfield, year_from: int, year_to: int, *, force: bool
) -> list[Analysis]:
    """연도별 Analysis를 만들거나 되살린다.

    이미 done이고 query_hash가 같으면 건너뛴다(재호출 방지). 검색식이 바뀌었으면
    같은 행을 pending으로 되돌려 증분 재실행한다 — 프리즈는 두지 않는다.
    """
    queued: list[Analysis] = []
    for year in range(year_from, year_to + 1):
        current_hash = search.query_hash(subfield, year, year)
        row = db.query(Analysis).filter(
            Analysis.subfield_id == subfield.id, Analysis.year == year
        ).first()

        if row is None:
            row = Analysis(subfield_id=subfield.id, year=year, status="pending",
                           query_hash=current_hash)
            db.add(row)
            queued.append(row)
        elif force or row.status in ("failed", "paused") or row.query_hash != current_hash:
            row.status = "pending"
            row.query_hash = current_hash
            row.error = None
            row.batch_job_id = None
            queued.append(row)
        elif row.status in ACTIVE_STATES:
            queued.append(row)

    db.commit()
    return queued


def is_stale(db: Session, analysis: Analysis, subfield: Subfield) -> bool:
    """검색식이 바뀌어 갱신이 필요한 상태인지."""
    return analysis.query_hash != search.query_hash(subfield, analysis.year, analysis.year)


async def advance(db: Session, analysis: Analysis) -> None:
    """상태를 한 단계 전진시킨다. 각 단계는 독립적으로 재진입 가능해야 한다 —
    컨테이너가 언제 재시작되어도 DB 상태만 보고 이어갈 수 있어야 하기 때문."""
    subfield = db.query(Subfield).get(analysis.subfield_id)
    try:
        if analysis.status == "pending":
            analysis.status = "searching"
            db.commit()
        elif analysis.status == "searching":
            await _do_search(db, analysis, subfield)
        elif analysis.status == "extracting":
            await _do_extract(db, analysis)
        elif analysis.status == "reducing":
            await _do_reduce(db, analysis)
    except BudgetExceeded as e:
        logger.warning("[잡 %d] 예산 초과로 일시중지: %s", analysis.id, e)
        analysis.status = "paused"
        analysis.error = str(e)
        db.commit()
    except RateLimited as e:
        if e.permanent:
            analysis.status = "paused"
            analysis.error = "OpenAlex 일일 크레딧 소진 — 내일 자동 재개됩니다."
        else:
            analysis.error = str(e)
        db.commit()
    except Exception as e:
        logger.exception("[잡 %d] 실패", analysis.id)
        analysis.status = "failed"
        analysis.error = str(e)
        db.commit()


async def _do_search(db: Session, analysis: Analysis, subfield: Subfield) -> None:
    async with httpx.AsyncClient() as client:
        papers = await search.collect(db, subfield, analysis.year, analysis.year, client=client)

    if len(papers) > settings.max_papers_per_analysis:
        raise AnalysisTooLarge(
            f"검색 결과 {len(papers)}건이 상한 {settings.max_papers_per_analysis}건을 넘습니다. "
            f"검색식을 좁히거나 세부기술을 분할하세요."
        )

    rows = search.upsert_papers(db, papers)
    existing = {
        r.paper_id for r in db.query(AnalysisPaper.paper_id).filter(
            AnalysisPaper.analysis_id == analysis.id
        )
    }
    for row in rows:
        if row.id not in existing:
            db.add(AnalysisPaper(analysis_id=analysis.id, paper_id=row.id))

    analysis.searched_count = len(rows)
    analysis.snapshot_at = datetime.now(timezone.utc)
    analysis.status = "extracting"
    db.commit()


def _analysis_papers(db: Session, analysis: Analysis) -> list[Paper]:
    return (
        db.query(Paper)
        .join(AnalysisPaper, AnalysisPaper.paper_id == Paper.id)
        .filter(AnalysisPaper.analysis_id == analysis.id)
        .all()
    )


async def _do_extract(db: Session, analysis: Analysis) -> None:
    """batch 제출 전이면 제출하고, 제출됐으면 폴링한다. 24h까지 걸리므로
    잡 이름을 DB에 남겨 재시작 후에도 같은 batch를 이어서 본다."""
    if analysis.batch_job_id:
        state, results = gemini_batch.poll(analysis.batch_job_id)
        if state == "running":
            return
        if state == "failed":
            analysis.status = "failed"
            analysis.error = "Gemini batch 작업 실패"
            db.commit()
            return
        mapper.save_results(db, analysis, results or [])
        analysis.batch_job_id = None
        db.commit()
        # 청크가 여러 개면 아직 제출 못한 논문이 남아 있다. reducing으로 넘기지 않고
        # extracting에 머물러 다음 루프에서 다음 청크를 제출한다.
        if mapper.pending_papers(db, analysis, _analysis_papers(db, analysis)):
            return
        analysis.status = "reducing"
        db.commit()
        return

    papers = _analysis_papers(db, analysis)
    pending = mapper.pending_papers(db, analysis, papers)
    if not pending:
        analysis.status = "reducing"
        db.commit()
        return

    requests = mapper.build_requests(pending)
    batches = mapper.chunks(requests)
    # ponytail: 청크가 여러 개면 첫 청크만 제출하고 나머지는 다음 루프에서 이어간다.
    # 동시 batch 잡 수 상한을 넘기지 않는 가장 단순한 방법.
    analysis.batch_job_id = gemini_batch.submit(batches[0], thinking=settings.thinking_map)
    db.commit()


async def _do_reduce(db: Session, analysis: Analysis) -> None:
    papers = _analysis_papers(db, analysis)
    papers_by_key = {p.paper_key: p for p in papers}
    extractions = db.query(PaperExtraction).filter(
        PaperExtraction.paper_key.in_(list(papers_by_key)),
        PaperExtraction.subfield_id == analysis.subfield_id,
        PaperExtraction.model_ver == mapper.model_ver(),
    ).all()

    analysis.stats_json = stats.compute(
        papers, extractions, snapshot_at=analysis.snapshot_at or datetime.now(timezone.utc)
    )
    analysis.analyzed_count = len(extractions)
    analysis.report_md = await reducer.reduce_subfield(db, analysis, extractions, papers_by_key)
    analysis.status = "done"
    db.commit()


async def loop() -> None:
    """미완 잡을 주기적으로 스캔해 전진시킨다. 상태가 전부 DB에 있으므로
    프로세스가 죽었다 살아나도 그대로 이어진다."""
    logger.info("잡 루프 시작 (%d초 간격)", settings.loop_interval_seconds)
    while True:
        try:
            db = SessionLocal()
            try:
                active = db.query(Analysis).filter(Analysis.status.in_(ACTIVE_STATES)).all()
                for analysis in active:
                    await advance(db, analysis)
            finally:
                db.close()
        except Exception:
            logger.exception("잡 루프 순회 실패 — 다음 주기에 재시도")
        await asyncio.sleep(settings.loop_interval_seconds)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_runner.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: main.py에 루프 기동 추가**

```python
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import app.models  # noqa: F401  — 모든 모델 등록(FK 해석에 필요)
from app.services.runner import loop

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(loop())
    yield
    task.cancel()


app = FastAPI(title="전략기술 논문성과 분석", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 6: 컨테이너 기동 확인**

Run: `docker compose up -d --build api && sleep 5 && docker compose logs api | tail -20`
Expected: 로그에 `잡 루프 시작 (30초 간격)` 이 보이고 크래시 없음

- [ ] **Step 7: 커밋**

```bash
git add backend/app/services/runner.py backend/app/main.py backend/tests/test_runner.py
git commit -m "feat: 잡 상태머신 · 재시작 안전 백그라운드 루프"
```

---

### Task 11: API 라우터 (공개 + 관리자)

**Files:**
- Create: `backend/app/routers/__init__.py`, `backend/app/routers/public.py`, `backend/app/routers/admin.py`, `backend/app/deps.py`
- Modify: `backend/app/main.py` (라우터 등록, CORS)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Produces:
  - `GET /api/fields` → `[{id, name, slug, subfields: [{id, name, active}]}]`
  - `GET /api/fields/{field_id}/years` → `[{year, subfield_count, done_count}]`
  - `GET /api/analyses/{analysis_id}` → `{id, subfield_name, field_name, year, status, status_label, report_md, stats, searched_count, analyzed_count, snapshot_at, sampled}`
  - `GET /api/subfields/{subfield_id}/analyses/{year}` → 위와 동일 형태
  - `POST /api/admin/auth` → `{ok: true}` (키 검증만)
  - `GET|POST|PUT|DELETE /api/admin/subfields`
  - `POST /api/admin/preview` → `{openalex_count, kci_count, samples, estimated_cost_usd, budget_spent, budget_limit}`
  - `POST /api/admin/run` → `{queued: [analysis_id], blocked: [{year, reason}]}`
  - `GET /api/admin/dashboard` → 세부기술 × 연도 격자
  - `POST /api/admin/analyses/{id}/retry`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_api.py`:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.field import Field, Subfield


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()
    f = Field(name="양자", slug="quantum", order_no=1)
    db.add(f)
    db.flush()
    db.add(Subfield(field_id=f.id, name="양자컴퓨팅", query="quantum computing"))
    db.commit()

    app.dependency_overrides[get_db] = lambda: TestingSession()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_public_fields_lists_subfields(client):
    r = client.get("/api/fields")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "양자"
    assert r.json()[0]["subfields"][0]["name"] == "양자컴퓨팅"


def test_admin_requires_key(client):
    assert client.get("/api/admin/dashboard").status_code == 401
    assert client.get("/api/admin/dashboard",
                      headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_admin_accepts_correct_key(client):
    r = client.get("/api/admin/dashboard", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200


def test_admin_can_create_subfield(client):
    r = client.post(
        "/api/admin/subfields",
        headers={"X-Admin-Key": settings.admin_key},
        json={"field_id": 1, "name": "양자센서", "query": "quantum sensing"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "양자센서"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && ADMIN_KEY=testkey python -m pytest tests/test_api.py -v`
Expected: FAIL — `404 Not Found` (라우터 미등록)

- [ ] **Step 3: deps.py 구현**

```python
from fastapi import Header, HTTPException

from app.config import settings


def require_admin(x_admin_key: str = Header(default="")) -> None:
    """관리자 API 게이트. 계정 체계 없이 .env의 단일 키만 검증한다."""
    if not settings.admin_key or x_admin_key != settings.admin_key:
        raise HTTPException(status_code=401, detail="관리자 키가 올바르지 않습니다.")
```

- [ ] **Step 4: public.py 구현**

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.services.runner import STEP_LABELS

router = APIRouter(prefix="/api", tags=["public"])


def _serialize(db: Session, analysis: Analysis) -> dict:
    subfield = db.query(Subfield).get(analysis.subfield_id)
    field = db.query(Field).get(subfield.field_id)
    return {
        "id": analysis.id,
        "field_name": field.name,
        "subfield_name": subfield.name,
        "year": analysis.year,
        "status": analysis.status,
        "status_label": STEP_LABELS.get(analysis.status, analysis.status),
        "report_md": analysis.report_md,
        "stats": analysis.stats_json,
        "searched_count": analysis.searched_count,
        "analyzed_count": analysis.analyzed_count,
        "sampled": analysis.sampled,
        "snapshot_at": analysis.snapshot_at.isoformat() if analysis.snapshot_at else None,
        "error": analysis.error,
    }


@router.get("/fields")
def list_fields(db: Session = Depends(get_db)):
    fields = db.query(Field).order_by(Field.order_no).all()
    return [
        {
            "id": f.id,
            "name": f.name,
            "slug": f.slug,
            "subfields": [
                {"id": s.id, "name": s.name, "active": s.active}
                for s in db.query(Subfield).filter(Subfield.field_id == f.id).all()
            ],
        }
        for f in fields
    ]


@router.get("/fields/{field_id}/years")
def field_years(field_id: int, db: Session = Depends(get_db)):
    """이 분야에서 보고서가 존재하는 연도 목록."""
    subfield_ids = [s.id for s in db.query(Subfield).filter(Subfield.field_id == field_id)]
    rows = db.query(Analysis).filter(Analysis.subfield_id.in_(subfield_ids)).all()

    by_year: dict[int, dict] = {}
    for row in rows:
        entry = by_year.setdefault(row.year, {"year": row.year, "subfield_count": 0, "done_count": 0})
        entry["subfield_count"] += 1
        if row.status == "done":
            entry["done_count"] += 1
    return sorted(by_year.values(), key=lambda e: e["year"], reverse=True)


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).get(analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="분석 결과를 찾을 수 없습니다.")
    return _serialize(db, analysis)


@router.get("/subfields/{subfield_id}/analyses/{year}")
def get_by_subfield_year(subfield_id: int, year: int, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).filter(
        Analysis.subfield_id == subfield_id, Analysis.year == year
    ).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="분석 결과를 찾을 수 없습니다.")
    return _serialize(db, analysis)
```

- [ ] **Step 5: admin.py 구현**

```python
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.clients import kci, openalex
from app.config import settings
from app.database import get_db
from app.deps import require_admin
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.services import budget, runner, search

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class SubfieldIn(BaseModel):
    field_id: int
    name: str
    query: str
    query_kci: str | None = None
    active: bool = True


class PreviewIn(BaseModel):
    subfield_id: int
    year_from: int
    year_to: int


class RunIn(BaseModel):
    subfield_ids: list[int]
    year_from: int
    year_to: int
    force: bool = False


@router.post("/auth")
def auth():
    """키 검증 전용 — 프론트가 입력값을 확인할 때 쓴다."""
    return {"ok": True}


@router.get("/subfields")
def list_subfields(db: Session = Depends(get_db)):
    return [
        {"id": s.id, "field_id": s.field_id, "name": s.name, "query": s.query,
         "query_kci": s.query_kci, "active": s.active}
        for s in db.query(Subfield).order_by(Subfield.field_id, Subfield.name).all()
    ]


@router.post("/subfields")
def create_subfield(payload: SubfieldIn, db: Session = Depends(get_db)):
    if not db.query(Field).get(payload.field_id):
        raise HTTPException(status_code=404, detail="분야를 찾을 수 없습니다.")
    row = Subfield(**payload.model_dump())
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.put("/subfields/{subfield_id}")
def update_subfield(subfield_id: int, payload: SubfieldIn, db: Session = Depends(get_db)):
    row = db.query(Subfield).get(subfield_id)
    if not row:
        raise HTTPException(status_code=404, detail="세부기술을 찾을 수 없습니다.")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.delete("/subfields/{subfield_id}")
def delete_subfield(subfield_id: int, db: Session = Depends(get_db)):
    row = db.query(Subfield).get(subfield_id)
    if not row:
        raise HTTPException(status_code=404, detail="세부기술을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/preview")
async def preview(payload: PreviewIn, db: Session = Depends(get_db)):
    """검색만 실행해 건수·샘플·예상 비용을 보여준다. LLM은 호출하지 않는다."""
    subfield = db.query(Subfield).get(payload.subfield_id)
    if not subfield:
        raise HTTPException(status_code=404, detail="세부기술을 찾을 수 없습니다.")

    async with httpx.AsyncClient() as client:
        count, cost = await openalex.count_only(
            subfield.query, payload.year_from, payload.year_to, client=client
        )
        sample = await openalex.search(
            subfield.query, payload.year_from, payload.year_to, client=client, limit=20
        )
        kci_papers = await kci.search(
            subfield.kci_query(), payload.year_from, payload.year_to, client=client, limit=20
        )

    budget.record_usage(db, cost + sample.cost_usd, sample.remaining)
    pages = max(1, -(-min(count, settings.max_papers_per_analysis) // settings.openalex_per_page))

    return {
        "openalex_count": count,
        "kci_count": len(kci_papers),
        "samples": [
            {"title": p["title"], "year": p["year"], "journal": p["journal"],
             "has_abstract": bool(p["abstract"])}
            for p in sample.papers[:20]
        ],
        "estimated_pages": pages,
        "estimated_cost_usd": round(pages * settings.openalex_search_cost_usd, 4),
        "budget_spent": round(budget.spent_today(db), 4),
        "budget_limit": settings.openalex_daily_budget_usd,
        "over_limit": count > settings.max_papers_per_analysis,
        "max_papers": settings.max_papers_per_analysis,
    }


@router.post("/run")
def run(payload: RunIn, db: Session = Depends(get_db)):
    queued, blocked = [], []
    for subfield_id in payload.subfield_ids:
        subfield = db.query(Subfield).get(subfield_id)
        if not subfield:
            blocked.append({"subfield_id": subfield_id, "reason": "세부기술 없음"})
            continue
        for analysis in runner.enqueue(
            db, subfield, payload.year_from, payload.year_to, force=payload.force
        ):
            queued.append(analysis.id)
    return {"queued": queued, "blocked": blocked}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    """세부기술 × 연도 격자. 검색식이 바뀐 항목은 stale=True로 표시된다."""
    rows = []
    for subfield in db.query(Subfield).order_by(Subfield.field_id, Subfield.name).all():
        analyses = db.query(Analysis).filter(Analysis.subfield_id == subfield.id).all()
        rows.append({
            "subfield_id": subfield.id,
            "subfield_name": subfield.name,
            "field_id": subfield.field_id,
            "years": [
                {
                    "analysis_id": a.id,
                    "year": a.year,
                    "status": a.status,
                    "status_label": runner.STEP_LABELS.get(a.status, a.status),
                    "searched_count": a.searched_count,
                    "analyzed_count": a.analyzed_count,
                    "snapshot_at": a.snapshot_at.isoformat() if a.snapshot_at else None,
                    "stale": runner.is_stale(db, a, subfield),
                    "error": a.error,
                }
                for a in sorted(analyses, key=lambda x: x.year, reverse=True)
            ],
        })
    return {
        "rows": rows,
        "budget_spent": round(budget.spent_today(db), 4),
        "budget_limit": settings.openalex_daily_budget_usd,
        "default_year_range": settings.default_year_range,
    }


@router.post("/analyses/{analysis_id}/retry")
def retry(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).get(analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="분석을 찾을 수 없습니다.")
    analysis.status = "pending"
    analysis.error = None
    analysis.batch_job_id = None
    db.commit()
    return {"ok": True, "id": analysis.id}
```

- [ ] **Step 6: main.py에 라우터 등록**

`main.py`의 `app = FastAPI(...)` 아래에 추가:

```python
from fastapi.middleware.cors import CORSMiddleware

from app.routers import admin, public

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(public.router)
app.include_router(admin.router)
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `cd backend && ADMIN_KEY=testkey python -m pytest tests/test_api.py -v`
Expected: PASS (4 passed)

- [ ] **Step 8: 전체 테스트 확인**

Run: `cd backend && ADMIN_KEY=testkey python -m pytest -v`
Expected: 전체 PASS

- [ ] **Step 9: 커밋**

```bash
git add backend/app/routers backend/app/deps.py backend/app/main.py backend/tests/test_api.py
git commit -m "feat: 공개 · 관리자 API 라우터"
```

---

### Task 12: 프론트엔드 공개 화면 + PDF 인쇄

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tailwind.config.js`, `frontend/postcss.config.js`, `frontend/index.html`, `frontend/nginx.conf`, `frontend/Dockerfile`, `frontend/src/main.tsx`, `frontend/src/index.css`, `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/pages/FieldList.tsx`, `frontend/src/pages/FieldDetail.tsx`, `frontend/src/pages/Report.tsx`, `frontend/src/components/StatsPanel.tsx`

**Interfaces:**
- Consumes: Task 11의 공개 API
- Produces: 라우트 `/` (분야 목록), `/fields/:fieldId` (연도 선택), `/analyses/:analysisId` (보고서)

**디자인 지침:** 이 태스크를 시작할 때 `frontend-design` 스킬을 먼저 호출한다. `../trade-ews` 프론트엔드의 색·타이포·여백을 참고해 패밀리룩을 맞추되 동일할 필요는 없다.

- [ ] **Step 1: Vite 프로젝트 생성 및 의존성 설치**

```bash
cd frontend && npm create vite@latest . -- --template react-ts
npm install
npm install react-router-dom recharts react-markdown remark-gfm
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

- [ ] **Step 2: api.ts 작성**

```ts
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error((await res.json()).detail ?? "요청에 실패했습니다.");
  return res.json();
}

export async function post<T>(path: string, body: unknown, adminKey?: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(adminKey ? { "X-Admin-Key": adminKey } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json()).detail ?? "요청에 실패했습니다.");
  return res.json();
}

export interface Analysis {
  id: number;
  field_name: string;
  subfield_name: string;
  year: number;
  status: string;
  status_label: string;
  report_md: string | null;
  stats: Record<string, any>;
  searched_count: number;
  analyzed_count: number;
  sampled: boolean;
  snapshot_at: string | null;
  error: string | null;
}
```

- [ ] **Step 3: App.tsx 라우터 구성**

```tsx
import { BrowserRouter, Route, Routes } from "react-router-dom";
import FieldList from "./pages/FieldList";
import FieldDetail from "./pages/FieldDetail";
import Report from "./pages/Report";
import Admin from "./pages/Admin";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<FieldList />} />
        <Route path="/fields/:fieldId" element={<FieldDetail />} />
        <Route path="/analyses/:analysisId" element={<Report />} />
        <Route path="/admin" element={<Admin />} />
      </Routes>
    </BrowserRouter>
  );
}
```

- [ ] **Step 4: Report.tsx 작성 (모집단 표기 + 인쇄)**

```tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, type Analysis } from "../api";
import StatsPanel from "../components/StatsPanel";

export default function Report() {
  const { analysisId } = useParams();
  const [data, setData] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Analysis>(`/analyses/${analysisId}`).then(setData).catch((e) => setError(e.message));
  }, [analysisId]);

  if (error) return <p className="p-8 text-red-600">{error}</p>;
  if (!data) return <p className="p-8">불러오는 중…</p>;

  const excluded = data.searched_count - data.analyzed_count;

  return (
    <article className="mx-auto max-w-4xl px-6 py-12">
      <header className="mb-8">
        <p className="text-sm text-neutral-500">{data.field_name}</p>
        <h1 className="text-3xl font-semibold">
          {data.subfield_name} <span className="text-neutral-400">{data.year}</span>
        </h1>
        {/* 검색 모집단과 분석 모집단이 다르다는 점을 감추지 않는다 */}
        <p className="mt-2 text-sm text-neutral-600">
          검색 {data.searched_count.toLocaleString()}건 / 분석 대상{" "}
          {data.analyzed_count.toLocaleString()}건
          {excluded > 0 && ` (abstract 미보유 등 ${excluded.toLocaleString()}건 제외)`}
          {data.sampled && " · 성과 서술은 표본 기준, 통계는 전수"}
        </p>
        {data.snapshot_at && (
          <p className="text-xs text-neutral-400">
            수집 시점 {new Date(data.snapshot_at).toLocaleString("ko-KR")} 기준 (인용수 포함)
          </p>
        )}
        <button
          onClick={() => window.print()}
          className="mt-4 rounded border px-4 py-2 text-sm print:hidden"
        >
          PDF로 저장
        </button>
      </header>

      <StatsPanel stats={data.stats} />

      <div className="prose prose-neutral mt-10 max-w-none">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.report_md ?? ""}</ReactMarkdown>
      </div>
    </article>
  );
}
```

- [ ] **Step 5: index.css에 인쇄 스타일 추가**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@media print {
  @page {
    margin: 15mm;
  }
  body {
    background: #fff;
  }
  /* 표와 차트가 페이지 경계에서 잘리지 않게 한다 */
  table,
  figure,
  .recharts-wrapper {
    break-inside: avoid;
  }
  h2 {
    break-after: avoid;
  }
}
```

- [ ] **Step 6: StatsPanel.tsx 작성**

```tsx
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function StatsPanel({ stats }: { stats: Record<string, any> }) {
  if (!stats?.searched_count) return null;
  const byYear = Object.entries(stats.by_year ?? {}).map(([year, count]) => ({ year, count }));

  return (
    <section className="space-y-8">
      <h2 className="text-xl font-semibold">기본 통계</h2>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Tile label="검색 논문" value={stats.searched_count} />
        <Tile label="분석 대상" value={stats.analyzed_count} />
        <Tile label="국제공동연구" value={`${(stats.intl_collab_ratio * 100).toFixed(1)}%`} />
        <Tile label="인용수 중앙값" value={stats.citations?.median ?? 0} />
      </div>

      {byYear.length > 1 && (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={byYear}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="year" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="count" />
          </BarChart>
        </ResponsiveContainer>
      )}

      <RankTable title="상위 기관" rows={stats.top_institutions ?? []} />
      <RankTable title="상위 저널" rows={stats.top_journals ?? []} />
    </section>
  );
}

function Tile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border p-4">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value.toLocaleString?.() ?? value}</p>
    </div>
  );
}

function RankTable({ title, rows }: { title: string; rows: [string, number][] }) {
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto">
      <h3 className="mb-2 font-medium">{title}</h3>
      <table className="w-full text-sm">
        <tbody>
          {rows.slice(0, 10).map(([name, count]) => (
            <tr key={name} className="border-b">
              <td className="py-1.5">{name}</td>
              <td className="py-1.5 text-right tabular-nums">{count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 7: main.tsx · FieldList.tsx · FieldDetail.tsx 작성**

`frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

`frontend/src/pages/FieldList.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "../api";

interface Field {
  id: number;
  name: string;
  slug: string;
  subfields: { id: number; name: string; active: boolean }[];
}

export default function FieldList() {
  const [fields, setFields] = useState<Field[]>([]);

  useEffect(() => {
    get<Field[]>("/fields").then(setFields);
  }, []);

  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <h1 className="text-3xl font-semibold">전략기술 논문성과</h1>
      <p className="mt-2 text-sm text-neutral-600">
        분야를 선택하면 연도별 분석 보고서를 볼 수 있습니다.
      </p>

      <ul className="mt-8 grid gap-3 sm:grid-cols-2">
        {fields.map((f) => (
          <li key={f.id}>
            <Link to={`/fields/${f.id}`} className="block rounded-lg border p-4 hover:bg-neutral-50">
              <p className="font-medium">{f.name}</p>
              <p className="mt-1 text-xs text-neutral-500">
                세부기술 {f.subfields.filter((s) => s.active).length}개
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
```

`frontend/src/pages/FieldDetail.tsx` — 대분류 rollup 보고서는 초판 범위 밖이므로
세부기술 × 연도 보고서 목록을 나열한다:

```tsx
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get } from "../api";

interface Field {
  id: number;
  name: string;
  subfields: { id: number; name: string; active: boolean }[];
}

interface YearRow {
  year: number;
  subfield_count: number;
  done_count: number;
}

export default function FieldDetail() {
  const { fieldId } = useParams();
  const [field, setField] = useState<Field | null>(null);
  const [years, setYears] = useState<YearRow[]>([]);
  const [year, setYear] = useState<number | null>(null);

  useEffect(() => {
    get<Field[]>("/fields").then((all) => setField(all.find((f) => f.id === Number(fieldId)) ?? null));
    get<YearRow[]>(`/fields/${fieldId}/years`).then((rows) => {
      setYears(rows);
      setYear(rows[0]?.year ?? null);
    });
  }, [fieldId]);

  if (!field) return <p className="p-8">불러오는 중…</p>;

  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <Link to="/" className="text-sm text-neutral-500">← 분야 목록</Link>
      <h1 className="mt-2 text-3xl font-semibold">{field.name}</h1>

      {years.length === 0 ? (
        <p className="mt-8 text-neutral-600">아직 분석된 결과가 없습니다.</p>
      ) : (
        <>
          <div className="mt-6 flex gap-2">
            {years.map((y) => (
              <button
                key={y.year}
                onClick={() => setYear(y.year)}
                className={`rounded border px-3 py-1.5 text-sm ${
                  y.year === year ? "bg-neutral-900 text-white" : ""
                }`}
              >
                {y.year} <span className="opacity-60">({y.done_count}/{y.subfield_count})</span>
              </button>
            ))}
          </div>

          <ul className="mt-6 divide-y border-t">
            {field.subfields.filter((s) => s.active).map((s) => (
              <li key={s.id} className="py-3">
                <Link
                  to={`/subfields/${s.id}/${year}`}
                  className="flex items-center justify-between hover:underline"
                >
                  <span>{s.name}</span>
                  <span className="text-sm text-neutral-400">{year} 보고서 →</span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  );
}
```

`App.tsx`에 세부기술+연도 경로를 추가한다 (Report가 두 형태의 URL을 모두 받는다):

```tsx
<Route path="/subfields/:subfieldId/:year" element={<Report />} />
```

`Report.tsx`의 데이터 로딩부를 두 경로 모두 처리하도록 바꾼다:

```tsx
const { analysisId, subfieldId, year } = useParams();

useEffect(() => {
  const path = analysisId
    ? `/analyses/${analysisId}`
    : `/subfields/${subfieldId}/analyses/${year}`;
  get<Analysis>(path).then(setData).catch((e) => setError(e.message));
}, [analysisId, subfieldId, year]);
```

- [ ] **Step 8: nginx.conf + Dockerfile 작성**

`frontend/nginx.conf`:

```nginx
server {
  listen 80;
  root /usr/share/nginx/html;
  index index.html;

  location /api/ {
    proxy_pass http://api:8000;
    proxy_set_header Host $host;
    proxy_read_timeout 300s;
  }

  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

`frontend/Dockerfile`:

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

- [ ] **Step 9: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: 에러 없이 `dist/` 생성

- [ ] **Step 10: 커밋**

```bash
git add frontend
git commit -m "feat: 공개 화면 — 분야/연도/보고서 · PDF 인쇄"
```

---

### Task 13: 관리자 화면

**Files:**
- Create: `frontend/src/pages/Admin.tsx`, `frontend/src/components/SubfieldEditor.tsx`, `frontend/src/components/RunDialog.tsx`, `frontend/src/useAdminKey.ts`

**Interfaces:**
- Consumes: Task 11의 `/api/admin/*`
- Produces: `/admin` 라우트 — 키 입력 게이트 → 대시보드 · 검색식 편집 · 미리보기 · 실행 확정 · 재시도

- [ ] **Step 1: useAdminKey.ts 작성**

```ts
import { useState } from "react";

const STORAGE_KEY = "admin-key";

export function useAdminKey() {
  const [key, setKey] = useState(() => sessionStorage.getItem(STORAGE_KEY) ?? "");

  const save = (value: string) => {
    sessionStorage.setItem(STORAGE_KEY, value);
    setKey(value);
  };
  const clear = () => {
    sessionStorage.removeItem(STORAGE_KEY);
    setKey("");
  };
  return { key, save, clear };
}
```

- [ ] **Step 2: Admin.tsx 작성 (키 게이트 + 대시보드)**

```tsx
import { useEffect, useState } from "react";
import { post } from "../api";
import { useAdminKey } from "../useAdminKey";
import SubfieldEditor from "../components/SubfieldEditor";
import RunDialog from "../components/RunDialog";

interface YearCell {
  analysis_id: number;
  year: number;
  status: string;
  status_label: string;
  searched_count: number;
  analyzed_count: number;
  snapshot_at: string | null;
  stale: boolean;
  error: string | null;
}

interface Row {
  subfield_id: number;
  subfield_name: string;
  field_id: number;
  years: YearCell[];
}

export default function Admin() {
  const { key, save, clear } = useAdminKey();
  const [input, setInput] = useState("");
  const [data, setData] = useState<{ rows: Row[]; budget_spent: number; budget_limit: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async (adminKey: string) => {
    const res = await fetch("/api/admin/dashboard", { headers: { "X-Admin-Key": adminKey } });
    if (!res.ok) throw new Error("관리자 키가 올바르지 않습니다.");
    setData(await res.json());
  };

  useEffect(() => {
    if (key) load(key).catch((e) => { setError(e.message); clear(); });
  }, [key]);

  if (!key) {
    return (
      <div className="mx-auto max-w-sm px-6 py-24">
        <h1 className="mb-4 text-xl font-semibold">관리자 인증</h1>
        <input
          type="password"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="admin key"
          className="w-full rounded border px-3 py-2"
        />
        <button
          onClick={async () => {
            try {
              await post("/admin/auth", {}, input);
              save(input);
              setError(null);
            } catch { setError("관리자 키가 올바르지 않습니다."); }
          }}
          className="mt-3 w-full rounded bg-neutral-900 py-2 text-white"
        >
          접속
        </button>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </div>
    );
  }

  if (!data) return <p className="p-8">불러오는 중…</p>;

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">관리자</h1>
        <p className="text-sm text-neutral-600">
          OpenAlex 오늘 사용 ${data.budget_spent.toFixed(4)} / ${data.budget_limit.toFixed(2)}
        </p>
      </header>

      <SubfieldEditor adminKey={key} onChanged={() => load(key)} />
      <RunDialog adminKey={key} rows={data.rows} onRan={() => load(key)} />

      <h2 className="mb-3 mt-10 text-lg font-medium">실행 상태</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-neutral-500">
              <th className="py-2">세부기술</th><th>연도</th><th>상태</th>
              <th>검색/분석</th><th>최종수집</th><th></th>
            </tr>
          </thead>
          <tbody>
            {data.rows.flatMap((row) =>
              row.years.map((cell) => (
                <tr key={cell.analysis_id} className="border-b">
                  <td className="py-2">{row.subfield_name}</td>
                  <td>{cell.year}</td>
                  <td>
                    {cell.status_label}
                    {cell.stale && <span className="ml-2 text-amber-600">갱신 필요</span>}
                    {cell.error && <p className="text-xs text-red-600">{cell.error}</p>}
                  </td>
                  <td className="tabular-nums">{cell.searched_count} / {cell.analyzed_count}</td>
                  <td className="text-xs text-neutral-500">
                    {cell.snapshot_at ? new Date(cell.snapshot_at).toLocaleDateString("ko-KR") : "—"}
                  </td>
                  <td>
                    {(cell.status === "failed" || cell.status === "paused") && (
                      <button
                        onClick={async () => {
                          await post(`/admin/analyses/${cell.analysis_id}/retry`, {}, key);
                          load(key);
                        }}
                        className="rounded border px-2 py-1 text-xs"
                      >
                        재실행
                      </button>
                    )}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: RunDialog.tsx 작성 (미리보기 → 비용 확정 → 실행)**

```tsx
import { useState } from "react";
import { post } from "../api";

interface Preview {
  openalex_count: number;
  kci_count: number;
  estimated_pages: number;
  estimated_cost_usd: number;
  budget_spent: number;
  budget_limit: number;
  over_limit: boolean;
  max_papers: number;
  samples: { title: string; year: number; has_abstract: boolean }[];
}

export default function RunDialog({
  adminKey, rows, onRan,
}: {
  adminKey: string;
  rows: { subfield_id: number; subfield_name: string }[];
  onRan: () => void;
}) {
  const thisYear = new Date().getFullYear();
  const [subfieldId, setSubfieldId] = useState<number | "">("");
  const [yearFrom, setYearFrom] = useState(thisYear - 2);
  const [yearTo, setYearTo] = useState(thisYear);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <section className="mt-8 rounded-lg border p-5">
      <h2 className="mb-3 text-lg font-medium">분석 실행</h2>

      <div className="flex flex-wrap gap-3">
        <select
          value={subfieldId}
          onChange={(e) => { setSubfieldId(Number(e.target.value)); setPreview(null); }}
          className="rounded border px-3 py-2"
        >
          <option value="">세부기술 선택</option>
          {rows.map((r) => (
            <option key={r.subfield_id} value={r.subfield_id}>{r.subfield_name}</option>
          ))}
        </select>
        <input type="number" value={yearFrom} onChange={(e) => setYearFrom(Number(e.target.value))}
               className="w-24 rounded border px-3 py-2" />
        <input type="number" value={yearTo} onChange={(e) => setYearTo(Number(e.target.value))}
               className="w-24 rounded border px-3 py-2" />
        <button
          disabled={!subfieldId || busy}
          onClick={async () => {
            setBusy(true);
            try {
              setPreview(await post<Preview>("/admin/preview",
                { subfield_id: subfieldId, year_from: yearFrom, year_to: yearTo }, adminKey));
            } finally { setBusy(false); }
          }}
          className="rounded border px-4 py-2"
        >
          미리보기 (비용 0)
        </button>
      </div>

      {preview && (
        <div className="mt-4 rounded bg-neutral-50 p-4 text-sm">
          <p>
            OpenAlex {preview.openalex_count.toLocaleString()}건 · KCI {preview.kci_count}건 ·
            예상 {preview.estimated_pages}콜 / ${preview.estimated_cost_usd.toFixed(4)}
          </p>
          <p className="text-neutral-600">
            오늘 사용 ${preview.budget_spent.toFixed(4)} / ${preview.budget_limit.toFixed(2)}
          </p>
          {preview.over_limit && (
            <p className="mt-2 text-red-600">
              검색 결과가 상한 {preview.max_papers.toLocaleString()}건을 초과합니다.
              검색식을 좁히거나 세부기술을 분할하세요.
            </p>
          )}
          <ul className="mt-3 space-y-1 text-xs text-neutral-600">
            {preview.samples.slice(0, 5).map((s) => (
              <li key={s.title}>
                [{s.year}] {s.title} {!s.has_abstract && <span className="text-amber-600">(abstract 없음)</span>}
              </li>
            ))}
          </ul>
          <button
            disabled={preview.over_limit || busy}
            onClick={async () => {
              setBusy(true);
              try {
                await post("/admin/run",
                  { subfield_ids: [subfieldId], year_from: yearFrom, year_to: yearTo }, adminKey);
                setPreview(null);
                onRan();
              } finally { setBusy(false); }
            }}
            className="mt-4 rounded bg-neutral-900 px-4 py-2 text-white disabled:opacity-40"
          >
            이 내용으로 분석 실행
          </button>
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: SubfieldEditor.tsx 작성**

```tsx
import { useEffect, useState } from "react";
import { post } from "../api";

interface Subfield {
  id: number; field_id: number; name: string; query: string;
  query_kci: string | null; active: boolean;
}

export default function SubfieldEditor({
  adminKey, onChanged,
}: { adminKey: string; onChanged: () => void }) {
  const [items, setItems] = useState<Subfield[]>([]);
  const [draft, setDraft] = useState({ field_id: 1, name: "", query: "", query_kci: "" });

  const load = () =>
    fetch("/api/admin/subfields", { headers: { "X-Admin-Key": adminKey } })
      .then((r) => r.json()).then(setItems);

  useEffect(() => { load(); }, []);

  return (
    <section className="rounded-lg border p-5">
      <h2 className="mb-3 text-lg font-medium">세부기술 · 검색식</h2>

      <div className="flex flex-wrap gap-2">
        <input placeholder="분야 ID" type="number" value={draft.field_id}
               onChange={(e) => setDraft({ ...draft, field_id: Number(e.target.value) })}
               className="w-24 rounded border px-3 py-2" />
        <input placeholder="세부기술명" value={draft.name}
               onChange={(e) => setDraft({ ...draft, name: e.target.value })}
               className="rounded border px-3 py-2" />
        <input placeholder="공통 검색식 (OpenAlex)" value={draft.query}
               onChange={(e) => setDraft({ ...draft, query: e.target.value })}
               className="flex-1 rounded border px-3 py-2" />
        <input placeholder="KCI 검색식 (비우면 공통값 사용)" value={draft.query_kci}
               onChange={(e) => setDraft({ ...draft, query_kci: e.target.value })}
               className="flex-1 rounded border px-3 py-2" />
        <button
          onClick={async () => {
            await post("/admin/subfields",
              { ...draft, query_kci: draft.query_kci || null }, adminKey);
            setDraft({ field_id: draft.field_id, name: "", query: "", query_kci: "" });
            load(); onChanged();
          }}
          className="rounded bg-neutral-900 px-4 py-2 text-white"
        >
          추가
        </button>
      </div>

      <table className="mt-4 w-full text-sm">
        <tbody>
          {items.map((s) => (
            <tr key={s.id} className="border-b">
              <td className="py-2">{s.name}</td>
              <td className="text-neutral-600">{s.query}</td>
              <td className="text-neutral-400">{s.query_kci ?? "(공통 사용)"}</td>
              <td className="text-right">
                <button
                  onClick={async () => {
                    if (!confirm(`'${s.name}'을(를) 삭제할까요?`)) return;
                    await fetch(`/api/admin/subfields/${s.id}`,
                      { method: "DELETE", headers: { "X-Admin-Key": adminKey } });
                    load(); onChanged();
                  }}
                  className="text-xs text-red-600"
                >
                  삭제
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

- [ ] **Step 5: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: 에러 없이 `dist/` 생성

- [ ] **Step 6: 커밋**

```bash
git add frontend/src
git commit -m "feat: 관리자 화면 — 검색식 편집 · 미리보기 · 실행 확정 · 재실행"
```

---

### Task 14: 통합 확인 · PORTS 등록 · 문서

**Files:**
- Create: `README.md`, `CLAUDE.md`
- Modify: `../PORTS.md` (레지스트리에 NN=03 등록)

- [ ] **Step 1: 전체 스택 기동**

Run:

```bash
cp .env.example .env
# .env에 GEMINI_API_KEY, OPENALEX_API_KEY, ADMIN_KEY 입력
docker compose up -d --build
docker compose exec api alembic upgrade head
```

Expected: 세 컨테이너 모두 `running`

- [ ] **Step 2: 공개 API 동작 확인**

Run: `curl -s localhost:8003/api/fields | head -c 300`
Expected: 12개 분야가 JSON 배열로 반환

- [ ] **Step 3: 관리자 인증 확인**

Run:

```bash
curl -s -o /dev/null -w '%{http_code}\n' localhost:8003/api/admin/dashboard
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Admin-Key: $(grep ^ADMIN_KEY .env | cut -d= -f2)" \
  localhost:8003/api/admin/dashboard
```

Expected: `401` 다음 `200`

- [ ] **Step 4: 실제 세부기술 하나로 종단 확인**

브라우저에서 `http://localhost:8103/admin` 접속 → 관리자 키 입력 →
세부기술 추가(예: 분야 12 양자, 이름 "양자컴퓨팅", 검색식 `quantum computing error correction`) →
미리보기로 건수 확인 → 좁은 연도 범위(당해연도 1년)로 실행 →
`docker compose logs -f api`로 `논문 검색 중 → 성과 추출 중 → 보고서 작성 중 → 완료` 진행 확인 →
`http://localhost:8103/analyses/1`에서 보고서·통계·"검색 M건 / 분석 대상 N건" 표기 확인 →
"PDF로 저장" 버튼으로 인쇄 미리보기가 뜨고 표·차트가 페이지 경계에서 잘리지 않는지 확인.

- [ ] **Step 5: PORTS.md에 등록**

`/home/dev/code/PORTS.md`의 레지스트리 표에 한 줄 추가:

```
| 03 | performance-review | 8003 | 8103 | 5403 | — | — |
```

"비어 있는 NN" 줄에서 `03`을 제거하고, 아래 메모를 남긴다:

```
- performance-review → NN=00은 backend 포트가 8000이 되어 nst-wiki와 충돌하므로 03을 배정
```

- [ ] **Step 6: README.md 작성**

프로젝트 개요, 빠른 시작(.env 작성 → `docker compose up` → `alembic upgrade head`), 포트 표,
주요 환경변수 표, 관리자 사용법(키 입력 → 세부기술 등록 → 미리보기 → 실행)을 담는다.
OpenAlex 일일 예산($1 공유)과 abstract 약 18% 누락을 "알려진 제약"으로 명시한다.

- [ ] **Step 7: CLAUDE.md 작성**

실행 명령어, 포트, 파이프라인 6단계, 캐시 3중 키, 프리즈 없는 증분 갱신 정책,
OpenAlex 과금 모델(요청 건당·search $0.001), `model_ver` 변경 시 캐시 무효화,
`app/models/__init__.py`에 모델을 모두 import해야 FK가 해석된다는 점을 적는다.

- [ ] **Step 8: 커밋**

```bash
git add README.md CLAUDE.md ../PORTS.md
git commit -m "docs: README · CLAUDE.md · PORTS 레지스트리 등록(NN=03)"
```

---

## 자체 검토 결과

**스펙 커버리지** — 스펙 각 절이 어느 태스크에 대응하는지:

| 스펙 절 | 태스크 |
|---|---|
| 2. 확정 요구사항 (분야 계층·검색식 위치) | Task 2, 11 |
| 3. map → reduce | Task 7, 9 |
| 3. 규모 검증 / 3단 reduce | Task 9 (`group_for_reduce`) |
| 4. Rate limit — 예산 게이트 | Task 5, 6 |
| 4. OpenAlex 현행 제약 | Task 3 |
| 4. 대규모 대응 (하드 가드) | Task 10 (`AnalysisTooLarge`), 13 (`over_limit`) |
| 5. 데이터 모델 | Task 2 |
| 6. 캐시 3중 키 | Task 6(검색), 7(추출), 10(보고서) |
| 7. 프리즈 없는 증분 갱신 | Task 10 (`enqueue`, `is_stale`) |
| 8. 파이프라인 6단계 | Task 10 (`advance`) |
| 9. 관리자 기능 | Task 11, 13 |
| 10. 통계 항목 | Task 8 |
| 11. 프론트엔드 · PDF | Task 12 |
| 12. 배포 구성 | Task 1, 14 |
| 13. 테스트 범위 | Task 5·6·7·10에 분산 |

**검토 중 수정한 것**

- `rollup_field`는 Task 9에 구현되지만 이를 호출하는 잡 단계가 어느 태스크에도 없었다.
  대분류 보고서는 세부기술 보고서가 모두 완료된 뒤에야 의미가 있어 잡 상태머신에 넣기 애매하므로,
  **Task 11의 공개 API에서 요청 시점에 생성**하는 방식이 아니라 별도 후속 작업으로 미룬다.
  → 초판 범위에서는 세부기술 단위 보고서까지만 제공하고, `rollup_field`는 구현해두되 호출부는 두지 않는다.
  대분류 화면은 하위 세부기술 보고서 목록을 나열한다. (Task 12 `FieldDetail`)
- `sampled` 플래그는 모델·API·UI에 있으나 이를 True로 만드는 표본 추출 경로는 초판에 없다.
  스펙의 "강행 시 표본" 은 하드 가드가 먼저 막으므로 초판에서는 도달하지 않는다.
  필드는 남겨두되(스키마 변경 회피) 표본 로직은 후속 작업으로 미룬다.

**후속 작업 (초판 범위 밖)**

1. 대분류 rollup 보고서 생성·노출
2. 하드 가드 초과 시 표본 추출 실행 경로
3. 여러 batch 청크의 병렬 제출 (현재는 루프 주기마다 1청크씩)

---

## 실행 중 계획 수정 이력

구현·리뷰 과정에서 계획서 원본 코드의 결함이 발견돼 아래와 같이 바뀌었다.
Task 1~4의 코드 블록은 이 수정을 반영하지 않은 원본이므로, 실제 구현은 `backend/` 소스를 기준으로 본다.

| 태스크 | 수정 | 이유 |
|---|---|---|
| Task 1 | `alembic.ini`를 Task 2 산출물로 이관 | Task 1에 이를 만드는 Step이 없었고 실제로는 Task 2의 `alembic init`이 생성 |
| Task 1 | `class Config` → `model_config = SettingsConfigDict(...)` | Pydantic V2 deprecation |
| Task 1 | `backend/tests/conftest.py` 추가 | `settings` 모듈 레벨 싱글턴 때문에 테스트 import 시점에 필수 env가 필요 |
| Task 3 | `_sanitize_query` 추가 (콤마·파이프 → 공백) | OpenAlex filter DSL은 콤마를 AND, 파이프를 OR로 해석하며 이스케이프 수단이 없어, 검색식에 이 문자가 들어가면 **에러 없이 다른 결과**를 반환 |
| Task 3 | `http_max_attempts` / `http_timeout_seconds`를 `.env`로 | 전역 제약(한도는 `.env`) 위반 |
| Task 3 | `BASIC_PAGING_LIMIT` 삭제 | 항상 cursor 페이징을 쓰므로 참조되지 않는 죽은 상수 |
| Task 4 | `kci_max_pages`(기본 20) 추가 + 상한 도달 시 경고 | KCI는 연도 필터가 없어 코드에서 거르는데, 대상 연도 논문이 적으면 페이지를 무한정 넘길 수 있었음 |
| Task 4 | `PAGE_SIZE` → `settings.kci_page_size` | 전역 제약 위반 |
| Task 4 | `kci_concurrency` 삭제 | 분석 1건당 KCI 호출이 1회 순차라 동시성 대상이 없는 죽은 설정 |
