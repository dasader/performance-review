# CLAUDE.md — performance-review

전략기술 분야별 논문성과 분석 서비스. 자세한 설계는
`docs/superpowers/specs/2026-07-18-strategic-tech-paper-analysis-design.md` 참고.

## 실행 명령어

```bash
# 스택 기동 — api 컨테이너 entrypoint(docker-entrypoint.sh)가 uvicorn 전에
# alembic upgrade head를 자동 실행한다(M15, 수동 실행 불필요). 현재 head: 0015
# 프론트엔드는 web 컨테이너 안에서 빌드돼 nginx가 정적 파일로 서빙한다 —
# frontend/를 고쳤으면 반드시 --build로 다시 올려야 화면에 반영된다.
docker compose up -d --build

# .env를 고친 뒤에는 restart가 아니라 재생성해야 한다.
# `docker compose restart`는 env_file을 다시 읽지 않아 옛 값이 그대로 남는다.
docker compose up -d --force-recreate api

# 로그
docker compose logs -f api

# 테스트 (컨테이너 밖, 로컬 venv)
cd backend && ./.venv/bin/python -m pytest
cd backend && ./.venv/bin/python -m pytest tests/test_runner.py::test_step_labels_are_korean  # 단건
cd backend && ./.venv/bin/python -m pytest -k roadmap                                    # 이름 매칭

# 프론트엔드 테스트 (vitest, 순수 함수 대상 — jsdom 등 브라우저 환경 없음)
cd frontend && npm test
cd frontend && npx vitest run src/lib/reportMarkdown.test.ts   # 단건
cd frontend && npm run lint     # oxlint
cd frontend && npm run build    # tsc -b + vite build — 타입 오류는 여기서만 잡힌다

# 마이그레이션 추가
docker compose exec api alembic revision --autogenerate -m "설명"
```

테스트는 반드시 `backend/.venv`를 쓴다 — 시스템 파이썬에는 의존성이 안 깔려 있다.
백엔드 린터는 없다(ruff 등 미설치). 프론트만 oxlint를 쓴다.

**백엔드 테스트는 인메모리 sqlite로 돈다** — `test_api.py`가 `StaticPool`로 엔진을 만들고
`app.dependency_overrides[get_db]`를 갈아끼운다. DB에 접근하는 **미들웨어·의존성을 새로 추가하면
`SessionLocal`을 직접 부르지 말고 `request.app.dependency_overrides`를 먼저 확인**해야 한다
(`main.py::track_visitor`가 그 패턴). 안 그러면 실제 `DATABASE_URL`(테스트에 없는 postgres)로
붙으려다 공개 API 테스트가 전부 깨진다.

## 포트 (NN=03)

`api` 8003 · `web` 8103 · `db` 5403. 레지스트리는 `../PORTS.md`.
NN=00은 backend가 8000이 되어 nst-wiki와 충돌하므로 03을 배정했다.

## 프론트엔드 (React 19 + Vite + Tailwind + react-router)

프론트엔드 디자인은 `/home/dev/code/web-design/DESIGN.md` 체계를 따른다.
토큰(색·활자·간격·모서리)의 단일 출처는 `frontend/tailwind.config.js`이고,
컴포넌트 계약(`.btn*` · `.input` · `.banner*` · `.switch` · `.tbl-head` · `.table-scroll`)은
`frontend/src/index.css`의 `@layer components`에 모여 있다. **화면에서 로컬 클래스로
비슷한 것을 다시 조립하지 말고 이 둘을 고친다** — 예전에 그렇게 버튼 클래스가 8종,
입력 높이가 32/40px 두 종으로 갈렸다. 기계 점검은 `python3 /home/dev/code/web-design/check.py`.

**레이아웃 간격은 6값뿐이다**(4/8/12/16/24/40 = Tailwind `1·2·3·4·6·10`).
`src/lib/spacing.test.ts`가 `.tsx` 전체를 훑어 이를 고정하므로 `npm test`에서 바로 걸린다 —
간격은 "여기는 좁으니까 `py-2.5`로" 같은 판단이 한 줄씩 쌓여 조용히 무너지는 항목이고,
실제로 55곳이 그렇게 어긋나 있었다. **조작물 내부 여백은 예외**(버튼 좌우 14px, 입력
좌우 10px 등 — 정해진 높이를 맞추는 치수라 씨앗 CSS도 같은 값을 쓴다). 그 예외는
`index.css` 버튼 주석 한 곳에 근거와 함께 모여 있고, 테스트는 `.tsx`만 본다.

