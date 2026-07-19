# CLAUDE.md — performance-review

전략기술 분야별 논문성과 분석 서비스. 자세한 설계는
`docs/superpowers/specs/2026-07-18-strategic-tech-paper-analysis-design.md` 참고.

## 실행 명령어

```bash
# 스택 기동 — api 컨테이너 entrypoint(docker-entrypoint.sh)가 uvicorn 전에
# alembic upgrade head를 자동 실행한다(M15, 수동 실행 불필요). 현재 head: 0008
docker compose up -d --build

# .env를 고친 뒤에는 restart가 아니라 재생성해야 한다.
# `docker compose restart`는 env_file을 다시 읽지 않아 옛 값이 그대로 남는다.
docker compose up -d --force-recreate api

# 로그
docker compose logs -f api

# 테스트 (컨테이너 밖, 로컬 venv)
cd backend && ./.venv/bin/python -m pytest

# 프론트엔드 테스트 (vitest, 순수 함수 대상 — jsdom 등 브라우저 환경 없음)
cd frontend && npm test

# 마이그레이션 추가
docker compose exec api alembic revision --autogenerate -m "설명"
```

테스트는 반드시 `backend/.venv`를 쓴다 — 시스템 파이썬에는 의존성이 안 깔려 있다.

## 포트 (NN=03)

`api` 8003 · `web` 8103 · `db` 5403. 레지스트리는 `../PORTS.md`.
NN=00은 backend가 8000이 되어 nst-wiki와 충돌하므로 03을 배정했다.

## 파이프라인 (6단계)

상태머신은 `pending → searching → extracting → reducing → done`(실패 시 `failed`, 예산 소진 시 `paused`).
각 단계는 재진입 가능하게 짜여 있다 — 컨테이너가 언제 죽었다 살아나도 DB 상태만 보고 이어간다.
루프 본체는 `backend/app/services/runner.py::advance()` / `loop()`.

| # | 단계 | 파일 | 비고 |
|---|---|---|---|
| 1 | search | `app/services/search.py` (`collect`, `upsert_papers`) | OpenAlex + KCI 병렬 검색, DOI/title 정규화 후 중복 제거 |
| 2 | filter | `app/services/mapper.py::pending_papers` | 한국 판정·abstract 없는 레코드 제외·추출 캐시 히트 제외 |
| 3 | map | `app/services/mapper.py::build_requests` + `app/clients/gemini_batch.py` | Batch JSONL 제출 → 폴링 → 결과 저장, thinking=low |
| 4 | stats | `app/services/stats.py::compute` | 코드로만 집계, LLM 미사용 |
| 5 | reduce | `app/services/reducer.py::reduce_subfield` | 세부기술별 보고서, thinking=high. 건수가 `REDUCE_GROUP_THRESHOLD`(500) 넘으면 3단 reduce |
| 6 | rollup | `app/services/reducer.py::rollup_field` | 대분류 보고서 합성. **구현은 있으나 호출부는 아직 없음**(초판 범위 밖 — 세부기술 보고서까지만 제공) |

## 캐시 3중 키

| 단계 | 키 | 파일 |
|---|---|---|
| ① 검색 | `hash(query + year + source)` | `app/services/search.py::query_hash` |
| ② 추출 | `papers.paper_key` + `model_ver` | `app/services/mapper.py::model_ver`, `PaperExtraction` |
| ③ 보고서 | `analyses(subfield_id, year)` | `Analysis` 행 자체가 캐시 |

**`model_ver`**(`f"{gemini_model}/{thinking_map}"`)이 바뀌면 이전 추출 캐시는 자동으로 무효화된다 —
`paper_extractions` 조회가 항상 `model_ver == mapper.model_ver()`로 필터링하기 때문에, 모델이나
thinking 레벨을 바꾸면 같은 논문이라도 재추출된다(신규 행으로 쌓이지 덮어쓰지 않음).

## 프리즈 없는 증분 갱신

과거연도를 freeze하지 않는다(색인 지연·online-first 재배정·메타데이터 사후 보정 때문에 "작년 논문"이
올해 새로 잡히는 경로가 있다). `runner.py::enqueue()`가 검색식 해시(`query_hash`)가 바뀐 연도를
자동으로 `pending`으로 되돌려 재실행 대상에 넣고, `is_stale()`이 이를 판정한다.
`analyses.snapshot_at`이 최종 수집 시점, `analyses.query_hash`가 갱신 필요 여부의 근거다.

