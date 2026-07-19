import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import gemini_batch
from app.clients._http import RateLimited
from app.config import settings
from app.database import SessionLocal
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import Subfield
from app.models.paper import Paper, PaperExtraction
from app.models.schedule import AnalysisRun, ScheduledRun
from app.services import mapper, reducer, search, stats
from app.services.budget import BudgetExceeded, spent_today

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
    db: Session, subfield: Subfield, year_from: int, year_to: int, *, force: bool,
    trigger: str = "manual",
) -> list[Analysis]:
    """연도별 Analysis를 만들거나 되살린다.

    이미 done이고 query_hash가 같으면 건너뛴다(재호출 방지). 검색식이 바뀌었으면
    같은 행을 pending으로 되돌려 증분 재실행한다 — 프리즈는 두지 않는다.

    trigger는 이 행을 활성화한 원인(manual|scheduled)을 남긴다 — done에 도달할 때
    AnalysisRun에 그대로 기록되어 수동/자동 실행이 실제로 새 논문을 얼마나 찾아내는지
    나중에 비교할 수 있게 한다.
    """
    queued: list[Analysis] = []
    for year in range(year_from, year_to + 1):
        current_hash = search.query_hash(subfield, year, year)
        row = db.query(Analysis).filter(
            Analysis.subfield_id == subfield.id, Analysis.year == year
        ).first()

        if row is None:
            row = Analysis(subfield_id=subfield.id, year=year, status="pending",
                           query_hash=current_hash, trigger=trigger)
            db.add(row)
            queued.append(row)
        elif force or row.status in ("failed", "paused") or row.query_hash != current_hash:
            query_changed = row.query_hash != current_hash
            row.status = "pending"
            row.query_hash = current_hash
            row.error = None
            row.batch_job_id = None
            row.extract_attempts = 0  # M11: 재시도 카운터를 리셋하지 않으면 상한에
            row.search_attempts = 0   # 걸려 failed된 잡이 재실행 즉시 다시 failed된다.
            row.trigger = trigger
            if query_changed:
                # I7: 검색식이 바뀐 재실행은 옛 검색식으로만 걸리던 논문이 통계 모집단에
                # 영구히 남지 않도록 링크만 정리한다. papers 테이블 자체와 paper_extractions
                # 캐시(비용 들여 만든 자산)는 건드리지 않는다 — 같은 논문이 새 검색식에도
                # 걸리면 upsert_papers가 같은 행을 재사용하고 추출 캐시도 그대로 히트한다.
                db.query(AnalysisPaper).filter(AnalysisPaper.analysis_id == row.id).delete()
                # C4: analyzed_count도 옛 링크 기준의 값이라, 갱신 생략 판단(_do_reduce)이
                # 재검색 후 건수가 이 옛 값보다 작아 "늘지 않았다"고 오판할 수 있다.
                row.analyzed_count = 0
            queued.append(row)
        elif row.status in ACTIVE_STATES:
            queued.append(row)

    db.commit()
    return queued


def is_stale(db: Session, analysis: Analysis, subfield: Subfield) -> bool:
    """검색식이 바뀌어 갱신이 필요한 상태인지."""
    return analysis.query_hash != search.query_hash(subfield, analysis.year, analysis.year)


def resume_paused(db: Session) -> None:
    """paused 상태 analysis를 예산 여유가 생겼으면 pending으로 되돌린다.

    OpenAlex 예산 사용액은 UTC 날짜별 행으로 관리되므로(budget.spent_today),
    자정이 지나면 별도 컬럼 없이도 이 값이 자연히 0으로 리셋된다 — 이를 재개
    신호로 쓴다. ACTIVE_STATES에는 paused를 넣지 않는다: advance()가 paused를
    전진시키면 안 되고, 재개는 이 함수가 전담한다.
    """
    if spent_today(db) >= settings.openalex_daily_budget_usd:
        return
    paused = db.query(Analysis).filter(Analysis.status == "paused").all()
    for analysis in paused:
        logger.info("[잡 %d] 예산 여유 확인 — pending으로 재개", analysis.id)
        analysis.status = "pending"
        analysis.error = None
    if paused:
        db.commit()


