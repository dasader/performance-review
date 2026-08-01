# Elsevier 초록 폴백 설계 — 분석 대상 13%p 회복

작성일 2026-08-01. OpenAlex에 초록이 없는 Elsevier 논문의 초록을
ScienceDirect Article Retrieval API로 채워 넣는다.

다국가 확장(`2026-08-01-multi-country-expansion-design.md` §4-3)에서 파생됐지만
**현재의 KR 전용 분석에도 그대로 적용되는 독립 작업**이다. 국가 파라미터화와 순서 의존이 없다.

---

## 1. 근거 — 실측 요약

| 사실 | 실측값 |
|---|---|
| OpenAlex 초록 결측률 | 세부기술·국가별 8.1%~38.5% |
| 결측 중 Elsevier 비중 | **66.8%** (CN 재생에너지 결측 9,053건 중 6,046건) |
| ScienceDirect API 회수율 | **88%** (35/40건, 두 번 실행해 동일 재현) |
| 회수 초록 길이 | 평균 1,940자 (765~5,958) |
| 구독 전용(`openaccess=0`) 논문 | **회수됨** — OA 여부와 무관 |
| 무료 키로 동작 | Scopus Abstract Retrieval은 401, **ScienceDirect Article Retrieval은 200** |

**기대 효과**: 결측률 약 22% × Elsevier 비중 66.8% × 회수율 88% ≈ **전체 논문의 약 13%**가
분석 대상으로 복귀한다(분석률 약 78% → 약 91%).

전문(full text)은 구독이 필요하지만 **초록은 무료 등록 키로 열려 있다.** 우리가 필요한 것은
초록뿐이라 구독·insttoken·IP 대역 문제가 전부 비껴간다.

### ⚠ 선행 조건 — 이용약관 확인

회수한 초록은 map 단계에서 **Gemini API로 전송된다.** Elsevier API 이용약관이 제3자 전송을
어떻게 다루는지는 **미확인**이다. 기술적으로 되는 것과 써도 되는 것은 다르므로,
확인 전에는 키를 넣지 않는다. 설계상 **키가 비어 있으면 기능 전체가 꺼지므로**
코드는 먼저 머지해도 안전하다(§7).

---

## 2. 어디에 넣는가

파이프라인에서 초록이 쓰이는 지점은 하나다: `mapper.pending_papers`가 `p.abstract`가 빈
논문을 추출 대상에서 제외한다. 따라서 **그 앞이면 어디든 되고, 가장 이른 지점이 가장 싸다.**

```
search.collect()
  ├ openalex.search()      ← 초록이 여기서 비어 온다
  ├ kci.search()
  ├ merge_papers()
  └ _fill_missing_abstracts()   ← ★ 여기에 넣는다
     ↓
runner._do_search → search.upsert_papers()   (dict의 abstract가 그대로 저장됨)
     ↓
mapper.pending_papers()   (abstract 있는 논문만 남김 — 자동으로 늘어남)
     ↓
stats.compute()           (no_abstract_count가 자동으로 줄어듦)
```

`merge_papers` 직후에 dict의 `abstract`를 채우면 **하류 코드는 한 줄도 바뀌지 않는다.**
`upsert_papers`는 이미 "값이 더 채워진 경우에만 덮어쓴다"는 규칙이라 회수된 초록이
그대로 저장되고, 이후 재실행에서 빈 값으로 되돌려지지도 않는다.

---

## 3. 모듈 구조

기존 계층(clients = 순수 API 호출, services = 오케스트레이션 + DB)을 그대로 따른다.

### `app/clients/elsevier.py` (신규, 순수 클라이언트)

```python
async def fetch_abstract(doi: str, *, client: httpx.AsyncClient) -> str | None
```

- `GET https://api.elsevier.com/content/article/doi/{doi}`,
  헤더 `X-ELS-APIKey`, `Accept: application/json`
- 응답에서 `full-text-retrieval-response.coredata.dc:description`을 꺼낸다.
- **어떤 경우에도 예외를 던지지 않는다.** 실패는 전부 `None`.
- 429는 `Retry-After`(없으면 2초) 후 **한 번만** 재시도하고 그래도 실패하면 `None`.
  무한 백오프를 두지 않는 이유: 못 받아도 다음 달 실행에서 다시 시도되므로
  이번 실행을 붙잡고 있을 이유가 없다.

