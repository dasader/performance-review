import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from app.clients import gemini_batch
from app.clients._http import RateLimited
from app.config import settings
from app.database import SessionLocal
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import CountryComparison, FieldReport, RoadmapCheck, Subfield
from app.models.paper import Paper, PaperExtraction
from app.models.schedule import AnalysisRun, ScheduledRun, ScheduleSetting
from app.services import comparison, mapper, reducer, search, stats
from app.services._countries import parse_countries
from app.services._time import utcnow
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


def enqueue(
    db: Session, subfield: Subfield, year_from: int, year_to: int, *, force: bool,
    trigger: str = "manual", country: str = "KR",
) -> list[Analysis]:
    """연도별 Analysis를 만들거나 되살린다.

    이미 done이고 query_hash가 같으면 건너뛴다(재호출 방지). 검색식이 바뀌었으면
    같은 행을 pending으로 되돌려 증분 재실행한다 — 프리즈는 두지 않는다.

    trigger는 이 행을 활성화한 원인(manual|scheduled)을 남긴다 — done에 도달할 때
    AnalysisRun에 그대로 기록되어 수동/자동 실행이 실제로 새 논문을 얼마나 찾아내는지
    나중에 비교할 수 있게 한다.

    비활성 세부기술은 ValueError로 거부한다(호출부가 사유로 옮긴다).
    """
    if not subfield.active:
        # 비활성은 "목록에서 감춘 것"이 아니라 "돌리지 않기로 한 것"이다.
        # 예전에는 실행 화면이 active 목록만 보여주는 것이 유일한 방어였는데, 관리자
        # IA를 재편하며 그 화면들이 사라져 방어가 통째로 없어졌다 — 대시보드는 비활성도
        # 함께 내려주므로 여기서 막지 않으면 꺼둔 세부기술에 검색·추출이 그대로 돈다.
        # 호출부마다 막지 않고 여기서 막는 이유: 큐잉 경로가 셋이고 하나만 빠뜨려도
        # 같은 사고가 난다(_queue_all_active는 이미 active만 고르므로 여기 걸리지 않는다).
        raise ValueError(f"{subfield.name}은(는) 비활성 세부기술입니다.")

    queued: list[Analysis] = []
    for year in range(year_from, year_to + 1):
        current_hash = search.query_hash(subfield, year, year, country)
        row = db.query(Analysis).filter(
            Analysis.subfield_id == subfield.id,
            Analysis.year == year,
            Analysis.country == country,
        ).first()

        if row is None:
            row = Analysis(subfield_id=subfield.id, year=year, status="pending",
                           query_hash=current_hash, trigger=trigger,
                           extracted_this_run=0, country=country)
            db.add(row)
            queued.append(row)
        elif row.batch_job_id and row.status in ACTIVE_STATES:
            # C5: 진행 중인 batch가 있으면 force여도 건드리지 않는다. batch_job_id를
            # 비우면 Gemini에서 이미 돌고 있는(=과금되는) 잡의 핸들을 잃고 같은 논문을
            # 통째로 재제출하게 된다 — 청크당 최대 batch_max_requests_per_file건이라
            # 한 번 밟을 때마다 그만큼을 두 번 지불하고 한 번만 쓴다.
            # 그대로 두면 _do_extract가 다음 틱에 폴링을 이어받아 정상적으로 끝낸다.
            #
            # status가 failed인 행은 여기 걸리지 않는다(ACTIVE_STATES 밖) — batch가
            # 실제로 실패해 죽은 잡이므로 아래 분기에서 핸들을 비우고 재시작하는 게 맞다.
            logger.info(
                "[잡 %d] batch 진행 중(%s) — 재실행 요청을 무시하고 폴링을 이어간다",
                row.id, row.batch_job_id,
            )
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
            # 이번에 새로 시작하는 실행의 추출 건수를 세는 카운터라, 지난 실행에서
            # 누적된 값이 이번 AnalysisRun.new_papers에 섞여 들어가면 안 된다.
            row.extracted_this_run = 0
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


