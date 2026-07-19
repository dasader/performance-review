from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
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
