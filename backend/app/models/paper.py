from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
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
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # openalex | kci
    korea_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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
    model_ver: Mapped[str] = mapped_column(String(80), nullable=False)
