import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Query, sessionmaker

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


def test_row_recovers_from_concurrent_integrity_error(db, monkeypatch):
    """두 세션이 동시에 그날 첫 행을 만들려 할 때 IntegrityError를 흡수하는지 확인.

    세션 A(=db)가 먼저 커밋해 행을 선점한 뒤, 세션 B의 최초 조회가 그 행을
    놓쳤다고 강제로 흉내 낸다(실제 동시 요청이라면 A의 커밋 전에 조회했을 상황).
    그 결과 세션 B는 자기 나름의 insert를 시도해 unique 제약과 충돌하는데,
    _row가 이를 잡아 롤백 후 재조회로 복구해야 한다.
    """
    session_b = sessionmaker(bind=db.get_bind())()

    # 세션 A가 먼저 오늘자 행을 만들어 커밋 (경쟁에서 이긴 쪽).
    record_usage(db, 0.01, None)

    # 세션 B의 첫 조회만 강제로 빈 결과를 반환하게 해 A의 커밋 전에 조회한
    # 것처럼 흉내낸다. 이후 세션 B의 insert가 A의 행과 충돌해야 한다.
    original_first = Query.first
    calls = {"n": 0}

    def fake_first(self):
        calls["n"] += 1
        return None if calls["n"] == 1 else original_first(self)

    monkeypatch.setattr(Query, "first", fake_first)

    # IntegrityError 없이 A가 만든 행을 재조회해 반환해야 한다.
    assert spent_today(session_b) == pytest.approx(0.01)
