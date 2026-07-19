import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.clients import gemini_batch
from app.clients._http import RateLimited
from app.config import settings
from app.database import SessionLocal
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import Subfield
from app.models.paper import Paper, PaperExtraction
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
    db: Session, subfield: Subfield, year_from: int, year_to: int, *, force: bool
) -> list[Analysis]:
    """연도별 Analysis를 만들거나 되살린다.

    이미 done이고 query_hash가 같으면 건너뛴다(재호출 방지). 검색식이 바뀌었으면
    같은 행을 pending으로 되돌려 증분 재실행한다 — 프리즈는 두지 않는다.
    """
    queued: list[Analysis] = []
    for year in range(year_from, year_to + 1):
        current_hash = search.query_hash(subfield, year, year)
        row = db.query(Analysis).filter(
            Analysis.subfield_id == subfield.id, Analysis.year == year
        ).first()

        if row is None:
            row = Analysis(subfield_id=subfield.id, year=year, status="pending",
                           query_hash=current_hash)
            db.add(row)
            queued.append(row)
        elif force or row.status in ("failed", "paused") or row.query_hash != current_hash:
            row.status = "pending"
            row.query_hash = current_hash
            row.error = None
            row.batch_job_id = None
            row.extract_attempts = 0  # M11: 재시도 카운터를 리셋하지 않으면 상한에
            row.search_attempts = 0   # 걸려 failed된 잡이 재실행 즉시 다시 failed된다.
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

    analysis.searched_count = len(rows)
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
    analysis.batch_job_id = await gemini_batch.submit_async(first_batch)
    db.commit()


async def _do_reduce(db: Session, analysis: Analysis) -> None:
    papers = _analysis_papers(db, analysis)
    papers_by_key = {p.paper_key: p for p in papers}
    extractions = db.query(PaperExtraction).filter(
        PaperExtraction.paper_key.in_(list(papers_by_key)),
        PaperExtraction.subfield_id == analysis.subfield_id,
        PaperExtraction.model_ver == mapper.model_ver(),
    ).all()

    analysis.stats_json = stats.compute(
        papers, extractions, snapshot_at=analysis.snapshot_at or datetime.now(timezone.utc)
    )
    analysis.analyzed_count = len(extractions)
    analysis.report_md = await reducer.reduce_subfield(db, analysis, extractions, papers_by_key)
    analysis.status = "done"
    db.commit()


async def loop() -> None:
    """미완 잡을 주기적으로 스캔해 전진시킨다. 상태가 전부 DB에 있으므로
    프로세스가 죽었다 살아나도 그대로 이어진다."""
    logger.info("잡 루프 시작 (%d초 간격)", settings.loop_interval_seconds)
    while True:
        try:
            db = SessionLocal()
            try:
                resume_paused(db)
                active = db.query(Analysis).filter(Analysis.status.in_(ACTIVE_STATES)).all()
                for analysis in active:
                    await advance(db, analysis)
            finally:
                db.close()
        except Exception:
            logger.exception("잡 루프 순회 실패 — 다음 주기에 재시도")
        await asyncio.sleep(settings.loop_interval_seconds)
