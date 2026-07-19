# CLAUDE.md — performance-review

전략기술 분야별 논문성과 분석 서비스. 자세한 설계는
`docs/superpowers/specs/2026-07-18-strategic-tech-paper-analysis-design.md` 참고.

## 실행 명령어

```bash
# 스택 기동
docker compose up -d --build
docker compose exec api alembic upgrade head   # 현재 head: 0002

# 로그
docker compose logs -f api

# 테스트 (컨테이너 밖, 로컬 venv)
cd backend && ./.venv/bin/python -m pytest

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