`_http.get_with_retry`를 쓰지 않는다 — 그 함수는 4xx에서 `RuntimeError`를 던지는데,
여기서는 **404가 정상적인 결과**(ScienceDirect 미수록, 실측 40건 중 2건)이기 때문이다.

### `app/services/search.py` (수정)

```python
async def _fill_missing_abstracts(db, papers: list[dict], *, client) -> int
```

`collect()` 안에서 `merge_papers` 직후 한 줄로 호출한다.

```python
merged = merge_papers(oa.papers, kci_papers)
await _fill_missing_abstracts(db, merged, client=client)
```

---

## 4. 대상 선별 — 세 조건을 모두 만족할 때만 호출

1. `paper["abstract"]`가 비어 있다.
2. `paper["doi"]`가 **`10.1016/`로 시작**한다. ScienceDirect는 Elsevier 콘텐츠만
   호스팅하므로 다른 prefix는 확정적으로 404다 — 쿼터를 버릴 이유가 없다.
3. **DB의 `papers` 행에 이미 초록이 없다.**

3번이 중요하다. OpenAlex는 매달 같은 논문을 여전히 초록 없이 돌려주므로, DB를 보지 않으면
**이미 회수해 저장해 둔 논문을 매달 다시 받아온다**(KR 기준 연 36,000콜 낭비).
`upsert_papers`가 이미 쓰는 것과 같은 방식으로 `paper_key`를 한 번에 조회해 걸러낸다.

```python
keys = [p["paper_key"] for p in candidates]
already = {r.paper_key for r in db.query(Paper.paper_key)
           .filter(Paper.paper_key.in_(keys), Paper.abstract != "")}
```

---

## 5. 실패 정책 — KCI와 정반대로 간다

**어떤 실패도 분석을 멈추지 않는다.** 개별 논문 실패는 건너뛰고, 전체 실패(키 만료·서비스
장애)도 로그만 남기고 통과시킨다.