def resume_paused(db: Session) -> None:
    """paused 상태 analysis를 예산 여유가 생겼으면 pending으로 되돌린다.

    OpenAlex 예산 사용액은 UTC 날짜별 행으로 관리되므로(budget.spent_today),
    자정이 지나면 별도 컬럼 없이도 이 값이 자연히 0으로 리셋된다 — 이를 재개
    신호로 쓴다. ACTIVE_STATES에는 paused를 넣지 않는다: advance()가 paused를
    전진시키면 안 되고, 재개는 이 함수가 전담한다.

    한도를 아직 넘지 않았어도 **가장 싼 분석 한 건조차 못 치를 잔액이면 되돌리지
    않는다.** 예전에는 `>= 한도`만 봤는데, 실측(2026-08-24) 사용액이 $0.4990/$0.50에서
    멈추자 이 게이트를 계속 통과해 25건이 30초마다 paused→pending→paused를 오갔다
    (로그 1,003회). 되돌아간 각 건은 check_budget보다 **먼저** 도는 count_only를
    실제로 한 번씩 호출하는데, 게이트에 막히면 record_usage까지 가지 못해 그 비용이
    기록조차 되지 않는다. 게다가 그렇게 쌓인 전량이 UTC 자정 리셋 순간 한꺼번에
    OpenAlex로 쏟아진 것이 504 무더기(분석 9건 동시 failed)의 방아쇠였다.
    """
    # 분석 한 건의 하한: count 1콜 + 최소 1페이지. 실제 견적은 search.collect가
    # 건수를 보고 다시 계산하므로, 여기서는 "0건은 아니다"만 보장하면 된다.
    min_cost = 2 * settings.openalex_search_cost_usd
    if spent_today(db) + min_cost > settings.openalex_daily_budget_usd:
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
        result = await search.collect(
            db, subfield, analysis.year, analysis.year,
            client=client, country=analysis.country,
        )

    # 상한을 넘으면 거부하지 않고 인용 상위 N건을 수집한다(openalex.search가
    # sort=cited_by_count:desc로 받는다). 거부하면 CN 11개·US 3개 세부기술이 그냥
    # 실패한다(실측). 잘렸다는 사실은 stats의 population_total·sampled가 드러낸다.
    #
    # total_count는 _do_reduce 시점에 다시 얻을 수 없으므로(그때는 DB의 논문 링크만
    # 본다) 여기서 stats_json에 실어 둔다. _do_reduce가 읽어 stats.compute에 넘긴다.
    analysis.stats_json = {
        **(analysis.stats_json or {}),
        "population_total": result.total_count,
    }

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
        # save_results가 저장한 건 전부 mapper.pending_papers(캐시에 없는 논문)로만
        # 구성된 요청의 결과다 — 즉 saved는 "덮어쓴 총 행 수"가 아니라 "이번 실행에서
        # 실제로 LLM을 돌려 새로 얻은 결과 수"(비용이 발생한 건수)와 같다. 여러 청크에
        # 걸쳐 여러 번 저장되므로 누적한다(_do_reduce가 done 시점에 AnalysisRun로 옮긴다).
        analysis.extracted_this_run += saved
        analysis.batch_job_id = None
        db.commit()

        # _analysis_papers를 여기서 다시 부른다. save_results가 commit하고 세션은
        # expire_on_commit=True(기본값)라, 저장 전에 실어둔 Paper 인스턴스를 재사용하면
        # 속성 접근마다 행 단위 refresh가 나간다 — 실측 1,128건 기준 SELECT 1,130회 대
        # 재조회 3회로, 아끼려던 쿼리보다 훨씬 크게 손해다.
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
    # report_md 검사를 맨 뒤에 둔다 — loop()가 이 컬럼을 defer로 빼놨기 때문에 이걸
    # 먼저 보면 값싼 두 비교로 걸러낼 수 있는 경우에도 12KB 본문을 굳이 읽어온다.
    skip_reduce = (
        analysis.report_model_ver == current_model_ver
        and new_count <= prior_analyzed_count
        and analysis.report_md is not None
    )
    if skip_reduce:
        logger.info(
            "[잡 %d] 신규 추출 없음(%d→%d건, model_ver 동일) — 보고서 재생성 생략, 통계만 갱신",
            analysis.id, prior_analyzed_count, new_count,
        )
    else:
        # skip_reduce 분기에서는 sections_json을 건드리지 않는다 — 지우면 화면에서
        # 세부 보고서가 사라진다(재생성을 생략했는데 내용이 줄어드는 셈).
        analysis.report_md, analysis.sections_json = await reducer.reduce_subfield(
            db, analysis, extractions, papers_by_key
        )
        analysis.report_model_ver = current_model_ver

    # 통계는 인용수 등 값이 싸게 바뀌므로 스킵 여부와 무관하게 항상 다시 계산한다.
    analysis.stats_json = stats.compute(
        papers, extractions,
        snapshot_at=analysis.snapshot_at or datetime.now(timezone.utc),
        country=analysis.country,
        population_total=(analysis.stats_json or {}).get("population_total"),
    )
    analysis.analyzed_count = new_count
    analysis.status = "done"
    db.add(AnalysisRun(
        analysis_id=analysis.id,
        ran_at=datetime.now(timezone.utc),
        searched_count=analysis.searched_count,
        analyzed_count=new_count,
        # M18: new_count - prior_analyzed_count(총계의 차이)가 아니라 이번 실행에서
        # 실제로 추출한 건수(extracted_this_run)를 쓴다 — model_ver가 바뀌어 논문
        # 전량이 재추출돼도 총계는 그대로일 수 있어(전량 재추출 = 재추출 건수는 총계와
        # 같은데 차이는 0), 총계 차이만 보면 "신규 추출 없음"으로 오판한다.
        new_papers=analysis.extracted_this_run,
        trigger=analysis.trigger,
    ))
    db.commit()


