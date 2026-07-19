import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.budget import BudgetExceeded, check_budget, record_usage, spent_today


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_usage_accumulates_within_the_same_day(db):
    record_usage(db, 0.01, "0.9")
    record_usage(db, 0.02, "0.87")
    assert spent_today(db) == pytest.approx(0.03)


def test_check_budget_passes_under_limit(db):
    record_usage(db, 0.1, None)
    check_budget(db, 0.1)  # 0.2 < 0.5 → 통과


def test_check_budget_blocks_when_projected_over_limit(db):
    record_usage(db, 0.45, None)
    with pytest.raises(BudgetExceeded) as e:
        check_budget(db, 0.1)  # 0.55 > 0.5
    assert "예산" in str(e.value)