> Tailwind 프로젝트라 `check.py`의 11항 중 2항(**버튼 배경 채움**, **결측 `—`**)은
> 소스만 봐서는 항상 FAIL로 나온다 — 전자는 `@apply`라 소스 CSS에 `background:`가 없고,
> 후자는 `src/**/*.html`을 찾는데 `—`가 `.tsx`에 있기 때문이다. **빌드된 CSS와 렌더된
> DOM으로 재면 둘 다 PASS**다(실측 확인). 소스 기준 9/11이 정상이니 이 둘을 쫓아
> 코드를 바꾸지 말 것.

라우트는 `src/App.tsx` 한 곳에 모여 있다. 공개 화면은 분야 목록(`/`) · 분야 상세
(`/fields/:id`) · 분야 보고서 전용 페이지(`/fields/:id/report/:year`, `/roadmap-check/:year`) ·
세부기술 보고서(`/analyses/:id`, `/subfields/:id/:year`)이고, 관리자는 `/admin` 하나다.

- **관리자 인증**: `useAdminKey.ts`가 `sessionStorage`에 키 하나를 보관한다(계정 체계 없음,
  탭을 닫으면 사라짐). `api.ts`의 `ApiError`가 `status`를 들고 다니는 이유가 이것 —
  401이면 저장된 키를 지우고 인증 화면으로 되돌린다. 새 admin 호출부도 같은 판별을 써야 한다.
- **`npm run dev`(5173)는 API가 안 붙는다.** `vite.config.ts`에 proxy 설정이 없고 `api.ts`의
  `BASE`가 `/api`라 5173에서는 프론트만 뜬다. 붙이려면
  `VITE_API_BASE=http://localhost:8003 npm run dev`로 띄우고 `.env`의 `CORS_ORIGINS`에
  `http://localhost:5173`을 추가한다(`allow_origins`가 8103으로 좁혀져 있다).
  그럴 이유가 없으면 그냥 docker의 8103을 쓴다.
- **버전**: `frontend/package.json`의 `version`이 단일 출처다 — `vite.config.ts`가 빌드 타임에
  `__APP_VERSION__`으로 주입해 푸터에 표시한다. 프론트를 고치면 변경 성격에 맞춰(기능 추가 minor,
  수정 patch) 이 값을 함께 올린다.
- **넓은 표는 `.table-scroll`로 감싼다**(`overflow-x-auto` 단독 금지). 375px에서 관리자
  세부기술 표가 스크롤 컨테이너 안에 있는데도 문서 전체가 859px까지 가로로 밀렸다 —
  `body`·`main`·`section`은 전부 360px로 정상인데 `documentElement`만 표 폭을 따라갔고,
  `html`/`body`에 `overflow-x: clip`·`hidden`을 걸어도 막히지 않았다. `.table-scroll`이
  얹는 **`contain: paint`**가 그것을 막는다. LLM이 만드는 마크다운 표도 열 수를 우리가
  정하지 못하므로 `lib/prose.tsx::MARKDOWN_COMPONENTS`가 같은 컨테이너로 감싼다
  (`<table>`에 `display:block`을 주는 흔한 우회법은 쓰지 않는다 — `thead`의
  `table-header-group`이 깨져 인쇄에서 머리행이 페이지마다 반복되지 않는다).

## 파이프라인 (6단계)

상태머신은 `pending → searching → extracting → reducing → done`(실패 시 `failed`, 예산 소진 시 `paused`).
각 단계는 재진입 가능하게 짜여 있다 — 컨테이너가 언제 죽었다 살아나도 DB 상태만 보고 이어간다.
루프 본체는 `backend/app/services/runner.py::advance()` / `loop()`.

| # | 단계 | 파일 | 비고 |
|---|---|---|---|
| 1 | search | `app/services/search.py` (`collect`, `upsert_papers`) | OpenAlex + KCI 병렬 검색, DOI/title 정규화 후 중복 제거 |
| 2 | filter | `app/services/mapper.py::pending_papers` | abstract 없는 레코드 제외·추출 캐시 히트 제외. **한국 판정은 여기 없다** — OpenAlex 서버측 `country_code:KR` 필터가 이미 걸러 온다 |
| 3 | map | `app/services/mapper.py::build_requests` + `app/clients/gemini_batch.py` | Batch JSONL 제출 → 폴링 → 결과 저장, thinking=low |
| 4 | stats | `app/services/stats.py::compute` | 코드로만 집계, LLM 미사용 |
| 5 | reduce | `app/services/reducer.py::reduce_subfield` | 세부기술별 보고서, thinking=high. 건수가 `REDUCE_GROUP_THRESHOLD`(500) 넘으면 3단 reduce |
| 6 | rollup | `app/services/reducer.py::rollup_field` | 대분류 보고서 합성. 잡 루프가 아니라 관리자가 직접 호출한다(`build_field_report`) |