_SCHEDULE_SETTING_ID = 1


def get_schedule_settings(db: Session) -> ScheduleSetting:
    """스케줄 설정 싱글턴 행을 가져온다. 없으면 .env 값을 초기 기본값으로 한 행을 만든다.

    행이 생성된 뒤로는 이 값이 .env보다 우선한다 — 관리자 화면에서 PUT /admin/schedule로
    바꾸면 재기동 없이 다음 잡 루프 틱부터 바로 반영된다.
    """
    row = db.get(ScheduleSetting, _SCHEDULE_SETTING_ID)
    if row is not None:
        return row
    row = ScheduleSetting(
        id=_SCHEDULE_SETTING_ID,
        enabled=settings.schedule_enabled,
        day=settings.schedule_day,
        hour=settings.schedule_hour,
        years_back=settings.schedule_years_back,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # 동시 요청이 먼저 만든 경우(budget.py::_row와 같은 패턴).
        db.rollback()
        row = db.get(ScheduleSetting, _SCHEDULE_SETTING_ID)
    return row


def _now_schedule_tz() -> datetime:
    return datetime.now(ZoneInfo(settings.schedule_timezone))


def _is_schedule_due(now: datetime, *, day: int, hour: int) -> bool:
    return now.day == day and now.hour == hour


def next_scheduled_run_at(db: Session, now: datetime | None = None) -> datetime:
    """다음 예정 실행 시각(스케줄 타임존 기준, tzinfo 없이) — 관리자 화면 표시용."""
    cfg = get_schedule_settings(db)
    now = now or _now_schedule_tz()
    this_month = now.replace(day=cfg.day, hour=cfg.hour,
                              minute=0, second=0, microsecond=0, tzinfo=None)
    if now.replace(tzinfo=None) < this_month:
        return this_month
    year, month = now.year, now.month + 1
    if month > 12:
        year, month = year + 1, 1
    return this_month.replace(year=year, month=month)


def _queue_all_active(
    db: Session, cfg: ScheduleSetting, now: datetime, *, trigger: str
) -> tuple[int, int, list[int]]:
    """활성 세부기술 전부 × 당해~(당해-years_back)연도를 force=True로 큐잉한다.

    정기 실행(run_scheduled_if_due)과 수동 즉시 실행(run_scheduled_now)이 공유하는 부분.
    두 함수가 진짜로 다른 것은 run_month 키 구성과 due 판정뿐이므로 그쪽은 호출부에 남긴다.
    반환값은 (큐잉 건수, 활성 세부기술 수, 대상 연도) — 뒤 둘은 호출부 로그 문구에 쓰인다.
    """
    years = [now.year - i for i in range(cfg.years_back + 1)]
    # 콤마 구분 목록. 기본 "KR"이라 켜기 전에는 현행과 같다. 국가마다 검색·추출이
    # 따로 돌아 비용이 곱해지므로 관리자가 명시적으로 켜야 한다.
    countries = parse_countries(cfg.countries or "KR")
    subfields = db.query(Subfield).filter(Subfield.active.is_(True)).all()
    queued = 0
    for subfield in subfields:
        for year in years:
            for country in countries:
                queued += len(enqueue(db, subfield, year, year, force=True,
                                      trigger=trigger, country=country))
    return queued, len(subfields), years


def enqueue_due_comparisons(db: Session, *, now: datetime | None = None) -> int:
    """대상국 분석이 전부 done인 세부기술·연도의 국가 비교를 큐잉한다(매 틱 확인).

    **분석과 같은 시점에 큐잉할 수 없어서 이렇게 한다.** 비교는 모든 대상국 분석이
    done이어야 만들 수 있는데(collect_country_analyses가 하나라도 없으면 거부),
    월간 스케줄이 큐잉한 수백 건이 끝나기까지는 며칠이 걸린다 — 그 시점에 같이
    큐잉하면 전부 "상대국 분석 없음"으로 건너뛰어져 아무것도 만들어지지 않는다.
    그래서 잡 루프가 매 틱마다 "이제 준비된 것"을 찾는다.

    **다국 비교 하나만 만든다.** 3개국 이상이면 process_comparison이 쌍별 1:1을 먼저
    만들어 sections_json에 넣고 그것을 종합하므로, 1:1을 따로 큐잉하면 같은 결과물을
    다시 만드는 셈이다(세부기술·연도당 국가수-1콜 중복). 1:1 조회는 공개 API가 다국
    보고서의 해당 섹션으로 폴백한다.

    이미 행이 있으면 건너뛴다 — enqueue_comparison은 재생성(status를 pending으로
    되돌림)이라, 확인 없이 부르면 매 틱 같은 비교를 무한히 다시 만든다.

    55개 세부기술 × 연도를 매 틱(30초) 도는 경로라 질의를 2개로 묶는다.
    """
    cfg = get_schedule_settings(db)
    if not cfg.auto_comparison:
        return 0
    countries = parse_countries(cfg.countries)
    if len(countries) < 2:
        return 0

    now = now or _now_schedule_tz()
    years = [now.year - i for i in range(cfg.years_back + 1)]
    key = ",".join(sorted(countries))

    # ① 이미 만들어졌거나 만들고 있는 조합
    existing = {
        (sid, yr)
        for sid, yr in db.query(CountryComparison.subfield_id, CountryComparison.year).filter(
            CountryComparison.year.in_(years), CountryComparison.countries == key
        )
    }
    # ② 대상국 분석이 done이고 본문이 있는 것(빈 보고서는 비교 입력이 못 된다)
    ready: dict[tuple[int, int], set[str]] = {}
    for sid, yr, country in (
        db.query(Analysis.subfield_id, Analysis.year, Analysis.country)
        .join(Subfield, Subfield.id == Analysis.subfield_id)
        .filter(
            Subfield.active.is_(True),
            Analysis.year.in_(years),
            Analysis.country.in_(countries),
            Analysis.status == "done",
            Analysis.report_md.isnot(None),
            Analysis.report_md != "",
        )
    ):
        ready.setdefault((sid, yr), set()).add(country)

    queued = 0
    for (sid, yr), have in sorted(ready.items()):
        if (sid, yr) in existing or not set(countries) <= have:
            continue
        try:
            comparison.enqueue_comparison(db, sid, yr, countries)
            queued += 1
        except (LookupError, ValueError) as e:
            # 검증에 걸리는 건은 조용히 건너뛴다 — 하나가 막혀 나머지가 멈추면 안 된다.
            # 여기는 잡 루프라 사유를 돌려줄 상대가 없다(관리자가 부르는 /admin/queue는
            # skipped로 사유를 낸다) — 로그로만 남긴다.
            logger.debug("[비교 자동] 세부기술 %d %d년 건너뜀: %s", sid, yr, e)
    if queued:
        db.commit()
        logger.info("[비교 자동] %s %s년 — %d건 큐잉", key, years, queued)
    return queued


def run_scheduled_if_due(db: Session, *, now: datetime | None = None) -> int | None:
    """매월 (DB) schedule_day일 schedule_hour시대(schedule_timezone)에 활성 세부기술
    전부를 당해~(당해-schedule_years_back)연도로 enqueue한다. 설정은 get_schedule_settings로
    DB에서 읽는다(.env는 행이 없을 때의 초기 기본값일 뿐이다).

    **force=True인 이유**: enqueue()는 status="done"이고 query_hash가 그대로면 아무것도
    하지 않는다. 스케줄러가 force=False로 부르면 이미 완료된 분석은 매달 건너뛰어져
    "그 사이 새로 등재된 논문을 잡는다"는 스케줄러의 존재 이유가 사라진다.
    force=True로 검색을 매번 다시 돌리되, 신규 논문이 0건이면 _do_reduce가 보고서
    재생성을 생략하므로 실질 비용은 검색분(약 $0.004)에 그친다.

    ScheduledRun.run_month unique 제약이 멱등성의 근거다 — 컨테이너가 그 시간대에
    재시작돼 잡 루프가 다시 돌아도, 같은 달 두 번째 삽입은 IntegrityError로 막혀
    조용히 건너뛴다(budget.py::_row와 같은 패턴).
    """
    cfg = get_schedule_settings(db)
    if not cfg.enabled:
        return None
    now = now or _now_schedule_tz()
    if not _is_schedule_due(now, day=cfg.day, hour=cfg.hour):
        return None

    run_month = f"{now.year:04d}-{now.month:02d}"
    row = ScheduledRun(run_month=run_month, ran_at=now.replace(tzinfo=None), queued_count=0,
                        trigger="scheduled")
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.debug("[스케줄러] %s 이미 실행됨 — 건너뜀", run_month)
        return None

    queued, subfield_count, years = _queue_all_active(db, cfg, now, trigger="scheduled")
    row.queued_count = queued
    db.commit()
    logger.info(
        "[스케줄러] %s 월간 자동 분석 큐잉 완료 — 활성 세부기술 %d개 × 연도 %s, %d건 큐잉",
        run_month, subfield_count, years, queued,
    )
    return queued


def run_scheduled_now(db: Session, *, now: datetime | None = None) -> int:
    """관리자 화면의 "지금 실행" — 스케줄 시각 판정을 우회해 즉시 1회 큐잉한다.

    run_scheduled_if_due와 같은 모양(활성 세부기술 전부 × 당해~(당해-years_back)연도,
    force=True)으로 큐잉하되 두 가지가 다르다:
    - trigger를 "manual"로 남긴다 — 이 실행으로 완료된 건은 AnalysisRun.trigger에도
      "manual"로 기록되어, "정기 스케줄러가 실제로 새 논문을 얼마나 찾는가"라는
      원래 통계 목적이 즉흥 실행으로 흐려지지 않는다.
    - ScheduledRun.run_month를 "YYYY-MM"이 아니라 "YYYY-MM-manual-HHMMSSffffff"로
      만든다. run_month unique 제약이 그 달의 정기 실행 멱등성 키이므로, 여기서
      "YYYY-MM"을 그대로 쓰면 (a) 이 수동 실행이 먼저 들어갔을 때 그 달 정기 실행이
      IntegrityError로 막히거나, (b) 정기 실행이 먼저 있었을 때 이 수동 실행이
      거부된다 — 어느 쪽이든 "수동 실행이 정기 실행을 막으면 안 된다"는 요구를
      깬다. 접미사를 붙여 두 키가 절대 같은 문자열이 될 수 없게 한다.
      (마이크로초까지 포함하므로 같은 초 안에 버튼을 여러 번 눌러도 충돌하지 않는다.
      루프가 자동 재시도하는 경로가 아니라 사용자가 직접 누르는 단발 액션이라
      run_scheduled_if_due처럼 IntegrityError catch로 멱등성을 보장할 필요는 없다.)
    """
    now = now or _now_schedule_tz()
    cfg = get_schedule_settings(db)
    run_key = f"{now.year:04d}-{now.month:02d}-manual-{now:%H%M%S%f}"
    row = ScheduledRun(run_month=run_key, ran_at=now.replace(tzinfo=None), queued_count=0,
                        trigger="manual")
    db.add(row)
    db.flush()

    queued, subfield_count, years = _queue_all_active(db, cfg, now, trigger="manual")
    row.queued_count = queued
    db.commit()
    logger.info(
        "[스케줄러] 수동 즉시 실행 완료 — 활성 세부기술 %d개 × 연도 %s, %d건 큐잉",
        subfield_count, years, queued,
    )
    return queued


def schedule_history(db: Session, *, limit: int = 12) -> list[dict]:
    """관리자 화면의 "최근 실행 이력" — ScheduledRun 최신 limit건 + 성공 여부 요약(근사치).

    done_count: 이 실행의 ran_at ~ (시간순 다음 ScheduledRun의 ran_at, 없으면 지금) 구간에
    같은 trigger로 완료(AnalysisRun 생성)된 건수. ScheduledRun.ran_at은 스케줄 타임존
    기준 naive 값이고 AnalysisRun.ran_at은 UTC 기준이라 비교 전 UTC로 맞춘다. "이 구간에
    완료됐다"는 "이 실행이 큐잉한 것"의 근사치다 — AnalysisRun에 실행 ID를 직접 연결하는
    FK가 없어 정확히 귀속시킬 수는 없다(다음 실행 전에 완료된 것이라 이 실행 소관일
    가능성이 높다고 보는 수준).

    failed_count/paused_count/in_progress_count: "지금 이 순간" 같은 trigger의 Analysis
    상태 집계다. Analysis는 (subfield_id, year)별로 매번 덮어써지므로, 같은 trigger로
    더 나중에 실행된 적이 있다면 이 값은 그 나중 실행 결과로 이미 갈아치워진 것이라
    이 행 소관이 아니다 — 그래서 같은 trigger 중 "가장 최근" 실행 행에만 채우고
    (is_current_snapshot=True), 더 오래된 행은 0으로 둔다.
    """
    # 내림차순 limit건 한 번만 싣는다. "시간순 다음 실행"은 이 정렬에서 바로 앞 행이고
    # (rows[i-1]), trigger별 최신 실행도 각 trigger가 처음 등장하는 행이다 — 전체 테이블을
    # 다시 싣고 행마다 선형 탐색할 필요가 없다(scheduled_runs는 실행할 때마다 늘어난다).
    rows = db.query(ScheduledRun).order_by(ScheduledRun.ran_at.desc()).limit(limit).all()
    if not rows:
        return []

    latest_ran_at_by_trigger: dict[str, datetime] = {}
    for r in rows:
        latest_ran_at_by_trigger.setdefault(r.trigger, r.ran_at)  # 내림차순이라 첫 값이 최신

    # 상태 집계도 (trigger, status)별로 한 번에 세어 둔다 — is_current 행마다 3개씩
    # 따로 세면 같은 테이블을 6번 훑는다.
    status_counts: dict[tuple[str, str], int] = {
        (trigger, status): count
        for trigger, status, count in db.query(
            Analysis.trigger, Analysis.status, func.count(Analysis.id)
        ).group_by(Analysis.trigger, Analysis.status).all()
    }

    tz = ZoneInfo(settings.schedule_timezone)

    def to_utc_naive(schedule_tz_naive: datetime) -> datetime:
        return schedule_tz_naive.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)

    result = []
    for i, row in enumerate(rows):
        next_ran_at = rows[i - 1].ran_at if i else None  # 내림차순 — 바로 앞 행이 다음 실행
        window_start = to_utc_naive(row.ran_at)
        window_end = (
            to_utc_naive(next_ran_at) if next_ran_at else utcnow()
        )
        done_count = db.query(AnalysisRun).filter(
            AnalysisRun.trigger == row.trigger,
            AnalysisRun.ran_at >= window_start,
            AnalysisRun.ran_at < window_end,
        ).count()

        is_current = latest_ran_at_by_trigger.get(row.trigger) == row.ran_at
        if is_current:
            failed_count = status_counts.get((row.trigger, "failed"), 0)
            paused_count = status_counts.get((row.trigger, "paused"), 0)
            in_progress_count = sum(
                status_counts.get((row.trigger, s), 0) for s in ACTIVE_STATES
            )
        else:
            failed_count = paused_count = in_progress_count = 0

        result.append({
            "run_month": row.run_month,
            "ran_at": row.ran_at.isoformat(),
            "trigger": row.trigger,
            "queued_count": row.queued_count,
            "done_count": done_count,
            "failed_count": failed_count,
            "paused_count": paused_count,
            "in_progress_count": in_progress_count,
            "is_current_snapshot": is_current,
        })
    return result


