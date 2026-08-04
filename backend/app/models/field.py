from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Field(Base):
    __tablename__ = "fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    subfields: Mapped[list["Subfield"]] = relationship(back_populates="field")


class Subfield(Base):
    __tablename__ = "subfields"
    __table_args__ = (UniqueConstraint("field_id", "name", name="uq_subfield_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("fields.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_kci: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    field: Mapped[Field] = relationship(back_populates="subfields")

    def kci_query(self) -> str:
        """KCI override가 비어 있으면 공통 검색식을 쓴다."""
        return self.query_kci or self.query


class FieldReport(Base):
    """대분류(분야) 보고서 = 하위 세부기술 보고서를 LLM 1콜로 합성한 결과의 캐시.

    Analysis와 달리 상태머신이 없다 — 입력이 이미 완성된 세부기술 보고서라
    검색·추출 단계가 없고, 관리자가 누를 때 한 번에 만들어진다.
    """

    __tablename__ = "field_reports"
    __table_args__ = (UniqueConstraint("field_id", "year", name="uq_field_report_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("fields.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # pending(큐잉됨, 잡 루프가 처리 예정) | done | failed. 관리자가 "생성"을 누르면
    # 즉시 LLM을 부르지 않고 pending 행만 만든 뒤 runner.loop이 한 틱에 하나씩 처리한다
    # — 여러 분야를 일괄로 큐잉해도 API가 동시에 얻어맞지 않게.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="done")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending 첫 생성 시엔 빈 문자열이다(아직 만들어지지 않음). 재생성 중이면 옛 본문을
    # 그대로 두고 status만 pending으로 두어, 처리 완료 전까지 이전 보고서를 계속 보여준다.
    report_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 합성에 실제로 들어간 세부기술 보고서 수. 이후 새 세부기술이 done에 도달하면
    # 지금의 done 개수와 어긋나므로, 조회 시 "재생성이 필요한가"의 근거가 된다.
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Roadmap(Base):
    """분야별 전략기술로드맵 원문(마크다운). 분야당 한 판본만 들고 있는다 —
    이력 관리가 필요해지면 그때 version을 키에 넣는다.

    ⚠ 이 원문은 보고서 생성 시 Gemini API로 전송된다. 비공개 로드맵을 넣을지는
    관리자가 판단하며, 화면에도 그 사실을 명시한다. 외부로 내보낼 수 없는 판본을
    다뤄야 하면 생성 경로를 로컬 모델로 분기해야 하고, 그때 바뀌는 것은 이 모델이
    아니라 reducer.check_roadmap이 부르는 클라이언트뿐이다(README 참고).
    """

    __tablename__ = "roadmaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("fields.id"), nullable=False, unique=True)
    # "2026 제1호 개정" 같은 자유 문자열. 보고서에 어느 판본 기준인지 남기는 용도.
    version_label: Mapped[str] = mapped_column(String(200), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RoadmapCheck(Base):
    """로드맵 이행 점검 보고서 캐시. 분야 종합 보고서(FieldReport)와 별개로 둔다 —
    로드맵이 없는 분야도 종합 보고서는 쓸 수 있어야 하고, 로드맵만 개정됐을 때
    점검만 다시 돌릴 수 있어야 한다.
    """

    __tablename__ = "roadmap_checks"
    __table_args__ = (UniqueConstraint("field_id", "year", name="uq_roadmap_check_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("fields.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # FieldReport와 동일한 큐잉 상태 — 관리자 "생성"은 pending 큐잉일 뿐이고 실제
    # LLM 호출은 runner.loop이 한 틱에 하나씩 처리한다.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="done")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 점검 대상이었던 로드맵 목표 행 수(코드로 센 값). 보고서의 표 행 수가 이 값과
    # 다르면 전수 점검이 깨진 것이므로, 생성 직후 검증해 그 결과를 함께 남긴다.
    goal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 생성 시점의 로드맵 판본. 이후 로드맵이 바뀌면 재생성 필요 신호가 된다.
    roadmap_version: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class CountryComparison(Base):
    """같은 세부기술·연도의 국가별 분석을 합성한 비교 보고서 캐시.

    FieldReport와 같은 큐잉 패턴이다 — 관리자가 "생성"을 누르면 pending 행만 만들고
    실제 LLM 호출은 runner.loop이 한 틱에 하나씩 처리한다.

    countries가 유일키에 포함되는 이유: 같은 세부기술·연도라도 "KR,US"와 "KR,US,CN"은
    다른 보고서다. 국가 조합을 바꿔 만들어도 기존 것을 덮어쓰지 않는다.
    """

    __tablename__ = "country_comparisons"
    __table_args__ = (
        UniqueConstraint("subfield_id", "year", "countries", name="uq_comparison"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subfield_id: Mapped[int] = mapped_column(ForeignKey("subfields.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # 정렬된 콤마 구분 국가 코드("CN,KR,US"). 정렬해 저장하는 이유는 같은 조합을
    # 다른 순서로 요청해도 같은 행을 재사용하기 위해서다 — 안 그러면 같은 비교가
    # 순서만 바꿔 여러 행으로 쌓인다.
    countries: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="done")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 재생성 중에도 옛 본문을 남긴다(FieldReport와 같은 이유) — 처리가 끝나기 전까지
    # 이전 보고서를 계속 보여주기 위해서다.
    report_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 합성에 실제로 들어간 국가 수.
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 쌍별 비교 보고서. analyses.sections_json과 같은 모양이고 화면도 같은 펼침
    # 패턴을 쓴다. 3개국 이상에서 국가별 내용이 축약되지 않게 하는 장치다 —
    # 종합 1콜만 두면 국가가 늘수록 각 나라 몫이 줄어든다(2단계 이중 압축과 같은 문제).
    sections_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
