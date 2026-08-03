"""국가 비교 보고서 — 모델·대조표·큐잉·처리."""

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CountryComparison


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_country_comparison_roundtrip():
    """국가 목록은 콤마 문자열로 저장되고, 생성 전 기본값은 FieldReport와 같다."""
    db = _session()
    db.add(
        CountryComparison(
            subfield_id=1,
            year=2026,
            countries="CN,KR,US",
            generated_at=datetime(2026, 8, 3),
        )
    )
    db.commit()

    saved = db.query(CountryComparison).one()
    assert saved.countries == "CN,KR,US"
    # 생성 전에는 빈 본문 — 재생성 중에도 옛 본문을 남기기 위해 nullable이 아니다
    assert saved.status == "done"
    assert saved.report_md == ""
    assert saved.source_count == 0
