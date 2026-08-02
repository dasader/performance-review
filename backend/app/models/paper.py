from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database import Base


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    abstract: Mapped[str] = mapped_column(Text, nullable=False, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    journal: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    authors_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    institutions_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    countries_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    citations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # openalex | kci | both — "both"는 양쪽에서 같은 논문이 걸렸다는 뜻이다
    # (search.combine_source, I10). stats.by_source가 이 세 값을 그대로 구분해 센다.
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    # 교신저자(authorships[].is_corresponding)의 소속국 코드. countries_json이
    # "참여"라면 이쪽은 "주도"다 — 실측으로 일본 논문의 47%가 자국이 주도하지 않은
    # 국제공동연구라, 둘을 구분하지 않으면 국가별 숫자를 같은 의미로 오독한다.
    # OpenAlex authorships에 이미 들어 있어 추가 API 호출이 없다(보유율 91~94%).
    lead_countries_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class PaperExtraction(Base):
    __tablename__ = "paper_extractions"
    __table_args__ = (
        UniqueConstraint("paper_key", "subfield_id", "model_ver", name="uq_extraction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_key: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    subfield_id: Mapped[int] = mapped_column(ForeignKey("subfields.id"), nullable=False)
    tech_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    achievement_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metrics_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    approach: Mapped[str | None] = mapped_column(Text, nullable=True)
    improvement: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_ver: Mapped[str] = mapped_column(String(80), nullable=False)
