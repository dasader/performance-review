from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import (
    BaseModel,
    Field as PydanticField,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session

from app.clients import kci, openalex
from app.config import settings
from app.database import get_db
from app.deps import require_admin
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import CountryComparison, Field, FieldReport, Roadmap, RoadmapCheck, Subfield
from app.models.schedule import AnalysisRun
from app.services import budget, comparison, mapper, reducer, runner, search
from app.services._countries import invalid_countries, parse_countries
from app.services._time import utcnow

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _YearRangeMixin(BaseModel):
    year_from: int = PydanticField(ge=1900, le=2100)
    year_to: int = PydanticField(ge=1900, le=2100)

    @model_validator(mode="after")
    def _check_year_range(self):
        if self.year_from > self.year_to:
            raise ValueError(
                f"시작 연도({self.year_from})가 종료 연도({self.year_to})보다 늦습니다."
            )
        return self


class SubfieldIn(BaseModel):
    field_id: int
    name: NonBlankStr
    query: NonBlankStr
    query_kci: str | None = None
    active: bool = True


class RoadmapIn(BaseModel):
    version_label: NonBlankStr
    content_md: NonBlankStr


class PreviewIn(_YearRangeMixin):
    subfield_id: int
    # 국가마다 모집단이 다르다(같은 검색식으로 CN이 KR의 2~3배). 국가를 빼면
    # 미리보기가 늘 KR 기준으로 나와, 다른 국가를 실행하려는 사람에게 틀린 견적을 준다.
    country: str = "KR"


class RunIn(_YearRangeMixin):
    subfield_ids: list[int] = PydanticField(min_length=1)
    force: bool = False
    # ISO 3166-1 alpha-2. 기본 KR이라 화면을 고치기 전에도 기존 동작이 그대로다.
    country: str = "KR"


class QueueAnalysisIn(BaseModel):
    subfield_id: int
    # ISO 3166-1 alpha-2. 화면의 국가 열 한 칸이 이 항목 하나에 대응한다.
    country: str = "KR"
    force: bool = False


class QueueComparisonIn(BaseModel):
    subfield_id: int
    # 다국 비교 하나만 만든다 — 1:1은 그 안의 섹션으로 조회된다(2026-08-04 설계).
    #
    # **min_length=2를 걸지 않는다.** 스키마에서 막으면 항목 하나가 잘못됐을 때
    # Pydantic이 요청 본문 전체를 422로 거부해, 같이 보낸 다른 종류까지 통째로
    # 사라진다 — "한 건이 막혀도 나머지는 큐잉한다"는 이 API의 존재 이유와 충돌한다.
    # 국가 수 검증은 enqueue_comparison이 ValueError로 하고, 핸들러가 그것을
    # skipped 한 줄로 옮긴다.
    countries: list[str] = []


class QueueIn(BaseModel):
    """관리자 화면에서 체크한 셀들을 한 요청으로 큐잉한다.

    네 종류를 한 번에 받는 이유: 화면의 "선택한 N건 생성"이 호출 한 번이어야 하고,
    부분 실패 집계를 화면마다 따로 하지 않기 위해서다. 종류별로 나누면 15건 선택에
    왕복이 15번 나가고 어디까지 성공했는지를 프론트가 스스로 조립해야 한다.
    """

    year: int = PydanticField(ge=1900, le=2100)
    analyses: list[QueueAnalysisIn] = []
    comparisons: list[QueueComparisonIn] = []
    field_reports: list[int] = []      # field_id
    roadmap_checks: list[int] = []     # field_id


class ScheduleIn(BaseModel):
    enabled: bool
    day: int
    hour: int
    years_back: int
    # 콤마 구분 국가 목록("KR,US,CN"). 국가마다 검색·추출이 따로 돌아 비용이 곱해진다.
    countries: str = "KR"
    # 대상국 분석이 전부 done이 되면 국가 비교(다국 1건)를 자동 큐잉한다.
    auto_comparison: bool = False

    @field_validator("countries")
    @classmethod
    def _check_countries(cls, v: str) -> str:
        """형식이 어긋난 코드를 막는다 — 잘못 저장되면 스케줄러가 조용히 존재하지 않는
        국가로 검색을 돌려 0건을 받는다(오류도 안 난다)."""
        codes = parse_countries(v)
        if not codes:
            raise ValueError("국가를 최소 하나 지정해야 합니다.")
        bad = invalid_countries(codes)
        if bad:
            raise ValueError(f"국가 코드는 두 글자 알파벳이어야 합니다: {', '.join(bad)}")
        return ",".join(codes)

    @model_validator(mode="after")
    def _check_ranges(self):
        if not (1 <= self.day <= 28):
            raise ValueError(
                f"일자는 1~28 사이여야 합니다({self.day}). 29~31일은 없는 달이 있어 "
                f"그런 달에는 실행이 건너뛰어집니다."
            )
        if not (0 <= self.hour <= 23):
            raise ValueError(f"시각은 0~23 사이여야 합니다({self.hour}).")
        if not (0 <= self.years_back <= 5):
            raise ValueError(f"대상 연도 범위는 0~5 사이여야 합니다({self.years_back}).")
        return self


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
    if not db.get(Field, payload.field_id):
        raise HTTPException(status_code=404, detail="분야를 찾을 수 없습니다.")
    row = Subfield(**payload.model_dump())
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.put("/subfields/{subfield_id}")
def update_subfield(subfield_id: int, payload: SubfieldIn, db: Session = Depends(get_db)):
    row = db.get(Subfield, subfield_id)
    if not row:
        raise HTTPException(status_code=404, detail="세부기술을 찾을 수 없습니다.")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.delete("/subfields/{subfield_id}")
def delete_subfield(subfield_id: int, db: Session = Depends(get_db)):
    row = db.get(Subfield, subfield_id)
    if not row:
        raise HTTPException(status_code=404, detail="세부기술을 찾을 수 없습니다.")
    history_count = db.query(Analysis).filter(Analysis.subfield_id == subfield_id).count()
    if history_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"분석 이력이 {history_count}건 있어 삭제할 수 없습니다. "
                f"목록에서 감추려면 비활성화(active=false)하세요."
            ),
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/preview")
async def preview(payload: PreviewIn, db: Session = Depends(get_db)):
    """검색만 실행해 건수·샘플·예상 비용을 보여준다. LLM은 호출하지 않는다."""
    subfield = db.get(Subfield, payload.subfield_id)
    if not subfield:
        raise HTTPException(status_code=404, detail="세부기술을 찾을 수 없습니다.")

    try:
        budget.check_budget(db, settings.openalex_search_cost_usd)
    except budget.BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from e

    async with httpx.AsyncClient() as client:
        # count_only를 따로 부르지 않는다 — search()가 돌려주는 total_count가 meta.count,
        # 즉 잘리기 전 전체 건수라 count_only와 같은 값이다. 두 번 부르면 OpenAlex가
        # 요청 건당(반환 수와 무관하게) 과금하므로 미리보기 클릭마다 값이 두 배가 된다.
        try:
            sample = await openalex.search(
                subfield.query, payload.year_from, payload.year_to,
                client=client, limit=20, country=payload.country,
            )
        except Exception as e:
            # 페이지 도중 실패해도 이미 과금된 몫은 예산에 남겨야 한다(search.collect와
            # 같은 패턴). count_only를 없앤 뒤로는 이 호출이 미리보기의 유일한 과금
            # 지점이라, 여기서 놓치면 실패한 미리보기의 비용이 통째로 누락된다.
            budget.record_usage(db, getattr(e, "cost_usd", 0.0), None)
            raise
        # KCI는 한국학술지 전용이라 KR에서만 부른다 — search.collect와 같은 규약.
        # 타국 미리보기에 국내지 표본이 섞이면 실제 실행 결과와 어긋난다.
        kci_papers = []
        if payload.country == "KR":
            kci_papers = await kci.search(
                subfield.kci_query(), payload.year_from, payload.year_to, client=client, limit=20
            )

    count = sample.total_count
    budget.record_usage(db, sample.cost_usd, sample.remaining)
    pages = openalex.estimate_pages(count)
    openalex_cost = round(pages * settings.openalex_search_cost_usd, 4)

    # C3: 신규 추출 대상 추정. 정확히 하려면 openalex_count에서 이미 paper_extractions에
    # 있는 건수를 빼야 하지만, 그러려면 실제 검색을 한 번 더 돌려야 해 미리보기 비용이
    # 두 배가 된다. 대신 상한선 성격으로 min(openalex_count, max_papers)를 쓴다 —
    # 캐시 히트가 있으면 실제 LLM 호출은 이보다 적을 수 있지만, 이보다 많아지지는 않는다.
    estimated_papers_to_extract = min(count, settings.max_papers_per_analysis)
    llm_cost = mapper.estimate_llm_cost_usd(estimated_papers_to_extract)

    return {
        "openalex_count": count,
        "kci_sample_count": len(kci_papers),
        "kci_sample_truncated": len(kci_papers) >= 20,
        "samples": [
            {"title": p["title"], "year": p["year"], "journal": p["journal"],
             "has_abstract": bool(p["abstract"])}
            for p in sample.papers[:20]
        ],
        "estimated_pages": pages,
        "estimated_cost_usd": openalex_cost,
        # 아래 세 값은 모두 추정치다(특히 LLM 비용 — 논문당 평균 토큰 상수 기반 근사).
        "estimated_papers_to_extract": estimated_papers_to_extract,
        "estimated_llm_cost_usd": round(llm_cost, 4),
        "estimated_total_cost_usd": round(openalex_cost + llm_cost, 4),
        "budget_spent": round(budget.spent_today(db), 4),
        "budget_limit": settings.openalex_daily_budget_usd,
        "over_limit": count > settings.max_papers_per_analysis,
        "max_papers": settings.max_papers_per_analysis,
    }


