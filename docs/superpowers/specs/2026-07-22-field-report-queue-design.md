# 분야 보고서 큐잉 · 일괄 실행 · 세부기술 첨부 설계

작성일: 2026-07-22 · 상태: 구현 완료(migration 0015, frontend 0.17.0)

## 배경

분야 종합 보고서(`FieldReport`)와 로드맵 이행 점검(`RoadmapCheck`)은 세부기술 보고서를
LLM 1콜로 합성한 결과다. 기존에는 관리자가 "생성"을 누르면 **동기 응답**으로 처리했다
(종합 10초 / 로드맵 점검 17초). 세 가지 문제가 있었다:

1. 클릭하면 응답이 올 때까지 화면이 블로킹된다 — "화면에 머물러야 한다"는 요구와 어긋남.
2. 여러 분야를 한 번에 생성할 방법이 없다.
3. 분야 종합보고서를 하위 세부기술 보고서와 함께 출력(PDF)할 수 없다.

## 결정 사항 (사용자 확인)

| 항목 | 결정 |
|---|---|
| 큐잉 인프라 | 기존 잡 루프(`runner.loop`) 재사용 |
| 로드맵 점검 | 종합과 함께 큐잉화 |
| 세부기술 첨부 | 전체보기 페이지 토글(URL 쿼리) |
| 일괄 실행 대상 | 당해연도만 |

## 아키텍처

### 1. 큐잉 — 상태 컬럼 + 잡 루프

`field_reports`·`roadmap_checks`에 `status`(pending|done|failed)+`error` 컬럼 추가
(migration `0015`, 기존 행은 `done`으로 채움).

```
관리자 "생성" 클릭
  → enqueue_*(db, field_id, year): 검증 후 status=pending 행 upsert, 즉시 응답
  → 화면은 그 자리에서 status를 폴링(4초 간격)
runner.loop() 30초 틱
  → advance_field_reports(db): pending 중 가장 오래된 하나를 처리
  → process_*(db, row): LLM 호출 → report_md 채우고 status=done
  → 실패 시 _process_report가 흡수해 status=failed + error
화면
  → status=pending이면 폴링 지속, done되면 갱신, failed면 경고
```

**핵심 판단 — 한 틱에 하나씩.** 일괄로 수십 건을 큐잉해도 루프가 30초마다 하나만
처리한다. 한 틱에 전부 부르면 루프가 수 분 블로킹돼 세부기술 분석 잡까지 밀리고 RPM
버킷도 압박받는다. 느리지만(10건이면 5분) 기존 rate-limit 철학과 일치.
실측: 일괄 10건이 30초당 하나씩 순차 완료.

**enqueue / process 분리.** 검증(분야 존재·세부기술 보고서 유무·로드맵 유무)은 큐잉
시점에 해 관리자가 즉시 404/409를 받는다. `_process_report`가 예외를 흡수해 그 행만
failed로 남기므로 한 건의 실패가 루프를 멈추지 않는다(세부기술 잡 `advance`와 대칭).

**재생성 시 옛 본문 유지.** status만 pending으로 되돌리고 `report_md`는 그대로 둔다 —
처리가 끝나기 전까지 이전 보고서를 계속 보여준다.

### 2. 일괄 실행

- `POST /api/admin/field-reports/run-all?year=&kind=report|roadmap-check` — 당해연도 전체
  분야를 큐잉. 검증에 걸리는 분야(세부기술 보고서 없음·로드맵 미등록)는 `enqueue_*`의
  예외를 잡아 조용히 skip(하나가 막혀 전체가 실패하면 안 되므로).
- `GET /api/admin/field-reports?year=` — 분야별 종합/점검 상태 현황(관리자 탭 표).
- 관리자 화면에 **"분야 보고서" 탭** 추가: 일괄 생성 버튼 2개 + 현황 표. pending이 있으면
  5초 간격으로 현황을 다시 읽어 진행을 보여준다.

### 3. 세부기술 첨부

- `GET /api/fields/{id}/subfield-reports?year=` — 완성된 세부기술 보고서 본문 목록.
- 분야 종합 전용 페이지(`/fields/{id}/report/{year}?withSub=1`)에 "세부기술 보고서 포함"
  토글. URL 쿼리에 상태를 실어 공유·북마크 가능. 켜면 종합보고서 뒤에 각 세부기술
  보고서를 이어붙이고, 인쇄 시 `break-before-page`로 세부기술마다 새 페이지에서 시작.
- 각 본문은 프론트 `stripLeadingH1`로 자체 H1을 걷어낸다(화면이 제목을 붙이는 것과 같은 이유).
- 로드맵 점검 페이지에는 없다 — 점검 결과에 세부기술 원문을 붙이는 건 성격이 안 맞음.

## 데이터 흐름 · 인터페이스

| 계층 | 추가/변경 |
|---|---|
| 모델 | `FieldReport`/`RoadmapCheck`에 `status`,`error` (migration 0015) |
| 서비스 | `reducer`: `build_field_report`→`enqueue_field_report`+`process_field_report`, `check_roadmap`→`enqueue_roadmap_check`+`process_roadmap_check`; `runner`: `advance_field_reports`,`_process_report` |
| API(admin) | POST `.../report`·`.../roadmap-check`(큐잉으로 전환), `run-all`, `GET field-reports`(현황) |
| API(public) | `report`·`roadmap-check` 응답에 `status`/`error`; `GET subfield-reports` |
| 프론트 | `GeneratedReportSection`(폴링), `FieldReportsPanel`(관리자 탭), `FieldReportPage`(첨부 토글), `stripLeadingH1` 재사용 |

## 에러 처리

- 큐잉 시점: 없는 분야→404, 세부기술 보고서 없음/로드맵 미등록/표 아님→409.
- 처리 시점: 세부기술 보고서가 사라졌으면 ValueError→`failed`+error. 화면이 error를 표시.
- 폴링: 일시 오류는 무시하고 다음 틱 재시도.

## 테스트

- 백엔드: 큐잉 후 `_drain_report_queue()` 헬퍼로 처리를 흉내내고 done/failed/현황/일괄/첨부
  검증(`test_api.py`). failed가 루프를 멈추지 않는지, run-all이 대상만 큐잉하는지 포함.
- 프론트: `stripLeadingH1`·`ProgressPie` 순수 함수 단위 테스트.

## 한계 · 후속

- **취소 기능 없음.** 큐잉된 pending을 되돌릴 방법이 없어, 일괄 실행은 신중히 눌러야 한다.
- 종합보고서는 rollup 스킵 로직이 없어 재생성마다 LLM 1콜 비용이 든다(각 ~$0.01).
- 비공개 로드맵은 여전히 처리 시 Gemini API로 원문이 전송된다 — 로컬 모델 분기는
  `process_roadmap_check`의 `gemini_sync.generate` 한 지점만 바꾸면 되나 미구현.
