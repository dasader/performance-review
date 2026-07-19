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

> **`.env`를 수정한 뒤에는 `docker compose restart`가 아니라
> `docker compose up -d --force-recreate api`를 쓴다.** `restart`는 `env_file`을 다시 읽지 않아
> 옛 값이 그대로 남는다. 특히 OpenAlex는 키 없이도 소량 호출이 되기 때문에, 키가 반영되지
> 않은 상태에서도 검색·미리보기는 성공하고 Gemini 호출 단계에 가서야 실패한다.

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
| `KCI_API_KEY` | (빈 값) | **비우면 KCI를 건너뛰고 OpenAlex만 사용한다(의도적 skip). 설정했는데 API가 에러를 돌려주면 분석이 실패한다** — 아래 "KCI 실패는 조용히 넘어가지 않는다" 참고 |
| `KCI_MAX_PAGES` | `20` | KCI는 연도 필터 API가 없어 코드에서 걸러내는데, 대상 연도 논문이 적으면 페이지를 무한정 넘길 수 있어 상한을 둠. 도달 시 결과가 잘릴 수 있고 경고 로그가 남는다 |
| `ADMIN_KEY` | (빈 값) | 관리자 API/화면 인증용 단일 키 |
| `MAX_PAPERS_PER_ANALYSIS` | `5000` | 검색 결과가 이 값을 넘으면 실행 자체를 차단(하드 가드) |
| `REDUCE_GROUP_THRESHOLD` | `500` | 세부기술의 추출 건수가 이 값을 넘으면 3단 reduce로 자동 분기 |
| `DEFAULT_YEAR_RANGE` | `3` | 관리자 대시보드 기본 실행 범위(최근 N개년) |
| `LOOP_INTERVAL_SECONDS` | `30` | 백그라운드 잡 루프 주기 |
| `SCHEDULE_ENABLED` / `SCHEDULE_DAY` / `SCHEDULE_HOUR` / `SCHEDULE_YEARS_BACK` | `true` / `10` / `3` / `1` | 월간 자동 분석 스케줄의 **초기 기본값**(최초 실행 시 DB에 한 번만 seed됨) — 이후에는 관리자 화면 "자동 분석 스케줄" 카드에서 재기동 없이 바꾼다. 아래 "월간 자동 분석 스케줄러" 참고 |
| `SCHEDULE_TIMEZONE` | `Asia/Seoul` | 스케줄 타임존. **DB로 옮기지 않고 `.env` 전용으로 유지** — 변경이 드물고 잘못된 값을 넣으면 `ZoneInfo`가 즉시 실패하므로, 관리자 화면에는 읽기 전용으로만 표시한다 |
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

## 월간 자동 분석 스케줄러

별도 컨테이너나 스케줄러 라이브러리(APScheduler, celery-beat 등) 없이, 기존 30초 잡 루프
(`runner.loop()`)가 매 틱마다 "지금이 실행 시각인가"를 확인하는 방식으로 동작한다.

- **설정은 이제 DB에서 관리한다**: `SCHEDULE_ENABLED`/`SCHEDULE_DAY`/`SCHEDULE_HOUR`/`SCHEDULE_YEARS_BACK`은
  `schedule_settings` 테이블(싱글턴 행)이 없을 때 딱 한 번 seed되는 **초기 기본값**일 뿐이다. 실제 값을
  바꾸려면 `.env`를 고치고 컨테이너를 재생성할 필요 없이 관리자 화면(`/admin`)의 "자동 분석 스케줄"
  카드에서 바로 바꾸면 다음 잡 루프 틱부터 적용된다. `SCHEDULE_TIMEZONE`만 예외 — DB로 옮기지 않고
  `.env` 전용으로 유지하며 화면에는 읽기 전용으로 표시한다(타임존은 잘못 바꾸면 `ZoneInfo`가 즉시
  실패해 스케줄러 전체가 멈추는 값이라 변경 빈도·리스크가 다른 설정과 다르다고 판단했다).
- **주기·시각**: 매월 지정한 일(기본 10일) 시각대(기본 새벽 3시, `SCHEDULE_TIMEZONE` 기준 — 기본 KST)에
  실행된다. **1~3일을 피한 이유**: 같은 OpenAlex 키를 쓰는 다른 서비스와 겹치지 않기 위함.
- **대상 연도**: 활성(`active=True`) 세부기술 전부에 대해 당해연도 ~ (당해 − 대상 연도 범위)연도
  (기본값 1 → 당해·직전 2개년)를 큐잉한다. 매번 검색을 다시 돌려 **그 사이 새로 등재된 논문**을
  잡되, 신규가 0건이면 보고서 재생성을 생략해 비용은 검색분(약 $0.004)에 그친다.
- **멱등성**: `scheduled_runs.run_month`(예: `"2026-08"`)에 unique 제약을 걸어, 같은 달에 컨테이너가
  재시작돼 실행 시각대에 잡 루프가 다시 돌아도 중복 큐잉되지 않는다. 관리자 화면의 "지금 실행"(스케줄
  시각과 무관하게 즉시 1회 큐잉)은 `"YYYY-MM-manual-..."` 형식의 별도 키를 써서 그 달의 정기 실행을
  막지 않는다.
- **실행 이력**: 잡이 `done`에 도달할 때마다 `analysis_runs`에 검색/분석 건수와 트리거(`manual`|`scheduled`)를
  기록한다. 관리자 화면 "자동 분석 스케줄" 카드에서 최근 12건의 실행(월·시각·큐잉 건수·성공/실패 요약)을
  볼 수 있다(`GET /api/admin/schedule`) — 몇 달치가 쌓이면 "월별로 논문이 실제로 얼마나 느는가"를
  데이터로 확인하는 데도 쓴다.
- **비용 최적화**: 신규 추출 논문이 0건이면(모델 버전이 바뀌어 전량 재추출된 경우 제외) 보고서 재생성
  LLM 호출을 생략하고 통계만 갱신한다 — 재실행 1회 비용의 약 47%가 보고서 생성이라 이 스킵으로 크게
  줄어든다.
- 관리자 화면(`/admin`)의 "자동 분석 스케줄" 카드에서 on/off · 일정 편집 · 다음 실행 예정 시각 ·
  최근 실행 이력 · 즉시 실행("지금 실행")을 모두 처리한다.

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
- **KCI 실패는 조용히 넘어가지 않는다**: KCI는 키 만료·한도 초과를 HTTP 200 + 본문
  `<resultMsg>`(예: "사용기간이 종료되었습니다.")로 알린다. 이를 "결과 0건"으로 처리하면
  보고서가 국내지 성과를 0건으로 **단정**하게 되므로, 이 경우 `KciApiError`로 분석을 실패시킨다.
  KCI 없이 돌리려면 `KCI_API_KEY`를 비워 명시적으로 skip 모드를 택한다.
- **KCI 검색식은 한글로 넣어야 한다**: KCI는 국내 학술지 색인이라 영문 키워드로는 거의 걸리지 않는다.
  세부기술의 `query`(OpenAlex용, 영문)와 별도로 `query_kci`(한글)를 채워야 국내지 성과가 잡힌다.
  비워 두면 `query`를 그대로 쓰므로 영문 검색식이 KCI에 그대로 들어간다.
- **`thinking_level` 허용값 미확정**: `gemini-3.1-flash-lite`의 `thinking_level` 허용값이 공식 문서에
  명시돼 있지 않다. API가 `.env`의 `THINKING_MAP`(`low`)을 거부하면 `.env`에서 값을 조정한다(코드 변경 불필요).