KCI는 반대다 — 키가 만료되면 분석 전체가 `failed`로 끝난다(README "KCI 실패는 조용히
넘어가지 않는다"). **의도적으로 다르게 설계하는 이유**: KCI는 *검색 소스*라 빠지면
모집단 자체가 조용히 줄어들어 결과가 틀리지만, Elsevier 폴백은 *보강 단계*라 빠져도
결과가 틀리지 않고 그저 예전만큼만 분석될 뿐이다. 이미 `no_abstract_count`가
빠진 만큼을 정확히 드러낸다.

실행마다 요약 한 줄을 남겨 동작 여부를 확인할 수 있게 한다:

```
[초록회수] 대상 1,102건 (DB 기보유 제외 후) → 성공 970 / 404 78 / 초록없음 41 / 오류 13
```

---

## 6. 재시도·캐시 — 새 컬럼을 두지 않는다

실패한 논문에 "시도했음" 표시를 남기지 않는다. 다음 달 실행에서 그냥 다시 시도한다.

- 영구 실패는 회수 대상의 약 12%다. KR 기준 연 3,000건 × 12% ≈ 360건이 매달 재시도된다
  → 연 4,320콜. **주당 50,000건 한도의 0.2%**라 아낄 가치가 없다.
- 오히려 재시도가 이득이다. Elsevier가 나중에 초록을 채우면 자동으로 회수된다.
- 컬럼을 두면 마이그레이션 + "언제 만료시킬 것인가"라는 정책이 새로 생긴다.

`ponytail:` 주석으로 이 판단과 상한을 코드에 남긴다.

---

## 7. 동시성·설정

실측(지연 건당 0.31s, 문서상 한도 10 req/s):

| 동시성 | 처리량 | 429 |
|---:|---:|---|
| 1 | 3.0 req/s | 없음 |
| **3** | **9.5 req/s** | **없음** |
| 5 | 14.6 req/s | 20건 중 1건 발생 |

→ **`elsevier_concurrency = 3`**. 문서상 한도(10 req/s)에 가장 근접하면서 429가 나지 않는 값이다.
`asyncio.Semaphore(3)` + `asyncio.gather`로 구현한다.

소요 시간: 1,100건 기준 약 2분. 월 1회 배치라 수용 가능하고, 증분 실행에서는
대부분 DB 기보유로 걸러져 훨씬 짧다.

```python
# app/config.py
elsevier_api_key: str = ""      # 비어 있으면 회수 단계를 통째로 건너뛴다
elsevier_concurrency: int = 3
```

**키가 비어 있으면 즉시 반환한다.** Gemini 클라이언트 지연 생성과 같은 원칙 —
키 없이도 컨테이너는 떠야 하고 기존 동작이 그대로여야 한다. 이 덕분에 약관 확인 전에
코드를 머지해도 안전하다.

---

## 8. 테스트

기존 테스트가 인메모리 sqlite로 도는 구조를 그대로 쓴다(HTTP는 전부 목).

`backend/tests/test_elsevier.py` (신규)
- `dc:description`을 정상 파싱한다.
- 404 / 200-but-empty / 네트워크 오류 / 잘못된 JSON에서 **`None`을 반환하고 예외를 던지지 않는다.**
- 429 → `Retry-After` 후 1회 재시도하고, 재차 429면 `None`.

`backend/tests/test_search.py` (추가)
- `elsevier_api_key`가 비면 API를 **한 번도 부르지 않는다**(기존 동작 보존).
- `10.1016/`이 아닌 DOI, DOI가 없는 논문, 이미 초록이 있는 논문은 부르지 않는다.
- **DB에 이미 초록이 있는 `paper_key`는 부르지 않는다** (§4-3 회귀 방지 — 이게 깨지면
  쿼터가 조용히 10배로 샌다).
- 개별 논문이 실패해도 `collect()`가 정상 반환한다.

---

## 9. 문서

- `CLAUDE.md` — 파이프라인 표의 1단계(search) 항목에 회수 단계를 한 줄 추가하고,
  "실패해도 넘어간다"가 KCI와 반대라는 점과 그 근거를 명시한다.
- `.env.example` — `ELSEVIER_API_KEY=`(빈 값)와 약관 확인이 선행 조건이라는 주석.
- 실검증 상태 표에 `Elsevier 초록 회수` 행을 추가한다(설계 시점 ❌ 미검증 —
  종단 실행으로 확인 후 갱신).

---

## 10. 의도적으로 하지 않는 것

- **`recovered_count`를 `stats_json`에 넣기** — `no_abstract_count`가 이미 줄어드는 것으로
  효과가 드러나고, 동작 확인은 §5의 로그 한 줄이면 된다. 통계 스키마를 넓힐 이유가 없다.
- **Scopus Abstract Retrieval 병용** — 기관 구독 없이는 401이다(실측). 같은 초록을
  ScienceDirect가 무료로 주므로 쓸 이유가 없다.
- **Europe PMC 폴백 동시 도입** — 바이오 한정이고 Elsevier 폴백이 이미 대부분을 덮는다.
  이것을 넣고 나서 남는 구멍을 보고 판단한다.
- **회수 실패 논문의 재시도 억제 컬럼** — §6.
- **초록 원문 재배포** — 회수한 초록은 추출 입력으로만 쓰고 화면·보고서에 원문을
  노출하지 않는다(현행과 동일 — 보고서는 추출된 요약만 담는다).
- **전용 배치·큐** — 검색 단계 안에서 끝난다. 기존 잡 루프·상태머신을 건드리지 않는다.

---

## 11. 변경 요약

| 파일 | 변경 |
|---|---|
| `app/clients/elsevier.py` | 신규 — `fetch_abstract()` 한 함수 |
| `app/services/search.py` | `_fill_missing_abstracts()` 추가 + `collect()`에 한 줄 |
| `app/config.py` | `elsevier_api_key`, `elsevier_concurrency` |
| `tests/test_elsevier.py` | 신규 |
| `tests/test_search.py` | 케이스 추가 |
| `CLAUDE.md` · `.env.example` | 문서 |

**마이그레이션 없음. 모델 변경 없음. 프론트 변경 없음. 상태머신 변경 없음.**
