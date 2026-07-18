# 전략기술 분야별 논문성과 분석 서비스 — 설계

작성일: 2026-07-18
검토 문서: `docs/design.html` (동일 내용의 HTML 버전)

## 1. 개요

전략기술 분야별로 OpenAlex · KCI에서 한국 논문을 검색하고, title + abstract를 기반으로
주요 기술적 성과를 종합 정리해 마크다운 보고서와 PDF로 출력하는 서비스.

레퍼런스 `references/17_Spec-investigation` (TechSpec)에서 재활용:
검색 에이전트(OpenAlex/KCI), Gemini 호출 래퍼(`run_sync`), FastAPI/SQLAlchemy/Alembic 구조, Docker 구성.

## 2. 확정 요구사항

| 항목 | 결정 |
|---|---|
| 분야 정의 | 12대 국가전략기술을 대분류 seed로 넣고, 하위 세부기술(중점기술) 단위까지 관리자가 관리 |
| 검색식 보유 위치 | **세부기술에만** 보유. 대분류는 하위 결과 합성만 담당 |
| 한국 성과 판정 | KCI 전수 + OpenAlex `authorships.institutions.country_code = KR` 포함 논문 |
| 비용 절감 | Gemini Batch API + 결과 DB 캐시 |
| 인프라 | FastAPI + PostgreSQL + 내장 asyncio 백그라운드 루프 (Celery/Redis 미사용) |
| LLM | Gemini 3.1 Flash-Lite. map = thinking low, reduce = thinking high |
| 보고서 구성 | ① 기본 통계 ② 주요 기술적 성과 서술 ③ 세부기술별 주제 클러스터링 |
| PDF | 브라우저 인쇄 (`@media print` + `window.print()`) |
| 포트 | NN = 03 (api 8003 / web 8103 / db 5403) |

범위 제외: 연도간 변화·부상 키워드 분석, 사용자 계정/역할 체계, 이메일 알림.

## 3. 분석 방식 — 논문단위 map → 분야단위 reduce

**map**: 논문 1건 = Batch 요청 1개. `title + abstract` →
`{세부기술 태그, 기술적 성과 요약 1~2문장, 성과유형, 정량수치}` 구조화 추출.

**reduce**: 세부기술별로 map 결과(논문당 ~50토큰)를 한 번에 투입해 성과 서술 + 클러스터링.

캐시가 논문 단위이므로 재분석·당해연도 갱신 시 신규 논문에만 과금되고, 근거 논문 역추적이 가능하다.
청크 요약(30건씩 묶기)은 캐시 단위가 청크라 논문 1건 증가에 청크 전체 재호출이 필요해 채택하지 않았다.
abstract 전량 1콜은 컨텍스트 초과·환각·캐시 불가로 채택하지 않았다.

### 규모 검증 (한 세부기술 3,000건 기준)

- 사전 필터(중복 제거 · 한국 판정 · 추출 캐시 히트 제외)로 실제 map 대상은 축소되고, 재실행 시엔 신규분만 남는다.
- map 요청 3,000개 → JSONL 1,000건 단위 분할 제출. 입력 ~700 tok/건, 출력 ~250 tok/건(thinking low).
- 비용 대략 $0.2~0.4 (추정, 구현 시 실단가 확인). 소요 수십 분, 상한 24h.
- **병목은 reduce**: 3,000건 요약을 한 콜에 넣으면 ~180K 토큰이라 종합 품질이 떨어진다.
  세부기술 계층이 이를 자연히 분할하며, 한 세부기술이 `REDUCE_GROUP_THRESHOLD`(기본 500)를 넘으면
  성과유형/클러스터별 그룹 요약을 끼워 3단 reduce로 자동 분기한다.
- 실행 전 관리자 화면에 예상 규모·비용을 표시하고 확정을 받는다.

## 4. Rate limit 제어

| 경로 | 실제 제한 | 대응 |
|---|---|---|
| Batch 제출 (map) | 모델별 enqueued token 총량, 동시 batch job 수, 입력 파일 2GB | 제출 전 토큰 합산 → 슬롯 게이트 |
| 동기 호출 (reduce) | RPM · TPM | 토큰버킷 2종 + 429 지수 백오프 |
| 검색 API | OpenAlex polite pool ~10 req/s · KCI 동시성 3 | `asyncio.Semaphore` |

**사전 체크**: 제출 대상 논문의 예상 입력 토큰을 `문자수 / 4`로 합산한다
(논문마다 `count_tokens`를 부르면 그 자체가 호출 낭비이며, 게이트 판단엔 ±20% 오차로 충분).
진행 중 batch job의 enqueued 토큰 합 + 신규 예상치가 한도 이내일 때만 제출하고,
초과하면 대기 큐에 두어 백그라운드 루프가 슬롯이 비는 대로 순차 제출한다.