async def advance_field_reports(db: Session) -> None:
    """pending인 분야 종합 보고서·로드맵 점검·국가 비교를 한 틱에 하나씩 처리한다.

    한 틱에 전부 부르지 않는 이유: 각 생성이 LLM 1콜(약 10~17초)이라, 일괄로 큐잉된
    수십 건을 한 루프에서 다 돌리면 루프가 수 분간 블로킹돼 세부기술 분석 잡까지
    밀리고 RPM 버킷도 압박받는다. 가장 오래 기다린 것부터 하나씩, 세부기술 분석과
    자원을 나눠 쓴다(느리지만 rate-limit 철학과 일치).

    분야 종합을 로드맵 점검보다 먼저 처리한다 — 점검이 더 오래 걸려(17초) 종합이
    그 뒤에 줄서면 오래 대기하기 때문이다. 국가 비교는 입력이 가장 커(국가 수만큼
    보고서가 붙는다) 맨 뒤에 둔다.
    """
    # 순서가 곧 우선순위다 — 위에서부터 pending 행을 찾아 첫 하나만 처리한다.
    for model, processor, label in (
        (FieldReport, reducer.process_field_report, "분야 종합"),
        (RoadmapCheck, reducer.process_roadmap_check, "로드맵 점검"),
        (CountryComparison, comparison.process_comparison, "국가 비교"),
    ):
        row = db.query(model).filter(model.status == "pending").order_by(model.id).first()
        if row is not None:
            await _process_report(db, row, processor, label)
            return


