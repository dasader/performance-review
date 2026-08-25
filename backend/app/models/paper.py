from sqlalchemy import Integer, String, Text, UniqueConstraint
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
    # 캐시 키에 subfield_id가 없다: 추출 프롬프트(prompts.MAP_INSTRUCTION +
    # map_user_text(title, abstract))에 세부기술이 전혀 들어가지 않으므로, 같은 논문을
    # 세부기술마다 다시 추출하면 **같은 입력에 같은 일을 다시 시키고 두 번 과금**하는
    # 것이다. 실측(2026-08-25): 20.7만 행 중 22,718행(11%)이 그런 중복이었고, 텍스트가
    # 서로 달랐던 것은 LLM 샘플링이 비결정적이기 때문이지 세부기술 때문이 아니다
    # (동일 요약 0.1%). 분야가 겹치는 논문이 많은 과거연도를 넣을수록 이 비율은 커진다.
    #
    # **추출 프롬프트에 세부기술을 넣게 되면 이 키를 되돌려야 한다** — 그때는 같은
    # 논문의 추출 결과가 세부기술마다 달라지므로 공유하면 틀린 결과를 재사용하게 된다.
    # test_mapper.py::test_pending_reuses_extraction_from_another_subfield 가 현재
    # 동작을, test_mapper.py::test_map_prompt_stays_subfield_independent 가 그 전제를
    # 고정한다.
    __table_args__ = (
        UniqueConstraint("paper_key", "model_ver", name="uq_extraction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_key: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    tech_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    achievement_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metrics_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    approach: Mapped[str | None] = mapped_column(Text, nullable=True)
    improvement: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_ver: Mapped[str] = mapped_column(String(80), nullable=False)