async def advance(db: Session, analysis: Analysis) -> None:
    """상태를 한 단계 전진시킨다. 각 단계는 독립적으로 재진입 가능해야 한다 —
    컨테이너가 언제 재시작되어도 DB 상태만 보고 이어갈 수 있어야 하기 때문."""
    try:
        if analysis.status == "pending":
            analysis.status = "searching"
            db.commit()
        elif analysis.status == "searching":
            subfield = db.get(Subfield, analysis.subfield_id)
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
        elif analysis.status == "searching":
            # I9: get_with_retry가 이미 내부 재시도(기본 5회)를 소진한 뒤 올라온
            # 비영구 429다. 카운터 없이 두면 30초 간격으로 같은 페이지들을 무한히
            # 재과금하며 돈다 — extract_attempts와 대칭으로 상한을 둔다.
            analysis.search_attempts += 1
            if analysis.search_attempts >= settings.max_search_attempts:
                analysis.status = "failed"
                analysis.error = (
                    f"검색을 {settings.max_search_attempts}회 재시도해도 실패해 중단했습니다: {e}"
                )
            else:
                analysis.error = str(e)
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
        result = await search.collect(db, subfield, analysis.year, analysis.year, client=client)

    # C1: search.collect()는 openalex.search(limit=max_papers_per_analysis)로 부르므로
    # result.papers는 구조적으로 상한을 넘을 수 없다 — len(papers) 기준 가드는 과광범위
    # 검색식을 "차단"하는 게 아니라 조용히 "절단"만 하고 통과시킨다. 반드시 OpenAlex가
    # 보고한 잘리기 전 total_count로 판단해야 실제로 차단이 된다.
    if result.total_count > settings.max_papers_per_analysis:
        raise AnalysisTooLarge(
            f"검색 결과 전체 {result.total_count}건이 상한 {settings.max_papers_per_analysis}건을 "
            f"넘습니다. 검색식을 좁히거나 세부기술을 분할하세요."
        )

    rows = search.upsert_papers(db, result.papers)
    existing = {
        r.paper_id for r in db.query(AnalysisPaper.paper_id).filter(
            AnalysisPaper.analysis_id == analysis.id
        )
    }
    for row in rows:
        if row.id not in existing:
            db.add(AnalysisPaper(analysis_id=analysis.id, paper_id=row.id))

    # 세션이 autoflush=False(app/database.py)이므로 방금 add한 링크는 아직 DB에 없다.
    # flush 없이 아래 count()를 돌리면 0이 나온다.
    db.flush()

    # I7: len(rows)는 "이번 검색에서 걸린 건수"라 검색식을 좁혀 재실행하면 stats.compute가
    # 쓰는 _analysis_papers()(누적 링크)와 값이 어긋난다. AnalysisPaper 링크 총수(누적
    # 기준)로 통일해 Report.tsx와 StatsPanel.tsx가 항상 같은 숫자를 보게 한다.
    analysis.searched_count = db.query(AnalysisPaper).filter(
        AnalysisPaper.analysis_id == analysis.id
    ).count()
    analysis.snapshot_at = datetime.now(timezone.utc)
    analysis.search_attempts = 0  # I9: 성공했으니 재시도 카운트 리셋.
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
        state, results = await gemini_batch.poll_async(analysis.batch_job_id)
        if state == "running":
            return
        if state == "failed":
            analysis.status = "failed"
            analysis.error = "Gemini batch 작업 실패"
            db.commit()
            return

        before = len(mapper.pending_papers(db, analysis, _analysis_papers(db, analysis)))
        saved = mapper.save_results(db, analysis, results or [])
        analysis.batch_job_id = None
        db.commit()

        still_pending = mapper.pending_papers(db, analysis, _analysis_papers(db, analysis))
        # poll()이 성공을 반환해도 개별 요청이 대량 파싱 실패했을 수 있다. 제출 시점의
        # 건수를 그대로 비교할 수 없으니(제출과 폴링 사이 시점 차) 저장 전후로 남은
        # pending 건수가 줄지 않았는지로 "진행이 없었다"를 감지한다 — 그래야 다음
        # 루프에서 같은 청크를 무한히 재시도하는 상황을 조용히 넘기지 않는다.
        if before and len(still_pending) >= before:
            analysis.extract_attempts += 1
            logger.warning(
                "[잡 %d] batch 저장 %d건인데 남은 대상이 %d→%d건으로 줄지 않음 (시도 %d/%d) — "
                "결과 파싱 실패가 컸을 수 있습니다.",
                analysis.id, saved, before, len(still_pending),
                analysis.extract_attempts, settings.max_extract_attempts,
            )
        else:
            analysis.extract_attempts = 0  # 진행이 있었으니 재시도 카운트 리셋.

        if analysis.extract_attempts >= settings.max_extract_attempts:
            # 같은 논문을 영원히 재제출해 Gemini 크레딧을 낭비하지 않도록 여기서 끊는다.
            analysis.status = "failed"
            analysis.error = (
                f"논문 {len(still_pending)}건을 {settings.max_extract_attempts}회 재시도해도 "
                f"추출하지 못해 중단했습니다. Gemini 응답 파싱 실패가 반복되고 있을 수 있습니다."
            )
            db.commit()
            return

        # 청크가 여러 개면 아직 제출 못한 논문이 남아 있다. reducing으로 넘기지 않고
        # extracting에 머물러 다음 루프에서 다음 청크를 제출한다.
        if still_pending:
            db.commit()
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

    # C2: batch_job_id가 채워진(=진행 중인) analysis 수가 상한 이상이면 제출을 미룬다.
    # 이 검사가 없으면 loop()가 active analysis를 전부 순회하며 각자 submit을 부르므로
    # 동시 batch 잡 수가 설정 상한을 몇 배씩 넘어갈 수 있다. status는 extracting에
    # 그대로 두어 다음 루프 주기(30초)에 재시도한다.
    in_progress = db.query(Analysis).filter(Analysis.batch_job_id.isnot(None)).count()
    if in_progress >= settings.batch_max_concurrent_jobs:
        logger.info(
            "[잡 %d] batch 동시 실행 상한(%d/%d) 도달 — 제출 보류, 다음 루프에서 재시도",
            analysis.id, in_progress, settings.batch_max_concurrent_jobs,
        )
        return

    requests = mapper.build_requests(pending)
    batches = mapper.chunks(requests)
    # 청크가 여러 개면 첫 청크만 제출하고 나머지는 다음 루프에서 이어간다 — 동시 batch
    # 잡 수 상한을 넘기지 않는 가장 단순한 방법. 그 첫 청크도 batch_max_enqueued_tokens를
    # 넘으면 안 되므로 mapper.estimate_tokens 기반으로 한 번 더 자른다.
    first_batch = mapper.token_capped_chunk(pending[:len(batches[0])], batches[0])
    analysis.batch_job_id = await gemini_batch.submit_async(first_batch, analysis.id)
    db.commit()