### 분야 단위 보고서 두 종류 — 잡 루프로 큐잉 처리

1~6단계 세부기술 파이프라인(검색·추출)과 달리 분야 보고서는 입력이 이미 완성된
세부기술 보고서라 LLM 1콜(약 10~17초)로 끝난다. 그래도 **동기 응답이 아니라 큐잉**한다:
관리자가 "생성"을 누르면 행을 `status="pending"`으로만 만들고 즉시 응답하고(화면은 그
자리에서 폴링), 실제 LLM 호출은 `runner.advance_field_reports(db)`가 **한 틱(30초)에 하나씩**
처리한다. 일괄로 수십 건을 큐잉해도 API가 동시에 얻어맞지 않게 — 세부기술 분석 잡과
자원을 나눠 쓰는 rate-limit 철학과 일치(실측: 일괄 10건이 30초당 하나씩 순차 완료).

`enqueue_*`(검증+pending 큐잉)와 `process_*`(실제 LLM 호출)을 나눈 이유: 검증(분야 존재·
세부기술 보고서 유무·로드맵 유무)을 **큐잉 시점**에 해 관리자가 즉시 404/409를 받게 하고,
생성은 잡 루프가 나중에 한다. `_process_report`가 예외를 흡수해 그 행만 `failed`로 남기므로
한 건의 실패가 루프 전체를 멈추지 않는다(세부기술 잡 `advance`와 대칭).

| 보고서 | 큐잉 / 처리 | 캐시 | 생성 |
|---|---|---|---|
| 분야 종합 | `reducer.enqueue_field_report` / `process_field_report` | `field_reports(field_id, year)` | `POST /api/admin/fields/{id}/report?year=` |
| 로드맵 이행 점검 | `reducer.enqueue_roadmap_check` / `process_roadmap_check` | `roadmap_checks(field_id, year)` | `POST /api/admin/fields/{id}/roadmap-check?year=` |

`field_reports`·`roadmap_checks`는 `status`(pending|done|failed)+`error` 컬럼을 갖는다(migration
`0015`). **재생성 시 기존 `report_md`는 그대로 두고 `status`만 pending으로 되돌린다** — 처리가
끝나기 전까지 이전 보고서를 계속 보여주기 위해서다.

**일괄 실행**: `POST /api/admin/field-reports/run-all?year=&kind=report|roadmap-check`가 당해연도
전체 분야를 큐잉한다. 검증에 걸리는 분야(세부기술 보고서 없음·로드맵 미등록)는 조용히
건너뛴다 — 하나가 막혀 전체가 실패하면 안 되므로 `enqueue_*`의 예외를 잡아 skip한다.
관리자 "분야 보고서" 탭(`GET /api/admin/field-reports?year=`)이 분야별 상태를 한 표로 보여준다.

**둘을 한 보고서로 합치지 않은 이유**: 로드맵이 없는 분야도 종합 보고서는 쓸 수 있어야
하고, 로드맵만 개정됐을 때 점검만 다시 돌릴 수 있어야 한다.

공개 조회는 캐시만 읽는다(`GET /api/fields/{id}/report`·`/roadmap-check`). 행 자체가 없을 때만
404이고, **pending/failed도 그대로 내려준다**(화면이 status로 폴링·경고를 판단).
**로드맵 원문은 공개 API로 내려주지 않는다** — 비공개 판본일 수 있고, 화면에 필요한 건
점검 결과이지 원문이 아니다.

**세부기술 첨부**: 분야 종합 전용 페이지(`/fields/{id}/report/{year}?withSub=1`)에서 "세부기술
보고서 포함" 토글을 켜면 `GET /api/fields/{id}/subfield-reports?year=`로 하위 세부기술 보고서
본문을 받아 종합보고서 뒤에 이어붙인다(인쇄 시 `break-before-page`로 세부기술마다 새 페이지).
각 본문은 `stripLeadingH1`(프론트)로 자체 H1을 걷어낸다 — 화면이 제목을 붙이는 것과 같은 이유.

### 로드맵 전수 점검 — `goal_count`를 코드로 세어 프롬프트에 주입

