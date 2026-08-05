import pytest
from sqlalchemy.orm import Query, sessionmaker

from app.services.budget import BudgetExceeded, check_budget, record_usage, spent_today


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


def test_first_usage_row_commits_to_release_lock(db, monkeypatch):
    """spent_today가 그날 첫 usage 행을 만들 때 flush만 하고 커밋하지 않으면, 그
    INSERT의 usage_date 유니크 락이 요청 끝까지 유지된다 — async 엔드포인트가 그 락을
    쥔 채 외부 API를 await하다 멈추면 커넥션 풀이 통째로 묶인다(실측: idle in
    transaction 12시간, 첫 화면·관리자 전면 hang). 첫 행 생성 경로는 즉시 커밋해
    락을 놓아야 한다."""
    committed = []
    real_commit = db.commit
    monkeypatch.setattr(db, "commit", lambda: committed.append(True) or real_commit())
    spent_today(db)  # 그날 첫 행을 새로 만든다
    assert committed, "첫 usage 행 생성 시 커밋되지 않았다 — flush만 하면 락이 요청 끝까지 유지된다"