**런타임 방어**: 동일 프로젝트 쿼터를 다른 작업이 함께 소비하므로 429는 어차피 발생한다. 게이트와 재시도를 둘 다 둔다.
429 / `RESOURCE_EXHAUSTED` → 지수 백오프(1·2·4·8·16초, 최대 5회), `Retry-After` 헤더 우선.
재시도 소진 시 해당 청크만 `failed` 표시 후 진행을 계속하고, 관리자 화면에서 실패분만 재실행한다.

한도값은 모델·티어별로 달라지므로 전부 `.env`에 두고 코드에 하드코딩하지 않는다.

## 5. 데이터 모델

```
fields (12대 대분류)
  └ subfields (세부기술)          ← query, query_kci 보유
       └ analyses (subfield_id, year, status, report_md, stats_json,
                   snapshot_at, query_hash)
            └ analysis_papers (analysis_id, paper_id)

papers (paper_key, title, abstract, year, journal, authors, source,
        korea_flag, citations, institutions, countries)
    paper_key = DOI ‖ kci_id ‖ openalex_id

paper_extractions (paper_key, subfield_id, tech_summary,
                   achievement_type, metrics_json, model_ver)
```

`subfields.query`는 공통 검색식, `subfields.query_kci`는 선택적 override(비어 있으면 공통값 사용).
OpenAlex는 불리언 문법, KCI는 키워드 위주라 동일 문자열을 강제하지 않는다.

## 6. 캐시 전략 (3중 키)

| 단계 | 캐시 키 | 효과 |
|---|---|---|
| ① 검색 | `hash(query + year + source)` | 히트 시 OpenAlex/KCI API 재호출 없음 |
| ② 추출 | `papers.paper_key` (+ `model_ver`) | 논문당 Gemini 추출 평생 1회 |
| ③ 보고서 | `analyses(subfield_id, year)` | 동일 분야·연도 재요청 시 저장본 반환 |

## 7. 연도 처리 — 프리즈 없음, 전량 증분 갱신

과거연도 freeze를 두지 않는다. 전년도 논문이 올해 새로 포착되는 경로가 여러 개이기 때문이다.

| 요소 | 영향 |
|---|---|
| 색인 지연 | KCI 발행 후 3~6개월, OpenAlex는 Crossref 경유로 수주~수개월 |
| online-first 연도 재배정 | 선공개 후 정식 호 게재 시 `publication_year`가 사후 변경 |
| 메타데이터 사후 보정 | 저자 소속(=한국 판정 근거)·abstract가 나중에 채워짐 |
| 검색식 변경 | 검색식 수정 시 과거연도 보고서도 stale이 됨 |
| 인용수 | 계속 증가하는 값 → 기준 시점 명시 필요 |

- 모든 연도가 증분 재실행 대상. 캐시가 논문 단위라 재실행 비용은 신규분에만 발생한다.
- `analyses.snapshot_at`(최종 수집 시점)과 `analyses.query_hash`를 기록한다.
  검색식이 바뀌면 해시 불일치로 해당 연도가 자동 "갱신 필요"로 표시된다.
- 관리자 화면에 연도별 `최종수집일 / 논문수 / 상태(최신·갱신필요·미실행)`를 노출한다.
- 기본 실행 범위는 최근 `DEFAULT_YEAR_RANGE`(기본 3)개년으로 제한해 습관적 전량 재실행을 막는다.
- 인용수 통계에는 `snapshot_at` 기준임을 보고서에 명시한다.

## 8. 파이프라인 (6단계)

| # | 단계 | 내용 | LLM |
|---|---|---|---|
| 1 | `search` | 세부기술 검색식 → OpenAlex(연도+KR 필터) · KCI 병렬 검색. DOI/title 정규화 후 중복 제거 → `papers` upsert | — |
| 2 | `filter` | 한국 판정, abstract 없는 레코드 제외, `paper_extractions` 캐시 히트 제외 → 실제 map 대상 확정 | — |
| 3 | `map` | Batch JSONL 제출 → 폴링 → 결과 파싱 → `paper_extractions` 저장 | thinking low |
| 4 | `stats` | 기본 통계 전량 코드로 집계 (LLM 미사용 — 숫자를 모델에 맡기면 틀림) | — |
| 5 | `reduce` | 세부기술별 보고서: 성과 서술 + 주제 클러스터링. 임계값 초과 시 그룹 요약 삽입(3단) | thinking high |
| 6 | `rollup` | 대분류 보고서 = 하위 세부기술 보고서 합성 1콜 | thinking high |

### 잡 상태 머신 · 재시작 안전성