@router.post("/run")
def run(payload: RunIn, db: Session = Depends(get_db)):
    # C1: 여기서 검색 건수(over_limit)를 다시 확인하지 않는다 — 확인하려면 검색을
    # 한 번 더 돌려야 해 이 요청 자체가 이중과금이 된다. 프론트의 버튼 비활성화가
    # 유일한 사전 방어이고 curl로는 우회 가능하다. 상한 초과는 이제 차단이 아니라
    # 표본 수집으로 처리된다 — runner._do_search가 인용 상위 N건만 받고 그 사실을
    # stats의 population_total·sampled로 남긴다(거부하면 CN 11개·US 3개 세부기술이
    # 그냥 실패한다는 실측 때문에 바꿨다).
    if budget.spent_today(db) >= settings.openalex_daily_budget_usd:
        raise HTTPException(
            status_code=429,
            detail=(
                f"OpenAlex 일일 예산이 이미 소진되었습니다. "
                f"UTC {budget.reset_time_utc():%Y-%m-%d %H:%M} 이후 재시도하세요."
            ),
        )

    queued, blocked = [], []
    for subfield_id in payload.subfield_ids:
        subfield = db.get(Subfield, subfield_id)
        if not subfield:
            blocked.append({"subfield_id": subfield_id, "reason": "세부기술 없음"})
            continue
        try:
            rows = runner.enqueue(
                db, subfield, payload.year_from, payload.year_to,
                force=payload.force, country=payload.country,
            )
        except ValueError as e:
            blocked.append({"subfield_id": subfield_id, "reason": str(e)})
            continue
        queued.extend(a.id for a in rows)
    return {"queued": queued, "blocked": blocked}