`reducer.count_goal_rows()`가 로드맵 마크다운의 표 본문 행 수를 세고,
`ROADMAP_CHECK_INSTRUCTION`의 `{goal_count}`에 박아 "당신의 표도 정확히 N행이어야 한다"고
지시한다. **이 숫자를 빼고 "모든 목표를 빠짐없이"라고만 쓰면 모델이 여러 단계를 한 행으로
뭉갠다** — 실측으로 65행짜리 로드맵이 19행(29%)으로 줄었고, 줄어든 보고서는 "빠진 목표가
없다"로 오독된다. 숫자를 넣자 65/65가 나왔다. 문구를 약화시키지 말 것.

생성 후에도 `count_goal_rows(report_md)`로 다시 세어 `checked_count`에 남긴다.
`goal_count`와 다르면 조회 응답의 `incomplete=true`로 화면에 경고가 뜬다 — 보고서는
버리지 않고 남기되 재생성 여부는 관리자가 판단한다.

로드맵 저장(`PUT /api/admin/fields/{id}/roadmap`)은 `goal_count == 0`이면 **422로 거부**한다.
표가 아닌 줄글을 넣으면 전수 점검 강제가 통째로 무력화되는데, 그 사실이 보고서 생성
시점까지 드러나지 않으면 원인을 찾기 어렵다.

**판정 4단계를 합치지 말 것**: `데이터 없음`(논문은 분석했으나 근거가 없다)과
`분석 범위 밖`(그 중점기술의 세부기술 분석 자체를 아직 안 돌렸다)은 후속 조치가 다르다.

### 논문 데이터의 원리적 한계 — 로드맵 후반 단계는 답할 수 없다

실측(반도체 2026, 세부기술 6건 × 목표 65행): 로드맵 1단계는 관련 연구 확인 77%인데
3단계 이상은 22%로 떨어진다. 후반 단계 목표가 대부분 *양산성·가격 경쟁력·자급률·실증*
(웨이퍼당 $500, 자급도 30%, 수입의존도 95%→50%)이고 **논문에는 그런 내용이 실리지 않기
때문**이다. 검색식이 좁아서가 아니다.

그래서 프롬프트에 "### 4. 이 점검의 한계" 절을 강제해, 그런 목표가 `데이터 없음`으로
표기된 것이 연구 부진을 뜻하지 않는다는 점을 보고서 자체가 밝히게 했다. 빼면 읽는 사람이
`데이터 없음`을 "연구가 부진하다"로 오독한다.

### 로드맵 원문은 Gemini API로 나간다

`check_roadmap`이 원문을 프롬프트에 그대로 실어 보낸다. 관리자 화면(`RoadmapEditor`)에
이 사실을 명시하고, 비공개 판본인지는 관리자가 판단한다. **임베딩을 로컬화해도 이 문제는
해결되지 않는다** — 최종 종합을 외부 모델이 하는 한 원문은 프롬프트로 나간다.
외부로 내보낼 수 없는 판본을 다뤄야 하면 `check_roadmap`이 부르는 `gemini_sync.generate`를
로컬 모델 클라이언트로 분기하면 되고, 프롬프트·전수 점검 검증·저장 구조는 그대로 쓸 수
있다. 다만 **65행 전수 점검을 로컬 모델이 지켜내는지는 별도 검증이 필요하다**(미검증).

RAG/임베딩은 쓰지 않는다. 전수 대조는 "목표가 몇 개인지 알고 그 개수를 채우는" 작업이라
top-k 검색과 애초에 맞지 않고, 로드맵 전문(13KB) + 세부기술 보고서 6건이 합쳐도 57KB라
컨텍스트에 여유롭게 들어간다.

### reduce 입력의 `[세부기술: 이름 / 연도]` 헤더

`reduce_subfield`는 LLM 입력 맨 앞에 이 헤더를 붙이고, `REDUCE_INSTRUCTION`이 이를 근거로
H1 제목(`# {이름} {연도}년 성과 분석 보고서`)을 고정하게 한다. **빼면 모델이 본문 내용만 보고
제목을 새로 지어내 목록 화면의 세부기술명과 어긋난다** — 실측으로 "재생에너지" 분석의 보고서
제목이 "에너지 변환 및 자원 순환 공학"으로 나왔다. 3단 reduce는 최종 통합 입력이 중간 요약뿐이라
세부기술명이 아예 사라져 더 크게 어긋나므로, 단일·3단 **양쪽 호출 모두**에 붙여야 한다
(`test_reducer.py::test_three_tier_final_call_still_names_the_subfield`가 3단 쪽을 고정한다).

