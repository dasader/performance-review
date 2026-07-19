# 전략기술 분야별 논문성과 분석 서비스

12대 국가전략기술(및 하위 세부기술)별로 OpenAlex · KCI에서 한국 논문을 검색하고,
title + abstract를 근거로 주요 기술적 성과를 Gemini로 종합해 마크다운 보고서 + 통계 + PDF로
내보내는 서비스. 검색식은 세부기술 단위로만 관리자가 등록하며, 대분류는 하위 결과를 모아 보여준다.

세부 설계는 `docs/superpowers/specs/2026-07-18-strategic-tech-paper-analysis-design.md` (또는
`docs/design.html`)를 참고.

## 빠른 시작

```bash
cp .env.example .env
# .env에 GEMINI_API_KEY, OPENALEX_API_KEY, ADMIN_KEY를 채운다.
# 키가 비어 있어도 컨테이너는 기동된다(Gemini 클라이언트는 지연 생성) — 실제 분석 실행 시에만 필요.

docker compose up -d --build
```

`api` 컨테이너는 기동 시 entrypoint에서 `alembic upgrade head`를 자동으로 실행한 뒤에야
uvicorn을 띄운다(수동 실행 불필요) — 새 마이그레이션이 추가된 채 재배포해도 조용히 구
스키마로 뜨지 않는다. 세 컨테이너(`api` / `web` / `db`)가 모두 `running`이 되면 준비 완료.

- API: http://localhost:8003 (`/api/health`, `/api/fields`, `/api/admin/*`)
- 웹: http://localhost:8103 (nginx가 `/api/*`를 `api` 컨테이너로 프록시)

동작 확인:

```bash
curl -s localhost:8003/api/health                     # {"status":"ok"}
curl -s localhost:8003/api/fields | head -c 300        # 12대 분야가 seed되어 있어야 함
```

## 포트

| 컨테이너 | 호스트 포트 | 비고 |
|---|---|---|
| `api` (FastAPI + 백그라운드 잡 루프) | 8003 | Celery 없음 — 잡 루프가 api 프로세스 안에 asyncio 태스크로 상주 |
| `web` (nginx + React 빌드) | 8103 | `/api/*`를 api 컨테이너로 리버스 프록시 |
| `db` (PostgreSQL 16) | 5403 | |

NN=03. 레지스트리는 `../PORTS.md` 참고 — NN=00은 backend 포트가 8000이 되어 nst-wiki(8000)와
충돌하므로 회피했다.

## 주요 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | (빈 값) | 비어 있어도 기동은 되지만 분석 실행(map/reduce) 시 필요 |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | |
| `THINKING_MAP` / `THINKING_REDUCE` | `low` / `high` | 모델별 허용값이 다르므로 API가 거부하면 여기서 조정 |
| `OPENALEX_API_KEY` | (빈 값) | 2026-02-13부터 필수 |
| `OPENALEX_DAILY_BUDGET_USD` | `0.5` | 이 서비스가 쓸 몫의 상한(키 자체는 다른 서비스와 공유, 전체 한도 $1/day) |
| `OPENALEX_SEARCH_COST_USD` | `0.001` | search 계열 요청 1건 단가(참고용 — 실비용은 응답 `meta.cost_usd`로 집계) |
| `KCI_API_KEY` | (빈 값) | |
| `KCI_MAX_PAGES` | `20` | KCI는 연도 필터 API가 없어 코드에서 걸러내는데, 대상 연도 논문이 적으면 페이지를 무한정 넘길 수 있어 상한을 둠. 도달 시 결과가 잘릴 수 있고 경고 로그가 남는다 |
| `ADMIN_KEY` | (빈 값) | 관리자 API/화면 인증용 단일 키 |
| `MAX_PAPERS_PER_ANALYSIS` | `5000` | 검색 결과가 이 값을 넘으면 실행 자체를 차단(하드 가드) |
| `REDUCE_GROUP_THRESHOLD` | `500` | 세부기술의 추출 건수가 이 값을 넘으면 3단 reduce로 자동 분기 |
| `DEFAULT_YEAR_RANGE` | `3` | 관리자 대시보드 기본 실행 범위(최근 N개년) |
| `LOOP_INTERVAL_SECONDS` | `30` | 백그라운드 잡 루프 주기 |
| `API_PORT` / `WEB_PORT` / `DB_PORT` | `8003` / `8103` / `5403` | 호스트 포트 오버라이드 |

전체 목록은 `.env.example` 참고.

## 관리자 사용법

1. `http://localhost:8103/admin` 접속 → `.env`의 `ADMIN_KEY` 값을 입력(브라우저 `sessionStorage`에 보관, 계정 체계 없음).
2. **세부기술 등록**: 대분류 선택 → 이름 + 검색식(`query`, 필요 시 `query_kci` override) 입력.
   대분류 12개는 마이그레이션으로 이미 seed되어 있고, 세부기술은 초기 비어 있어 관리자가 직접 추가한다.
3. **미리보기**: 실행 전 "미리보기" 버튼으로 소스별 건수·샘플 20건을 확인한다. 검색만 실행하므로 LLM 비용은 0.
4. **실행**: 연도 범위를 선택해 실행을 확정하면 신규 논문 수 · 예상 토큰 · 예상 비용 · 제출 청크 수를 보여준 뒤 잡을 큐에 넣는다.
   진행 상황은 `docker compose logs -f api`로 `논문 검색 중 → 성과 추출 중 → 보고서 작성 중 → 완료` 순으로 확인할 수 있다.
5. **실행 이력/대시보드**: 세부기술 × 연도 격자에서 최종수집일 · 논문수 · 상태를 보고, 실패분만 재실행할 수 있다.

## 알려진 제약

- **OpenAlex API 키 필수 + 일일 예산 공유**: OpenAlex는 API 키가 필수이며 무료 한도가 **하루 $1(UTC 자정 리셋)**이다.
  이 키는 다른 서비스와 공유되므로, `OPENALEX_DAILY_BUDGET_USD`(기본 $0.5)로 이 서비스가 쓸 몫만 제한한다.
  예산을 넘으면 신규 검색이 `paused`로 멈추고 다음날(UTC) 자동 재개된다.
- **abstract 약 18% 누락**: OpenAlex 표본 확인 결과 논문의 약 18%는 abstract가 없다.
  abstract가 없는 논문은 map(성과 추출) 대상에서 제외되며, 보고서의
  **"검색 M건 / 분석 대상 N건 (abstract 미보유 제외)"** 표기가 이 차이를 나타낸다.
- **Gemini Batch는 최대 24시간**: batch 작업 완료까지 최대 24시간이 걸릴 수 있다. 그 사이 `api` 컨테이너가
  재시작되어도 진행 상태와 batch job id가 DB에 있으므로 잡은 이어서 처리된다.
- **KCI 페이지 상한**: KCI는 연도 필터 API가 없어 코드에서 걸러내며, 대상 연도 논문이 적은 검색식이면
  `KCI_MAX_PAGES`(기본 20)에 먼저 걸려 결과가 잘릴 수 있다.
- **`thinking_level` 허용값 미확정**: `gemini-3.1-flash-lite`의 `thinking_level` 허용값이 공식 문서에
  명시돼 있지 않다. API가 `.env`의 `THINKING_MAP`(`low`)을 거부하면 `.env`에서 값을 조정한다(코드 변경 불필요).