## 월간 자동 분석 스케줄러

새 컨테이너/라이브러리 없이 `runner.loop()`(30초 주기) 안에서 매 틱마다
`run_scheduled_if_due(db)`를 호출해 "지금이 실행 시각인가"를 확인하는 방식.

**설정은 DB에서 관리한다(재기동 불필요)**: `ScheduleSetting`(테이블 `schedule_settings`, 싱글턴
행 id=1)이 `enabled`/`day`/`hour`/`years_back`을 들고 있고, `runner.get_schedule_settings(db)`가
행이 없으면 `.env`의 `schedule_enabled`/`schedule_day`/`schedule_hour`/`schedule_years_back`을
**초기 기본값**으로 한 번만 seed한다. 이후로는 이 DB 행이 항상 우선한다 — 관리자 화면
`GET/PUT /api/admin/schedule`이 이 행을 읽고 쓴다. `schedule_timezone`만 예외로 DB로 옮기지
않고 `.env` 전용을 유지한다(변경이 드물고, 잘못된 값을 넣으면 `ZoneInfo`가 즉시 실패해
스케줄러 전체가 멈추는 값이라 다른 설정과 리스크가 다르다).

- 조건: `settings.schedule_timezone`(기본 KST) 기준 DB `day`일 `hour`시대(기본 10일 3시 — 1~3일은
  다른 서비스와 OpenAlex 키 공유로 회피)이고, `scheduled_runs.run_month`(예: `"2026-08"`)에 아직
  그 달 행이 없을 때.
- 멱등성: `run_month` unique 제약 + `db.flush()` 후 `IntegrityError`를 잡아 롤백하는 패턴
  (`budget.py::_row`와 동일). 컨테이너가 실행 시각대에 재시작돼 루프가 다시 돌아도 두 번째
  삽입 시도는 여기서 막혀 중복 큐잉되지 않는다.
- 활성(`active=True`) 세부기술 전부 × 당해~(당해−`years_back`)연도를 **`force=True`**로
  `enqueue()`한다 — 실제 검색·추출·보고서 생성은 기존 잡 루프·예산 게이트·batch 슬롯 게이트가
  그대로 처리한다.
- **관리자 "지금 실행"(`POST /api/admin/schedule/run-now` → `runner.run_scheduled_now`)**은
  스케줄 시각 판정(`_is_schedule_due`)을 건너뛰고 즉시 같은 방식으로 큐잉하되, `run_month`에
  `"YYYY-MM-manual-HHMMSSffffff"` 형식(접미사 + 마이크로초)을 써서 그 달의 정기 실행 키
  (`"YYYY-MM"`)와 절대 겹치지 않게 한다 — 겹치면 둘 중 나중에 도는 쪽이 `IntegrityError`로
  막혀 "수동 실행이 정기 실행을 막는다"는 요구를 깬다. `trigger`도 `"manual"`로 남겨(`Analysis`/
  `AnalysisRun`/`ScheduledRun` 모두) 즉흥 실행의 성공률이 정기 스케줄러 통계에 섞이지 않게 한다.
  루프의 자동 재시도 경로가 아니라 사용자가 직접 누르는 단발 액션이라 `IntegrityError` catch로
  멱등성을 보장할 필요는 없다(마이크로초 단위 키라 충돌 가능성도 사실상 없음).
- `GET /api/admin/schedule`의 `history`(`runner.schedule_history`, 최대 12건)는 `ScheduledRun`
  각 행의 `done_count`를 `AnalysisRun.ran_at`이 [이 실행, 시간순 다음 실행) 구간에 든 같은
  `trigger` 건수로 근사한다(`AnalysisRun`에 실행 ID를 연결하는 FK가 없어 정확한 귀속은
  불가능 — `ScheduledRun.ran_at`은 스케줄 타임존 naive, `AnalysisRun.ran_at`은 UTC라 비교 전
  변환 필요). `failed`/`paused`/`in_progress` 건수는 "지금 이 순간" 같은 `trigger`의 `Analysis`
  상태 집계라 같은 `trigger` 중 **가장 최근** 실행 행에만 채우고(`is_current_snapshot=True`),
  더 오래된 행은 이후 실행에 상태가 덮어써졌을 것이므로 0으로 둔다.