## 세부기술 체계 — 국가전략기술 제1호 개정안

`fields`/`subfields`는 개정안의 **10대 분야 55개 중점기술**을 그대로 심은 것이다(migration
`0010`, 검색식 원본도 그 파일의 `FIELDS`/`SUBFIELDS` 상수에 있다). 검색식은 OpenAlex
`title_and_abstract.search` 전용 영문 불리언이고, `query_kci`는 전부 NULL이라 KCI에도 같은
영문식이 쓰인다 — 키 갱신 후 국문 검색식을 채워야 국내지가 잡힌다.

`0010`은 분야·세부기술이 전부 새 id를 받으므로 기존 분석·보고서와 `paper_extractions`를 함께
지운다. **추출 캐시는 `subfield_id`에 묶여 있어**(`uq_extraction`) 세부기술을 교체하면 어차피
히트하지 않는다 — `papers`(검색 캐시)만 보존된다.

검색식을 고칠 때는 `0010`을 편집하지 말고 새 마이그레이션에서 UPDATE한다(`0011`이 차세대 고성능
센싱을 1,051건 → 259건으로 좁힌 예). `test_strategic_tech_seed.py`가 개수·괄호 균형과
`_sanitize_query` 통과 여부(콤마·파이프가 섞이면 검색식이 조용히 쪼개진다)를 고정하므로,
새 검색식을 추가하면 그 테스트에도 함께 물려야 한다.

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
  나중에 이 데이터로 **주기를 조정**할 때 볼 지표는 목적에 따라 다르다:
  - **`AnalysisRun.searched_count`의 연속 실행 간 증가분** — 같은 `analysis_id`의 실행을 시간순으로
    비교해 "이 연도에 새로 검색되는 논문이 실제로 몇 건씩 늘어나는가"를 직접 보여준다. **주기 조정에
    쓸 지표는 이쪽.**
  - **`AnalysisRun.new_papers`** — "이번 실행에서 LLM을 돌려 실제로 추출한 논문 수"(=비용이 발생한
    건수, `Analysis.extracted_this_run`을 `_do_extract`가 누적하고 `_do_reduce`가 옮겨 적음). 비용
    신호에 가깝고, `model_ver`가 바뀌면 검색 결과가 하나도 안 늘어도 전량 재추출되어 값이 커진다 —
    "논문이 얼마나 늘었는가"의 대리 지표로 쓰면 안 된다.
  - **주의**: `analysis_runs` 초기 6개 행(2026-07-19 이전, `new_papers`가 대부분 0)은 `new_papers`를
    `new_count - prior_analyzed_count`(총계의 차이)로 계산하던 버그 시절 값이라, model_ver 변경으로
    전량 재추출된 경우에도 0으로 남아 있다(실측: analysis 4 — 양자컴퓨팅 2026, 스키마 v1→v2로 10건
    전량 재추출됐는데 `new_papers=0`). 이 버그를 고치면서(`extracted_this_run` 컬럼, migration 0009)
    기존 6개 행은 **소급 정정하지 않았다** — 근거가 없어서다. 이 행들의 `new_papers`는 신뢰하지 말 것.
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

## 분석 삭제 정책 — `DELETE /api/admin/analyses/{id}`

분석(보고서) 삭제는 `Analysis`/`AnalysisPaper`/`AnalysisRun`(보고서·통계·링크·이력)만 지우고
`papers`/`paper_extractions`는 절대 건드리지 않는다 — 추출 결과는 LLM 비용을 들여 만든 캐시라
다른 세부기술·연도와 공유되기 때문이다(재실행 시 캐시 히트로 추출 비용 없이 복원됨).
진행 중(`ACTIVE_STATES`)인 분석은 409로 삭제를 거부한다(batch가 이미 제출됐을 수 있어 고아
상태 방지). 세부기술 삭제(`DELETE /api/admin/subfields/{id}`)는 분석 이력이 하나라도 남아
있으면 409로 막힌다 — 개별 분석을 먼저 지워야 막다른 길 없이 세부기술도 지울 수 있다.

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
| 3단 reduce 분기 (`REDUCE_GROUP_THRESHOLD` 초과) | ✅ 검증 (재생에너지 2026, 추출 703건 → 9개 그룹) |
| 3단 reduce의 그룹 재분할 (`{유형} (n)`) | ❌ 미검증 — 한 성과유형이 500을 넘은 적이 없음 |
| batch 다중 청크 (1,000건 초과) | ❌ 미검증 — 703건이 단일 청크로 처리됨 |
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
