from datetime import date, datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.budget import OpenAlexUsage


class BudgetExceeded(RuntimeError):
    pass


def _today() -> date:
    return datetime.now(timezone.utc).date()


def reset_time_utc() -> datetime:
    """OpenAlex 예산이 리셋되는 다음 UTC 자정."""
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _row(db: Session) -> OpenAlexUsage:
    row = db.query(OpenAlexUsage).filter(OpenAlexUsage.usage_date == _today()).first()
    if row:
        return row
    row = OpenAlexUsage(usage_date=_today(), cost_usd=0.0)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # 다른 세션이 같은 usage_date 행을 먼저 커밋한 경우(동시 첫 요청).
        # 롤백 후 재조회하면 그 행을 찾을 수 있다.
        db.rollback()
        row = db.query(OpenAlexUsage).filter(OpenAlexUsage.usage_date == _today()).first()
        if row is None:
            raise
    return row


def spent_today(db: Session) -> float:
    return _row(db).cost_usd


def check_budget(db: Session, estimated_cost: float) -> None:
    """예상 비용을 더해도 이 서비스 몫을 넘지 않는지 확인한다.

    # ponytail: check→호출→record 사이에 잠금이 없어 동시 실행 시 한도를 소폭 넘을 수 있다.
    # 기본 예산이 실제 한도($1/day)의 절반이라 이를 흡수한다. 정확한 상한이 필요해지면
    # usage 행에 SELECT ... FOR UPDATE 잠금을 건다.
    """
    spent = spent_today(db)
    projected = spent + estimated_cost
    if projected > settings.openalex_daily_budget_usd:
        raise BudgetExceeded(
            f"OpenAlex 일일 예산 초과: 사용 ${spent:.4f} + 예상 ${estimated_cost:.4f} "
            f"> 한도 ${settings.openalex_daily_budget_usd:.2f}. "
            f"UTC {reset_time_utc():%Y-%m-%d %H:%M} 이후 재시도하세요."
        )


def record_usage(db: Session, cost_usd: float, remaining: str | None) -> None:
    """실제 발생 비용을 누적하고, 서버가 보고한 잔여값을 함께 남긴다.

    remaining은 공유 키를 쓰는 다른 서비스의 소비까지 반영된 실측값이라
    자체 누적치보다 신뢰도가 높다 — 진단용으로 보존한다.
    """
    row = _row(db)
    row.cost_usd += cost_usd
    if remaining is not None:
        row.remaining_reported = remaining
    db.commit()