async def _process_report(db: Session, row, processor, label: str) -> None:
    """report 행 하나를 처리하고, 실패하면 status=failed + error로 남긴다.
    한 건의 실패가 루프 전체를 멈추지 않게 여기서 흡수한다(세부기술 잡의 advance와 대칭).

    row는 FieldReport·RoadmapCheck·CountryComparison 중 하나라 공통 컬럼이 id·year뿐이다
    — field_id를 직접 읽으면 비교 행(subfield_id를 가진다)에서 AttributeError가 난다."""
    try:
        logger.info("[%s] id=%d year=%d 처리 시작", label, row.id, row.year)
        await processor(db, row)
        logger.info("[%s] id=%d year=%d 완료", label, row.id, row.year)
    except Exception as e:
        logger.exception("[%s] id=%d year=%d 실패", label, row.id, row.year)
        db.rollback()
        row.status = "failed"
        row.error = str(e)
        db.commit()


async def _tick(db: Session) -> None:
    """루프 한 주기. 테스트가 sleep 없이 한 틱만 돌릴 수 있게 분리했다.

    ★ 분석을 먼저 전진시키고 보고서를 나중에 처리한다. 비교 하나가 쌍별 포함 최대
    2분 걸리는데 그것이 앞에 있으면 그 틱의 세부기술 진행이 통째로 밀린다 —
    보고서 합성은 검색·추출 파이프라인보다 우선이 아니다.
    """
    run_scheduled_if_due(db)
    enqueue_due_comparisons(db)
    resume_paused(db)
    # report_md(보고서 마크다운, 건당 12KB 규모)·stats_json·sections_json은 advance()가
    # 읽지 않는다 — _do_reduce가 쓰기만 한다. defer하지 않으면 30초마다
    # 활성 분석 전체의 보고서 본문을 통째로 읽어온다(월간 실행 직후엔
    # 55개 세부기술 × 연도 규모라 수 MB에 이른다). 지연 로딩이라
    # _do_reduce의 대입은 그대로 동작한다.
    # sections_json은 3단 reduce의 성과유형별 중간 보고서들이라 report_md보다
    # 크다 — 이것만 빠뜨리면 위 defer가 막으려던 비용의 대부분이 그대로 남는다.
    active = (
        db.query(Analysis)
        .filter(Analysis.status.in_(ACTIVE_STATES))
        .options(
            defer(Analysis.report_md),
            defer(Analysis.stats_json),
            defer(Analysis.sections_json),
        )
        .all()
    )
    for analysis in active:
        await advance(db, analysis)
    await advance_field_reports(db)


async def loop() -> None:
    """미완 잡을 주기적으로 스캔해 전진시킨다. 상태가 전부 DB에 있으므로
    프로세스가 죽었다 살아나도 그대로 이어진다."""
    logger.info("잡 루프 시작 (%d초 간격)", settings.loop_interval_seconds)
    while True:
        try:
            db = SessionLocal()
            try:
                await _tick(db)
            finally:
                db.close()
        except Exception:
            logger.exception("잡 루프 순회 실패 — 다음 주기에 재시도")
        await asyncio.sleep(settings.loop_interval_seconds)