- **`force=True`여야 하는 이유**: `enqueue(force=False)`는 이미 `done`이고 `query_hash`가 그대로면
  아무 것도 하지 않는다. 스케줄러가 그렇게 부르면 완료된 세부기술은 매달 건너뛰어져
  "그 사이 새로 등재된 논문을 잡는다"는 스케줄러의 존재 이유가 사라진다. 대신 매번 검색을
  다시 돌리되, 신규 논문이 0건이면 `_do_reduce`가 보고서 재생성을 생략하므로 실질 비용은
  검색분(약 $0.004)에 그친다. 이 동작은 `test_scheduler.py::test_run_scheduled_requeues_already_done_analysis`
  가 고정한다 — 깨지면 스케줄러가 조용히 무력화된 것이다.
- `AnalysisRun`이 매 `done` 도달 시 `trigger`(`manual`|`scheduled`) · 검색/분석 건수를 남긴다.
  OpenAlex의 `from_created_date` 필터가 유료 플랜 전용이라 막혀 있어, 대신 이 실행 이력을
  몇 달 쌓아 "논문이 실제로 얼마나 느는가"를 데이터로 확인할 계획이다(조회 API는 아직 없음).
- `python:3.11-slim`에는 시스템 tz 데이터베이스가 없어 `zoneinfo.ZoneInfo("Asia/Seoul")`가
  실패할 수 있다 — `requirements.txt`의 `tzdata` 패키지로 해결(컨테이너 안에서
  `python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Asia/Seoul'))"`로 확인 가능).

## 재실행 비용 최적화 — 신규 추출 0건이면 보고서 재생성 생략

`runner.py::_do_reduce`는 이번 실행에서 추출 건수(`len(extractions)`)가 이전 `analysis.analyzed_count`
보다 늘지 않았고 `report_md`가 이미 있으면 `reducer.reduce_subfield`(LLM 호출)를 건너뛰고 기존
`report_md`를 유지한다. 통계(`stats_json`)는 인용수 등이 바뀌므로 스킵 여부와 무관하게 항상
다시 계산한다. 실측 재실행 1회 비용 $0.0225 중 보고서 재생성이 $0.0105(약 47%)라 이 스킵으로
$0.0040까지 떨어진다.

**model_ver 변경은 별도로 추적**: `mapper.model_ver()`가 바뀌면(모델 교체·`EXTRACTION_SCHEMA_VERSION`
상향) 같은 논문 집합이 전량 재추출되어 건수가 이전과 같을 수 있다 — `analyzed_count` 비교만으로는
이를 "늘지 않았다"고 오판한다. `analyses.report_model_ver`(마지막 보고서 생성 시점의 model_ver)를
같이 비교해 이 경우를 구분한다. 검색식이 바뀌어(`query_hash` 불일치) `AnalysisPaper` 링크가
비워지는 경로(`enqueue()`)에서는 `analyzed_count`도 함께 0으로 리셋한다 — 그러지 않으면 재검색 후
건수가 옛(더 큰) 값보다 작아 보여 갱신이 잘못 생략될 수 있다.

## OpenAlex 과금

요청 **건당** 과금(반환 레코드 수와 무관). list+filter는 $0.0001, **search 계열(불리언 검색식 포함)은
전부 $0.001**로 감싸도 동일하다. `.env`의 `OPENALEX_SEARCH_COST_USD`는 사전 게이트 판단용 참고값이고,
**실제 과금은 매 응답의 `meta.cost_usd`를 읽어 `app/services/budget.py::record_usage`로 누적한다**
(`app/clients/openalex.py`). 키를 다른 서비스와 공유하므로 잔여 예산을 추정하지 않고
`X-RateLimit-Remaining` 헤더 실측값을 그대로 신뢰한다.

## 모델 import — `app/models/__init__.py`

