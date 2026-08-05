from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScheduleSetting(Base):
    """월간 자동 분석 스케줄의 런타임 설정 — 단일 행(싱글턴, id=1)만 존재한다.

    .env의 SCHEDULE_ENABLED/DAY/HOUR/YEARS_BACK은 이제 "초기 기본값"으로만 쓰인다 —
    이 행이 없으면 그 값으로 한 행을 만들고, 이후에는 이 행이 우선한다(재기동 없이
    관리자 화면에서 변경 가능, app.services.runner.get_schedule_settings 참고).

    schedule_timezone은 여기 포함하지 않는다 — 타임존 변경은 드물고, 잘못된 값이
    들어가면 ZoneInfo가 즉시 실패해 스케줄러 전체가 멈춘다. .env 전용으로 남기고
    관리자 화면에는 읽기 전용으로만 보여준다.
    """

    __tablename__ = "schedule_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    years_back: Mapped[int] = mapped_column(Integer, nullable=False)
    # 스케줄러가 돌 국가. 콤마 구분("KR,US,CN"). 기본 KR이라 켜기 전에는 현행과 같다.
    countries: Mapped[str] = mapped_column(String(100), nullable=False, default="KR")
    # 대상 국가 분석이 전부 done이 되면 국가 비교를 자동으로 큐잉한다.
    #
    # 분석과 같은 시점에 큐잉할 수 없다 — 비교는 모든 대상국 분석이 done이어야 만들 수
    # 있는데(collect_country_analyses), 스케줄이 큐잉한 수백 건이 끝나기까지 시간이
    # 걸리므로 그 시점에 같이 큐잉하면 전부 "상대국 분석 없음"으로 건너뛰어진다.
    # 그래서 잡 루프가 매 틱마다 "준비된 것"을 찾는 방식이다(runner.enqueue_due_comparisons).
    #
    # 만드는 것은 **다국 비교 하나뿐**이다. 3개국 이상이면 process_comparison이 쌍별
    # 1:1을 먼저 만들어 sections_json에 넣고 그것을 종합하므로, 1:1을 따로 큐잉하면
    # 같은 결과물을 다시 만드는 셈이다(세부기술·연도당 국가수-1콜 중복).
    auto_comparison: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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

    run_month은 정기 실행이면 "YYYY-MM"(멱등성 키, unique). 관리자가 "지금 실행"으로
    즉시 실행하면 "YYYY-MM-manual-HHMMSSffffff" 형식을 써서 그 달의 정기 실행 키와
    절대 겹치지 않게 한다(app.services.runner.run_scheduled_now 참고) — 수동 실행이
    그 달의 정기 실행을 막아버리면 안 되기 때문이다. trigger로 두 종류를 구분한다.
    """

    __tablename__ = "scheduled_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_month: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
