import hashlib
from datetime import date, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services import _time
from app.config import settings
from app.models.visit import Visit




def _client_hash(ip: str, user_agent: str, day: date) -> str:
    raw = f"{ip}{user_agent}{settings.visitor_salt}{day.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_visit(db: Session, ip: str, user_agent: str) -> None:
    """공개 페이지 요청 1건을 오늘 날짜의 순방문자로 기록한다.

    같은 (날짜, 해시) 조합은 unique 제약에 걸린다 — budget.py::_row와 같은 패턴으로
    IntegrityError를 잡아 롤백하고 조용히 무시한다(이미 기록된 방문자).
    """
    day = _time.today()
    db.add(Visit(usage_date=day, visitor_hash=_client_hash(ip, user_agent, day)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def week_start(day: date) -> date:
    """월요일 시작 기준 이번 주의 첫날."""
    return day - timedelta(days=day.weekday())


def visitor_stats(db: Session) -> dict:
    today = _time.today()
    start = week_start(today)

    rows = (
        db.query(Visit.usage_date, Visit.visitor_hash)
        .filter(Visit.usage_date >= start, Visit.usage_date <= today)
        .all()
    )
    by_day: dict[date, set[str]] = {}
    for d, h in rows:
        by_day.setdefault(d, set()).add(h)

    daily = []
    d = start
    while d <= today:
        daily.append({"date": d.isoformat(), "count": len(by_day.get(d, ()))})
        d += timedelta(days=1)

    week_hashes = {h for hashes in by_day.values() for h in hashes}
    return {
        "today": len(by_day.get(today, ())),
        "this_week": len(week_hashes),
        "daily": daily,
    }
