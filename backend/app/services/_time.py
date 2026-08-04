"""시각 헬퍼 — DB 컬럼의 tz 규약을 한 곳에 둔다.

네 모듈(reducer·comparison·budget·visitors)이 같은 두 줄을 각자 갖고 있었다.
줄 수보다 **규약이 갈라지는 것**이 문제다 — 이 프로젝트는 이미 tz 의미가 섞여 있어
(ScheduledRun.ran_at은 스케줄 타임존 naive, AnalysisRun.ran_at은 UTC) 한 곳만 고치면
조용히 어긋난다.
"""

from datetime import date, datetime, timezone


def utcnow() -> datetime:
    """DB의 naive DateTime 컬럼에 맞춘 현재 UTC 시각(tzinfo 제거)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today() -> date:
    """UTC 기준 오늘. 예산 일일 한도와 방문자 집계가 이 경계를 쓴다."""
    return datetime.now(timezone.utc).date()
