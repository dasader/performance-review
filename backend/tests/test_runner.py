import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.services import runner


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    f = Field(name="양자", slug="quantum", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="양자컴퓨팅", query="quantum computing")
    db.add(sf)
    db.commit()
    return db, sf


def test_enqueue_creates_one_analysis_per_year(ctx):
    db, sf = ctx
    created = runner.enqueue(db, sf, 2024, 2026, force=False)
    assert {a.year for a in created} == {2024, 2025, 2026}
    assert all(a.status == "pending" for a in created)


def test_enqueue_skips_done_analysis_with_matching_hash(ctx):
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=False)
    assert again == []
    assert db.query(Analysis).count() == 1


def test_enqueue_reruns_when_query_changed(ctx):
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    db.commit()

    sf.query = "quantum error correction"   # 검색식 변경 → query_hash 불일치
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=False)
    assert len(again) == 1
    assert again[0].status == "pending"
    assert db.query(Analysis).count() == 1  # 같은 행을 재사용


def test_enqueue_force_reruns_even_when_fresh(ctx):
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=True)
    assert len(again) == 1


def test_step_labels_are_korean():
    assert runner.STEP_LABELS["searching"] == "논문 검색 중"
    assert runner.STEP_LABELS["extracting"] == "성과 추출 중"
    assert runner.STEP_LABELS["reducing"] == "보고서 작성 중"
    assert runner.STEP_LABELS["done"] == "완료"