@router.post("/queue")
def queue(payload: QueueIn, db: Session = Depends(get_db)):
    """체크한 대상들을 한 번에 큐잉하고, 건너뛴 것은 사유와 함께 돌려준다.

    하나가 막혀도 나머지는 큐잉한다(field-reports/run-all의 규약). **조용히 건너뛰지
    않는 것**이 run-all과 다른 점이다 — "10건 큐잉, 3건 건너뜀"만으로는 상대국 분석이
    없어서인지 로드맵이 미등록이어서인지 알 수 없었다.

    enqueue 계열 함수들이 각자 db.commit()을 하므로 여기서 다시 커밋하지 않는다.
    """
    queued = {"analyses": 0, "comparisons": 0, "field_reports": 0, "roadmap_checks": 0}
    skipped: list[dict] = []

    # 예산이 이미 소진됐으면 분석은 큐잉하지 않는다. 큐잉해 두면 잡 루프가 건마다
    # count_only(건당 $0.001)를 한 번 쓰고 paused로 내려간다 — search.collect가
    # 예산 게이트보다 먼저 부르기 때문이다(페이징 전에 게이트를 통과시키려는 설계).
    # 보고서류는 OpenAlex를 쓰지 않으므로 같은 요청 안에서도 그대로 처리한다.
    over_budget = bool(payload.analyses) and (
        budget.spent_today(db) >= settings.openalex_daily_budget_usd
    )
    for item in payload.analyses:
        # 화면의 셀은 subfield × country × year라, skip 사유에도 country를 실어야
        # 프론트가 어느 셀이 걸렸는지 되짚을 수 있다(Finding 3) — 정규화 전 원문을
        # 쓰면 "us"와 "US"가 다른 셀처럼 보인다.
        codes = parse_countries(item.country)
        if not codes or invalid_countries(codes):
            skipped.append({
                "kind": "analysis", "subfield_id": item.subfield_id,
                "country": item.country,
                "reason": f"국가 코드는 두 글자 알파벳이어야 합니다: {item.country}",
            })
            continue
        country = codes[0]

        if over_budget:
            skipped.append({
                "kind": "analysis",
                "subfield_id": item.subfield_id,
                "country": country,
                "reason": (
                    f"OpenAlex 일일 예산 소진 — UTC "
                    f"{budget.reset_time_utc():%Y-%m-%d %H:%M} 이후 재시도하세요."
                ),
            })
            continue
        subfield = db.get(Subfield, item.subfield_id)
        if subfield is None:
            skipped.append({"kind": "analysis", "subfield_id": item.subfield_id,
                            "country": country, "reason": "세부기술 없음"})
            continue
        try:
            rows = runner.enqueue(
                db, subfield, payload.year, payload.year,
                force=item.force, country=country,
            )
        except ValueError as e:
            # 비활성 세부기술. 화면이 선택 불가로 그리지만 다른 세션에서 비활성화하면
            # 오래된 선택이 그대로 넘어오므로 서버에서도 막는다(검색·추출은 과금이다).
            skipped.append({"kind": "analysis", "subfield_id": item.subfield_id,
                            "country": country, "reason": str(e)})
            continue
        if not rows:
            # runner.enqueue가 빈 리스트를 돌려주는 건 이미 done이고 query_hash도
            # 그대로라는 뜻(runner.py 주석 참고) — 아무 것도 안 됐는데 queued도 skipped도
            # 안 남으면 5건 선택해 눌렀을 때 "analyses: 0"만 보고 원인을 알 수 없다.
            skipped.append({
                "kind": "analysis", "subfield_id": item.subfield_id, "country": country,
                "reason": "이미 완료된 분석이고 검색식도 바뀌지 않았습니다 — "
                          "다시 실행하려면 강제 재실행을 선택하세요.",
            })
            continue
        queued["analyses"] += len(rows)

    for item in payload.comparisons:
        # enqueue_comparison도 내부에서 parse_countries로 정규화하지만 형식 오류(예:
        # "USA")는 걸러내지 않는다 — 걸러내지 않으면 잘못된 코드가 그대로 저장돼 검색
        # 필터·비교 화면 어느 쪽에서도 매칭되지 않는 고아 행이 된다(Finding 2).
        codes = parse_countries(item.countries)
        bad = invalid_countries(codes)
        if bad:
            skipped.append({
                "kind": "comparison", "subfield_id": item.subfield_id, "countries": codes,
                "reason": f"국가 코드는 두 글자 알파벳이어야 합니다: {', '.join(bad)}",
            })
            continue
        try:
            comparison.enqueue_comparison(db, item.subfield_id, payload.year, codes)
            queued["comparisons"] += 1
        except (LookupError, ValueError) as e:
            skipped.append({"kind": "comparison", "subfield_id": item.subfield_id,
                            "countries": codes, "reason": str(e)})

    # 분야 산출물 두 종류는 큐잉 함수 이름과 집계 키만 다르고 실패 처리가 같다.
    for kind, field_ids, enqueue_one in (
        ("field_report", payload.field_reports, reducer.enqueue_field_report),
        ("roadmap_check", payload.roadmap_checks, reducer.enqueue_roadmap_check),
    ):
        for field_id in field_ids:
            try:
                enqueue_one(db, field_id, payload.year)
                queued[f"{kind}s"] += 1
            except (LookupError, ValueError) as e:
                skipped.append({"kind": kind, "field_id": field_id, "reason": str(e)})

    return {"queued": queued, "skipped": skipped}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    """세부기술 × 연도 격자. 검색식이 바뀐 항목은 stale=True로 표시된다."""
    subfields = db.query(Subfield).order_by(Subfield.field_id, Subfield.name).all()

    # 세부기술마다 따로 조회하면 55개 세부기술에 56번 질의하게 되고, 전체 ORM 행을
    # 실으면 화면이 쓰지도 않는 report_md(건당 12KB 규모)와 stats_json까지 딸려온다.
    # 필요한 컬럼만 한 번에 읽어 subfield_id로 묶는다. stale 판정도 여기서 같이 받은
    # query_hash로 계산해 셀마다 질의가 나가지 않게 한다.
    by_subfield: dict[int, list] = {}
    for a in db.query(
        Analysis.id, Analysis.subfield_id, Analysis.year, Analysis.status,
        Analysis.searched_count, Analysis.analyzed_count, Analysis.snapshot_at,
        Analysis.error, Analysis.query_hash, Analysis.country,
    ).all():
        by_subfield.setdefault(a.subfield_id, []).append(a)

    # 비교 상태도 같은 응답에 싣는다 — 세부기술 탭이 분석과 비교를 한 표에 그리므로
    # 두 번 부르면 두 응답의 연도·국가가 어긋날 여지가 생긴다. 연도별로 나눠 담아
    # 화면의 연도 필터와 행 펼침이 추가 요청 없이 동작하게 한다.
    comparisons: dict[int, dict[int, dict[str, str]]] = {}
    for c in db.query(
        CountryComparison.subfield_id, CountryComparison.year,
        CountryComparison.countries, CountryComparison.status,
    ).all():
        cells = comparisons.setdefault(c.subfield_id, {}).setdefault(c.year, {})
        cells[c.countries] = c.status
        # 3개국 이상 비교는 쌍별 1:1을 먼저 만들어 sections_json에 담고 종합한다
        # (process_comparison). 그 쌍을 미생성으로 두면 이미 있는 것을 다시 만든다.
        codes = c.countries.split(",")
        if c.status == "done" and len(codes) > 2:
            base = comparison.base_country(codes)
            for other in codes:
                if other != base:
                    cells.setdefault(",".join(sorted((base, other))), "in_multi")

    rows = []
    for subfield in subfields:
        analyses = by_subfield.get(subfield.id, [])
        rows.append({
            "subfield_id": subfield.id,
            "subfield_name": subfield.name,
            "field_id": subfield.field_id,
            # 비활성 세부기술도 행은 보여주되(운영자가 존재를 알아야 함) 프론트가 선택
            # 후보에서 뺀다 — 예전 /admin/subfields?active=true 필터가 하던 유일한 가드였다.
            "active": subfield.active,
            # JSON 객체 키는 문자열이어야 한다.
            "comparisons": {
                str(year): cells
                for year, cells in comparisons.get(subfield.id, {}).items()
            },
            "years": [
                {
                    "analysis_id": a.id,
                    "year": a.year,
                    "status": a.status,
                    "status_label": runner.STEP_LABELS.get(a.status, a.status),
                    "searched_count": a.searched_count,
                    "analyzed_count": a.analyzed_count,
                    "snapshot_at": a.snapshot_at.isoformat() if a.snapshot_at else None,
                    # country를 빼면 KR 해시와 비교돼 비KR 분석이 영원히 "갱신 필요"로
                    # 뜬다 — enqueue()가 해시를 만들 때와 같은 인자를 줘야 한다.
                    "stale": a.query_hash != search.query_hash(subfield, a.year, a.year, a.country),
                    "error": a.error,
                    "country": a.country,
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


@router.get("/schedule")
def get_schedule(db: Session = Depends(get_db)):
    """스케줄 설정 + 상태. 스케줄 설정 카드(관리자 화면)가 통째로 이 응답 하나로 그려진다."""
    cfg = runner.get_schedule_settings(db)
    db.commit()  # 첫 조회에서 새로 만든 기본값 행을 즉시 영속화한다.
    return {
        "enabled": cfg.enabled,
        "day": cfg.day,
        "hour": cfg.hour,
        "years_back": cfg.years_back,
        "countries": cfg.countries,
        "auto_comparison": cfg.auto_comparison,
        "timezone": settings.schedule_timezone,  # 읽기 전용 — .env 전용 값
        # 스케줄 타임존(기본 KST) 기준 wall-clock 값을 tzinfo 없이 그대로 낸다.
        "next_run_at": runner.next_scheduled_run_at(db).isoformat(),
        "history": runner.schedule_history(db, limit=12),
    }


@router.put("/schedule")
def update_schedule(payload: ScheduleIn, db: Session = Depends(get_db)):
    cfg = runner.get_schedule_settings(db)
    cfg.enabled = payload.enabled
    cfg.day = payload.day
    cfg.hour = payload.hour
    cfg.years_back = payload.years_back
    cfg.countries = payload.countries
    cfg.auto_comparison = payload.auto_comparison
    db.commit()
    # 프론트가 저장 후 곧바로 재조회하는 화면이라, GET과 같은 응답(history 포함)을
    # 그대로 돌려준다 — 응답 dict를 두 벌 유지하면 한쪽만 고쳐져 어긋난다.
    return get_schedule(db)


@router.post("/schedule/run-now")
def run_schedule_now(db: Session = Depends(get_db)):
    """스케줄 시각 판정을 우회해 즉시 1회 큐잉한다. 되돌릴 수 없는 동작이므로 프론트에서
    확인 단계를 거친 뒤에만 호출해야 한다."""
    return {"queued_count": runner.run_scheduled_now(db)}


@router.get("/fields/{field_id}/roadmap")
def get_roadmap(field_id: int, db: Session = Depends(get_db)):
    """등록된 로드맵 원문. 없으면 404가 아니라 빈 값 — 편집 화면이 그대로 새 입력을
    받는 폼으로 쓰인다."""
    row = db.query(Roadmap).filter(Roadmap.field_id == field_id).one_or_none()
    if row is None:
        return {"version_label": "", "content_md": "", "goal_count": 0, "updated_at": None}
    return {
        "version_label": row.version_label,
        "content_md": row.content_md,
        "goal_count": reducer.count_goal_rows(row.content_md),
        "updated_at": row.updated_at.isoformat(),
    }


@router.put("/fields/{field_id}/roadmap")
def put_roadmap(field_id: int, payload: RoadmapIn, db: Session = Depends(get_db)):
    """로드맵 원문 저장(분야당 1건, 덮어쓰기).

    목표 행을 하나도 못 찾으면 저장을 거부한다 — 표 형식이 아닌 텍스트를 넣으면
    전수 점검 강제(goal_count 주입)가 무력화되는데, 그 사실이 보고서 생성 시점까지
    드러나지 않으면 원인을 찾기 어렵다.
    """
    if db.get(Field, field_id) is None:
        raise HTTPException(status_code=404, detail="분야를 찾을 수 없습니다.")

    goal_count = reducer.count_goal_rows(payload.content_md)
    if goal_count == 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "로드맵에서 단계별 목표 행을 찾지 못했습니다. 목표가 마크다운 표"
                "(| 단계 | 시기 | 기술적 목표 | 형식)로 되어 있는지 확인하세요."
            ),
        )

    row = db.query(Roadmap).filter(Roadmap.field_id == field_id).one_or_none()
    if row is None:
        row = Roadmap(field_id=field_id)
        db.add(row)
    row.version_label = payload.version_label
    row.content_md = payload.content_md
    row.updated_at = utcnow()
    db.commit()
    return {"goal_count": goal_count}


@router.delete("/fields/{field_id}/roadmap")
def delete_roadmap(field_id: int, db: Session = Depends(get_db)):
    """로드맵 원문 삭제. 이미 생성된 점검 보고서는 남긴다 — 그 시점의 판본으로 만든
    기록이고, roadmap_version에 어느 판본이었는지 적혀 있다."""
    db.query(Roadmap).filter(Roadmap.field_id == field_id).delete()
    db.commit()
    return {"ok": True}


def _enqueue_or_http(fn, db, field_id: int, year: int) -> dict:
    """enqueue_* 공통 래퍼 — LookupError→404, ValueError→409로 옮기고 pending 응답을 만든다.

    생성은 즉시 실행하지 않고 pending으로 큐잉만 한다(실제 LLM 호출은 runner.loop이
    한 틱에 하나씩). 화면은 응답을 받은 뒤 그 자리에서 status를 폴링한다.
    """
    try:
        row = fn(db, field_id, year)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"field_id": row.field_id, "year": row.year, "status": row.status}


@router.post("/fields/{field_id}/roadmap-check")
def enqueue_roadmap_check(field_id: int, year: int, db: Session = Depends(get_db)):
    """로드맵 이행 점검을 pending으로 큐잉한다. ⚠ 처리 시 로드맵 원문이 Gemini API로
    전송된다(reducer.process_roadmap_check 주석 참고)."""
    return _enqueue_or_http(reducer.enqueue_roadmap_check, db, field_id, year)


@router.post("/fields/{field_id}/report")
def enqueue_field_report(field_id: int, year: int, db: Session = Depends(get_db)):
    """분야 종합 보고서를 pending으로 큐잉한다. 이미 있는 연도를 다시 부르면 재생성
    큐잉 — 그 사이 새로 done이 된 세부기술을 반영하는 유일한 방법이다."""
    return _enqueue_or_http(reducer.enqueue_field_report, db, field_id, year)


@router.get("/field-reports")
def field_reports_overview(year: int, db: Session = Depends(get_db)):
    """관리자 "분야 보고서" 탭용 현황 — 분야별 종합/점검 상태를 한 번에.

    분야마다 따로 조회하면 질의가 분야 수만큼 나가므로, 두 테이블을 각각 한 번에 읽어
    분야 id로 묶는다. 로드맵 등록 여부도 함께 내려 어느 분야가 점검 대상인지 보인다.
    """
    reports = {
        r.field_id: r
        for r in db.query(FieldReport).filter(FieldReport.year == year)
    }
    checks = {
        r.field_id: r
        for r in db.query(RoadmapCheck).filter(RoadmapCheck.year == year)
    }
    roadmap_fields = {r.field_id for r in db.query(Roadmap.field_id)}

    def cell(row) -> dict | None:
        if row is None:
            return None
        return {
            "status": row.status,
            "source_count": row.source_count,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            "error": row.error,
        }

    rows = []
    for field in db.query(Field).order_by(Field.order_no).all():
        rows.append({
            "field_id": field.id,
            "field_name": field.name,
            "has_roadmap": field.id in roadmap_fields,
            "report": cell(reports.get(field.id)),
            "roadmap_check": cell(checks.get(field.id)),
        })
    return {"year": year, "rows": rows}


@router.post("/field-reports/run-all")
def run_all_field_reports(year: int, kind: str = "report", db: Session = Depends(get_db)):
    """당해연도 전체 분야를 일괄 큐잉한다. kind: report(분야 종합) | roadmap-check.

    검증에 걸리는 분야(세부기술 보고서 없음·로드맵 미등록 등)는 조용히 건너뛴다 —
    "가능한 것만 큐잉"이 일괄 실행의 의도이므로 하나가 막혀 전체가 실패하면 안 된다.
    """
    if kind not in ("report", "roadmap-check"):
        raise HTTPException(status_code=422, detail="kind는 report 또는 roadmap-check여야 합니다.")
    enqueue = (
        reducer.enqueue_field_report if kind == "report" else reducer.enqueue_roadmap_check
    )
    queued, skipped = 0, 0
    for field in db.query(Field).all():
        try:
            enqueue(db, field.id, year)
            queued += 1
        except (LookupError, ValueError):
            skipped += 1
    return {"kind": kind, "year": year, "queued": queued, "skipped": skipped}


@router.get("/comparison-grid")
def comparison_grid(year: int, db: Session = Depends(get_db)):
    """세부기술 × (국가 분석 · 비교 보고서) 현황.

    열은 schedule_settings.countries에 설정된 국가만 — 안 쓰는 나라 열이 늘어붙으면
    격자가 읽히지 않는다. 세부기술마다 질의하면 55번 나가므로 두 테이블을 각각
    한 번에 읽어 subfield_id로 묶는다(field_reports_overview와 같은 방식).
    """
    cfg = runner.get_schedule_settings(db)
    countries = parse_countries(cfg.countries or "KR")
    subfields = db.query(Subfield).filter(Subfield.active.is_(True)).order_by(
        Subfield.field_id, Subfield.name
    ).all()
    ids = [s.id for s in subfields]

    analyses: dict[int, dict[str, str]] = {}
    for a in db.query(Analysis.subfield_id, Analysis.country, Analysis.status).filter(
        Analysis.subfield_id.in_(ids), Analysis.year == year
    ):
        analyses.setdefault(a.subfield_id, {})[a.country] = a.status

    comparisons: dict[int, dict[str, str]] = {}
    for c in db.query(
        CountryComparison.subfield_id, CountryComparison.countries, CountryComparison.status
    ).filter(CountryComparison.subfield_id.in_(ids), CountryComparison.year == year):
        cells = comparisons.setdefault(c.subfield_id, {})
        cells[c.countries] = c.status
        # 3개국 이상 비교는 쌍별 1:1을 먼저 만들어 sections_json에 담고 그것을 종합한다
        # (process_comparison). 즉 "한국 vs 중국"은 이미 이 행 안에 있는데, 격자는
        # "CN,KR" 행을 찾으므로 없다고 표시했다 — 그래서 실제로는 만들 필요가 없는
        # 1:1을 다시 만들게 된다. 포함된 쌍을 별도 상태로 알려 그 중복을 막는다.
        codes = c.countries.split(",")
        if c.status == "done" and len(codes) > 2:
            base = comparison.base_country(codes)
            for other in codes:
                if other == base:
                    continue
                pair = ",".join(sorted((base, other)))
                cells.setdefault(pair, "in_multi")

    return {
        "year": year,
        "countries": countries,
        "rows": [
            {
                "subfield_id": s.id,
                "subfield_name": s.name,
                "field_id": s.field_id,
                "analyses": analyses.get(s.id, {}),
                "comparisons": comparisons.get(s.id, {}),
            }
            for s in subfields
        ],
    }


@router.post("/comparisons/run-all")
def run_all_comparisons(year: int, mode: str = "pairs", db: Session = Depends(get_db)):
    """당해연도 전체 세부기술의 비교를 일괄 큐잉한다.

    mode=pairs — 기준국과 각 상대국의 1:1 비교를 각각 만든다(KR,US / KR,CN).
    mode=all   — 설정된 국가 전체를 한 보고서로(KR,US,CN).

    대상이 안 되는 세부기술(상대국 분석 없음)은 조용히 건너뛴다 —
    하나가 막혀 전체가 실패하면 안 된다(field-reports/run-all과 같은 규약).
    """
    if mode not in ("pairs", "all"):
        raise HTTPException(status_code=422, detail="mode는 pairs 또는 all이어야 합니다.")

    cfg = runner.get_schedule_settings(db)
    countries = sorted(parse_countries(cfg.countries or "KR"))
    if len(countries) < 2:
        raise HTTPException(
            status_code=409,
            detail="스케줄의 대상 국가가 2개 이상이어야 비교를 만들 수 있습니다.",
        )

    combos = (
        [sorted(pair) for pair in comparison.pair_countries(countries)]
        if mode == "pairs"
        else [countries]
    )

    queued = skipped = 0
    for subfield in db.query(Subfield).filter(Subfield.active.is_(True)):
        for combo in combos:
            try:
                comparison.enqueue_comparison(db, subfield.id, year, combo)
                queued += 1
            except (LookupError, ValueError):
                # enqueue_comparison은 검증(세부기술 존재·국가 2개 이상·상대국 분석
                # 존재)을 db.add() 이전에 전부 마치므로 실패 시 세션에 남는 미결
                # 변경이 없다 — 그래도 이전 반복에서 쌓였을 수 있는 커밋되지 않은
                # 상태를 확실히 비워, 이 세부기술을 건너뛴 것이 다음 세부기술의
                # 질의(analyses.filter 등)에 영향을 주지 않게 한다.
                db.rollback()
                skipped += 1
    return {"queued": queued, "skipped": skipped}


@router.delete("/analyses/{analysis_id}")
def delete_analysis(analysis_id: int, db: Session = Depends(get_db)):
    """개별 분석(보고서) 삭제.

    세부기술 삭제는 분석 이력이 있으면 409로 막힌다(delete_subfield) — 분석 결과는 비용을
    들여 만든 자산이라서다. 그런데 보고서를 개별로 지울 방법이 없으면 한 번 분석한
    세부기술은 영영 삭제할 수 없는 막다른 길이 된다. 이 엔드포인트가 그 탈출구다.

    Paper/PaperExtraction은 절대 지우지 않는다: 추출 결과는 LLM 비용을 들여 만든 캐시이고
    paper_key + model_ver로 다른 세부기술·연도의 분석과 공유된다(mapper.pending_papers가
    이 캐시를 조회) — 여기서 지우는 건 이 분석이 만든 보고서·통계·링크(AnalysisPaper)·
    실행 이력(AnalysisRun)뿐이다. 재실행하면 캐시 히트로 추출 비용 없이 다시 만들어진다.
    """
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="분석을 찾을 수 없습니다.")
    if analysis.status in runner.ACTIVE_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"진행 중인 분석입니다(상태: "
                f"{runner.STEP_LABELS.get(analysis.status, analysis.status)}). "
                f"이미 배치 작업이 제출됐을 수 있어 중간에 삭제하면 고아 상태가 됩니다. "
                f"완료되거나 실패한 뒤에 삭제하세요."
            ),
        )
    db.query(AnalysisRun).filter(AnalysisRun.analysis_id == analysis_id).delete()
    db.query(AnalysisPaper).filter(AnalysisPaper.analysis_id == analysis_id).delete()
    db.delete(analysis)
    db.commit()
    return {"ok": True}