async def _do_reduce(db: Session, analysis: Analysis) -> None:
    papers = _analysis_papers(db, analysis)
    papers_by_key = {p.paper_key: p for p in papers}
    extractions = db.query(PaperExtraction).filter(
        PaperExtraction.paper_key.in_(list(papers_by_key)),
        PaperExtraction.subfield_id == analysis.subfield_id,
        PaperExtraction.model_ver == mapper.model_ver(),
    ).all()

    prior_analyzed_count = analysis.analyzed_count
    current_model_ver = mapper.model_ver()
    new_count = len(extractions)

    # C4: 신규 추출이 없으면(건수가 늘지 않았으면) LLM을 다시 부르지 않는다 — 실측상
    # 재실행 1회 비용의 약 47%가 보고서 재생성이다. model_ver가 바뀌면 같은 논문
    # 집합이 전량 재추출된 것이라 건수가 그대로여도 "늘어난 것"으로 취급해야 하므로
    # analyzed_count 비교만이 아니라 report_model_ver로 이를 별도로 확인한다.
    skip_reduce = (
        analysis.report_md is not None
        and new_count <= prior_analyzed_count
        and analysis.report_model_ver == current_model_ver
    )
    if skip_reduce:
        logger.info(
            "[잡 %d] 신규 추출 없음(%d→%d건, model_ver 동일) — 보고서 재생성 생략, 통계만 갱신",
            analysis.id, prior_analyzed_count, new_count,
        )
    else:
        analysis.report_md = await reducer.reduce_subfield(db, analysis, extractions, papers_by_key)
        analysis.report_model_ver = current_model_ver

    # 통계는 인용수 등 값이 싸게 바뀌므로 스킵 여부와 무관하게 항상 다시 계산한다.
    analysis.stats_json = stats.compute(
        papers, extractions, snapshot_at=analysis.snapshot_at or datetime.now(timezone.utc)
    )
    analysis.analyzed_count = new_count
    analysis.status = "done"
    db.add(AnalysisRun(
        analysis_id=analysis.id,
        ran_at=datetime.now(timezone.utc),
        searched_count=analysis.searched_count,
        analyzed_count=new_count,
        new_papers=max(new_count - prior_analyzed_count, 0),
        trigger=analysis.trigger,
    ))
    db.commit()


