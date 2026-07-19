from datetime import date

from sqlalchemy import Date, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Visit(Base):
    """공개 페이지 요청을 (날짜, 방문자 해시) 단위로 기록해 일별 순방문자 수를 센다.

    원본 IP/User-Agent는 저장하지 않는다 — visitor_hash는 sha256(ip+ua+salt+날짜)라
    되돌릴 수 없다. unique 제약이 같은 날 같은 방문자의 중복 기록을 막는다.
    """

    __tablename__ = "visits"
    __table_args__ = (UniqueConstraint("usage_date", "visitor_hash", name="uq_visit_day_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    visitor_hash: Mapped[str] = mapped_column(String(64), nullable=False)
