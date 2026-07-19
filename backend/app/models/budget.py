from datetime import date

from sqlalchemy import Date, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OpenAlexUsage(Base):
    """UTC 날짜별 자체 사용액 누적 + 마지막으로 관측한 서버측 잔여값."""

    __tablename__ = "openalex_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    remaining_reported: Mapped[str | None] = mapped_column(String(50), nullable=True)