def _now_schedule_tz() -> datetime:
    return datetime.now(ZoneInfo(settings.schedule_timezone))


def _is_schedule_due(now: datetime) -> bool:
    return now.day == settings.schedule_day and now.hour == settings.schedule_hour


def next_scheduled_run_at(now: datetime | None = None) -> datetime:
    """다음 예정 실행 시각(스케줄 타임존 기준, tzinfo 없이) — 관리자 대시보드 표시용."""
    now = now or _now_schedule_tz()
    this_month = now.replace(day=settings.schedule_day, hour=settings.schedule_hour,
                              minute=0, second=0, microsecond=0, tzinfo=None)
    if now.replace(tzinfo=None) < this_month:
        return this_month
    year, month = now.year, now.month + 1
    if month > 12:
        year, month = year + 1, 1
    return this_month.replace(year=year, month=month)


def run_scheduled_if_due(db: Session, *, now: datetime | None = None) -> int | None:
    """매월 schedule_day일 schedule_hour시대(schedule_timezone)에 활성 세부기술 전부를
    당해~(당해-schedule_years_back)연도로 enqueue한다.

    **force=True인 이유**: enqueue()는 status="done"이고 query_hash가 그대로면 아무것도
    하지 않는다. 스케줄러가 force=False로 부르면 이미 완료된 분석은 매달 건너뛰어져
    "그 사이 새로 등재된 논문을 잡는다"는 스케줄러의 존재 이유가 사라진다.
    force=True로 검색을 매번 다시 돌리되, 신규 논문이 0건이면 _do_reduce가 보고서
    재생성을 생략하므로 실질 비용은 검색분(약 $0.004)에 그친다.

    ScheduledRun.run_month unique 제약이 멱등성의 근거다 — 컨테이너가 그 시간대에
    재시작돼 잡 루프가 다시 돌아도, 같은 달 두 번째 삽입은 IntegrityError로 막혀
    조용히 건너뛴다(budget.py::_row와 같은 패턴).
    """
    if not settings.schedule_enabled:
        return None
    now = now or _now_schedule_tz()
    if not _is_schedule_due(now):
        return None

    run_month = f"{now.year:04d}-{now.month:02d}"
    row = ScheduledRun(run_month=run_month, ran_at=now.replace(tzinfo=None), queued_count=0)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.debug("[스케줄러] %s 이미 실행됨 — 건너뜀", run_month)
        return None

    years = [now.year - i for i in range(settings.schedule_years_back + 1)]
    subfields = db.query(Subfield).filter(Subfield.active.is_(True)).all()
    queued = 0
    for subfield in subfields:
        for year in years:
            queued += len(enqueue(db, subfield, year, year, force=True, trigger="scheduled"))

    row.queued_count = queued
    db.commit()
    logger.info(
        "[스케줄러] %s 월간 자동 분석 큐잉 완료 — 활성 세부기술 %d개 × 연도 %s, %d건 큐잉",
        run_month, len(subfields), years, queued,
    )
    return queued


async def loop() -> None:
    """미완 잡을 주기적으로 스캔해 전진시킨다. 상태가 전부 DB에 있으므로
    프로세스가 죽었다 살아나도 그대로 이어진다."""
    logger.info("잡 루프 시작 (%d초 간격)", settings.loop_interval_seconds)
    while True:
        try:
            db = SessionLocal()
            try:
                run_scheduled_if_due(db)
                resume_paused(db)
                active = db.query(Analysis).filter(Analysis.status.in_(ACTIVE_STATES)).all()
                for analysis in active:
                    await advance(db, analysis)
            finally:
                db.close()
        except Exception:
            logger.exception("잡 루프 순회 실패 — 다음 주기에 재시도")
        await asyncio.sleep(settings.loop_interval_seconds)