@router.post("/analyses/{analysis_id}/retry")
def retry(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="분석을 찾을 수 없습니다.")
    analysis.status = "pending"
    analysis.error = None
    analysis.batch_job_id = None
    analysis.extract_attempts = 0  # M11: 상한에 걸려 failed된 잡을 재시도할 때
    analysis.search_attempts = 0   # 카운터를 리셋 안 하면 첫 재시도에서 즉시 다시 failed된다.
    db.commit()
    return {"ok": True, "id": analysis.id}


@router.post("/subfields/{subfield_id}/comparison")
def enqueue_comparison(
    subfield_id: int, year: int, countries: str, db: Session = Depends(get_db)
):
    """국가 비교 보고서를 pending으로 큐잉한다. countries는 콤마 구분(예: KR,US,CN).

    형식 오류·국가 2개 미만은 422, 세부기술 없음은 404, 분석이 없는 국가는 409.
    국가 코드 형식을 여기서 막는 이유는 스케줄 countries와 같다 — 잘못 저장되면
    존재하지 않는 국가로 조회가 돌아 조용히 404가 되고 원인을 찾기 어렵다.
    """
    codes = parse_countries(countries)
    if invalid_countries(codes):
        raise HTTPException(status_code=422, detail="국가 코드는 두 글자 알파벳이어야 합니다.")
    try:
        row = comparison.enqueue_comparison(db, subfield_id, year, codes)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        # 국가 2개 미만은 요청 형식 문제(422), 분석 부재는 상태 충돌(409). 예전에는
        # 예외 메시지에 "2개"가 들어 있는지로 갈랐고, 그러면 문구를 다듬는 순간
        # 상태 코드가 조용히 바뀐다 — 문자열이 아니라 요청 데이터로 판정한다.
        raise HTTPException(status_code=422 if len(codes) < 2 else 409, detail=str(e))
    return {
        "subfield_id": row.subfield_id,
        "year": row.year,
        "countries": row.countries.split(","),
        "status": row.status,
    }
