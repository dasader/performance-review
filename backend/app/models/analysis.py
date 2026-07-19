from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database import Base


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (UniqueConstraint("subfield_id", "year", name="uq_analysis_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subfield_id: Mapped[int] = mapped_column(ForeignKey("subfields.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # pending | searching | extracting | reducing | done | failed | paused
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    report_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    searched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analyzed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sampled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    batch_job_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 배치 결과 저장 후에도 pending_papers가 줄지 않은(파싱 실패 등으로 진행이 없는)
    # 연속 횟수. max_extract_attempts에 도달하면 무한 재제출을 끊고 failed로 전환한다.
    extract_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 검색 단계에서 비영구(non-permanent) RateLimited를 만난 연속 횟수(get_with_retry가
    # 내부 재시도를 이미 소진한 뒤 올라온 것). max_search_attempts에 도달하면 30초마다
    # 같은 페이지들을 무한히 재과금하며 도는 대신 failed로 전환한다.
    search_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 이 행을 마지막으로 pending으로 되돌린(=활성화한) 원인. manual(관리자 수동 실행) |
    # scheduled(월간 스케줄러). done에 도달할 때 AnalysisRun.trigger로 그대로 옮겨 적어
    # 수동/자동 실행이 실제로 새 논문을 얼마나 찾아내는지 나중에 비교할 수 있게 한다.
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    # 마지막으로 report_md를 생성했을 때의 mapper.model_ver(). 재실행 시 model_ver가
    # 바뀌면(모델 교체·추출 스키마 버전 상향) 같은 논문 집합이 전량 재추출되어 추출
    # 건수는 그대로일 수 있다 — analyzed_count 비교만으로는 "건수가 그대로니 재추출도
    # 없었다"로 오판하므로, 이 값으로 model_ver 변경 자체를 별도 신호로 잡는다.
    report_model_ver: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # 이번 실행(enqueue가 이 행을 pending으로 되돌린 시점부터 done까지) 동안
    # mapper.save_results()가 실제로 LLM 결과를 저장한 논문 수의 누적치. 추출은
    # batch_max_requests_per_file 단위로 여러 청크로 쪼개져 여러 루프 틱에 걸쳐
    # 저장되므로(_do_extract) 메모리 변수로는 셀 수 없다 — 이 컬럼에 누적하고,
    # _do_reduce가 done 시점에 AnalysisRun.new_papers로 그대로 옮겨 적는다.
    # enqueue()가 이 행을 새로 만들거나 되살릴 때만 0으로 리셋한다(그 외에는 계속 누적).
    extracted_this_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AnalysisPaper(Base):
    __tablename__ = "analysis_papers"
    __table_args__ = (UniqueConstraint("analysis_id", "paper_id", name="uq_analysis_paper"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), nullable=False, index=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False)