SQLAlchemy가 FK를 해석하려면 관련 모델 클래스가 전부 import되어 있어야 한다.
`app/models/__init__.py`가 모든 모델(`Field`, `Subfield`, `Analysis`, `AnalysisPaper`, `Paper`,
`PaperExtraction`, `OpenAlexUsage`)을 한 곳에 모아 import하고, `app/main.py`가
`import app.models  # noqa: F401`로 이를 강제한다. **새 모델을 추가하면 이 파일에도 반드시 추가할 것** —
빠뜨리면 Alembic autogenerate가 해당 테이블을 못 보거나 FK 해석이 조용히 깨진다.

## Gemini 클라이언트 — 지연 생성

`app/clients/gemini_sync.py`, `app/clients/gemini_batch.py`는 `genai.Client()`를 모듈 로드 시점이
아니라 실제 호출 시점(`_get_client()`)에 만든다. `GEMINI_API_KEY`가 비어 있으면 `genai.Client()`가
즉시 `ValueError`를 던지는데, 모듈 import 시점에 만들면 키 없이는 컨테이너 자체가 뜨지 못한다.
**키 없이도 `docker compose up`은 성공해야 정상** — 실패하면 이 지연 생성이 깨진 것이니 먼저 의심할 것.

## Batch 요청 JSON — camelCase, systemInstruction은 최상위

`mapper.py::build_requests`가 만드는 batch 요청 바디는 손으로 쓴 dict가 아니라 google-genai SDK 타입
(`types.Content`, `types.GenerateContentConfig` 등)을 `model_dump(by_alias=True, mode="json")`으로
직렬화한 것이다. 와이어 스키마는 **camelCase**이고, **`systemInstruction`은 `contents`/`generationConfig`와
형제로 request 최상위에** 온다(`generationConfig` 안이 아님). `responseMimeType` / `responseSchema` /
`thinkingConfig`는 `generationConfig` 안에 중첩된다. 이 구조는 SDK 소스
(`google/genai/batches.py::_InlinedRequest_to_mldev`, `google/genai/models.py::_GenerateContentConfig_to_mldev`)로
확인한 것이므로, `GenerateContentConfig`를 통째로 덤프하면 `systemInstruction`이 잘못된 위치에 나온다 —
반드시 별도 `Content`로 분리해 조립해야 한다.

## 실검증 상태 (2026-07-19)

실제 API 키로 종단 실행해 확인한 범위. **미검증 항목을 검증된 것처럼 다루지 말 것.**

| 경로 | 상태 |
|---|---|
| OpenAlex 검색·cursor 페이징·비용 실측 기록·KR 필터·abstract 인라인 수신 | ✅ 검증 |
| 예산 게이트, 미리보기 비용 추정 | ✅ 검증 |
| Gemini Batch 제출·폴링·결과 JSONL 파싱, `thinking_level="low"` 수용 | ✅ 검증 |
| reduce(thinking `high`) 보고서 생성 | ✅ 검증 (논문 10건 규모) |
| 잡 상태머신 `pending→searching→extracting→reducing→done`, `/retry` | ✅ 검증 |
| 통계 집계, 모집단 분리 표기(검색 11 / 분석 10 / abstract 미보유 1) | ✅ 검증 |
| **KCI 검색 (국문 검색식 포함)** | ❌ **미검증** — 키 만료(`사용기간이 종료되었습니다.`) |
| 3단 reduce 분기 (`REDUCE_GROUP_THRESHOLD` 초과) | ❌ 미검증 — 실행 규모가 작아 도달 안 함 |
| batch 다중 청크 (1,000건 초과) | ❌ 미검증 — 동일 |
| 대량 검색 시 하드 가드(`AnalysisTooLarge`) 실동작 | ❌ 미검증 — 단위 테스트만 |

### KCI 키가 만료된 상태에서 돌릴 때

`KCI_API_KEY`가 **설정돼 있는데 만료**면 모든 분석이 `failed`로 끝난다(의도된 동작 —
자세한 이유는 README "KCI 실패는 조용히 넘어가지 않는다" 참고). 키를 갱신하기 전까지
OpenAlex만으로 돌리려면 `.env`에서 `KCI_API_KEY`를 **비우고** 컨테이너를 재생성한다:

```bash
# .env: KCI_API_KEY=
docker compose up -d --force-recreate api
```

키를 갱신한 뒤에는 세부기술의 `query_kci`에 **한글 검색식**을 채워야 국내지가 잡힌다.
