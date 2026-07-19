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


class AnalysisPaper(Base):
    __tablename__ = "analysis_papers"
    __table_args__ = (UniqueConstraint("analysis_id", "paper_id", name="uq_analysis_paper"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), nullable=False, index=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False)