```
pending → searching → extracting → reducing → done
                          │            │
                          └──→ failed ←┘   (실패 청크만 격리, 부분 재실행 가능)
```

Batch는 최대 24h이므로 컨테이너 재시작이 반드시 걸린다.
진행 상태와 batch job id를 전부 DB에 두고, FastAPI startup의 백그라운드 루프가 미완 잡을 스캔해 이어받는다.
메모리에 상태를 남기지 않는다.

## 9. 관리자 기능

`.env`의 `ADMIN_KEY` 단일 값. 관리자 API는 `X-Admin-Key` 헤더를 검증하고,
프론트는 입력받아 `sessionStorage`에 보관한다. 사용자 계정/역할 체계는 만들지 않는다(운영자 1인 전제).

| 화면 | 기능 |
|---|---|
| 분야 관리 | 대분류 12개는 마이그레이션으로 seed. 세부기술은 초기 비어 있으며 관리자가 CRUD(추가·수정·삭제·활성화)로 등록 |
| 검색식 편집 | 세부기술별 `query` + `query_kci` override. 미리보기 버튼은 검색만 실행해 소스별 건수·샘플 20건 표시 (LLM 미호출 = 비용 0) |
| 실행 대시보드 | 세부기술 × 연도 격자에 최종수집일 / 논문수 / 상태. 대상 체크 → 실행 |
| 실행 확정 | 검색 선행 후 신규 논문 수 · 예상 토큰 · 예상 비용 · 제출 청크 수 표시 → 확정 |
| 실행 이력 | 잡 목록·진행률·소요시간·실제 토큰 사용량, 실패분 재실행 |

## 10. 기본 통계 항목 (코드 집계)

- 연도별 논문 수 · 소스 구성(KCI 국내지 vs 국제지 비율)
- 상위 기관 20 · 상위 저널 20 · 상위 저자 20
- 국제공동연구 비율(KR 외 국가 소속 공저자 포함 논문 비중) · 주요 협력국
- 인용수 분포(중앙값 · 상위 10% 임계값 · 피인용 상위 논문 리스트)
- 세부기술별 논문 분포 · 성과유형별 분포

## 11. 프론트엔드 · PDF

- 공개 화면 3개: 분야 목록 → 분야 상세(연도 선택) → 보고서(통계 표·차트 + 성과 서술 + 클러스터 표)
- URL 공유가 필요하므로 `react-router` 최소 사용 (레퍼런스의 step-state 방식은 공유 불가)
- 차트: Recharts
- 디자인: `/frontend` 스킬 + `../trade-ews` 패밀리룩
- PDF: 보고서 페이지에 `@media print` 스타일 적용 후 `window.print()` → "PDF로 저장".
  서버사이드 PDF 생성은 하지 않는다(레퍼런스에서 weasyprint 실패 전례, 브라우저 인쇄는 의존성 0에
  한글 폰트·차트 렌더링이 그대로 재현됨).

## 12. 배포 구성

포트 NN = 03. PORTS.md에서 비어 있는 가장 작은 번호는 00이지만 `80`+`00` = 8000을 nst-wiki가 점유 중이라 회피했다.

| 컨테이너 | 호스트 포트 | 비고 |
|---|---|---|
| `api` (FastAPI + 백그라운드 루프) | 8003 | Celery 없음 — 루프가 api 프로세스 내부에 상주 |
| `web` (nginx + React 빌드) | 8103 | |
| `db` (PostgreSQL 16) | 5403 | |

PORTS.md 레지스트리에 `03 | performance-review` 한 줄을 추가한다.

### .env

```
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.1-flash-lite
THINKING_MAP=low
THINKING_REDUCE=high

KCI_API_KEY=
OPENALEX_MAILTO=            # polite pool

ADMIN_KEY=
DATABASE_URL=

BATCH_MAX_ENQUEUED_TOKENS=
BATCH_MAX_CONCURRENT_JOBS=2
BATCH_MAX_REQUESTS_PER_FILE=1000
SYNC_RPM=
SYNC_TPM=

REDUCE_GROUP_THRESHOLD=500  # 초과 시 3단 reduce
DEFAULT_YEAR_RANGE=3        # 기본 실행 범위(최근 N개년)
```

## 13. 테스트 범위

프레임워크·픽스처 없이 `pytest` 소수만. 깨지면 조용히 돈이 새는 로직에 한정한다.

- 증분 로직 — 캐시 히트 논문이 map 대상에서 제외되는가
- `query_hash` 변경 시 "갱신 필요" 판정
- rate 게이트 — 한도 초과 시 제출이 대기 큐로 가는가
- 한국 판정 필터 (KCI 전수 / OpenAlex KR 포함)
