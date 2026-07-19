from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnalysisRun(Base):
    """analyses가 done에 도달할 때마다 남기는 실행 이력.

    OpenAlex의 from_created_date 필터가 유료 플랜 전용이라 "과거 연도 논문이 실제로
    얼마나 늘어나는가"를 직접 조회할 수 없다 — 대신 실행할 때마다 이 행을 쌓아 몇 달 뒤
    실측 증가 곡선을 데이터로 확인한다. 조회 API는 이번 범위 밖이라 기록만 남긴다.
    """

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), nullable=False, index=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    searched_count: Mapped[int] = mapped_column(Integer, nullable=False)
    analyzed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    new_papers: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)  # manual | scheduled


class ScheduledRun(Base):
    """월간 스케줄러가 실제로 큐잉을 실행한 달의 기록 — run_month unique 제약이
    멱등성의 근거다. 컨테이너가 실행 시각대(예: 매월 10일 03시 KST)에 재시작돼
    잡 루프가 다시 돌아도, 같은 run_month로 두 번째 삽입은 IntegrityError로 막힌다.

    ran_at은 스케줄 타임존(KST) 기준 시각을 그대로(naive) 저장한다 — 이 표는 관리자
    화면에 "다음/마지막 실행 시각(KST)"을 보여주기 위한 값이라, 나머지 테이블처럼
    UTC로 두면 매번 변환해야 해서 오히려 헷갈린다.
    """

    __tablename__ = "scheduled_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_month: Mapped[str] = mapped_column(String(7), nullable=False, unique=True)  # "2026-08"
    ran_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
