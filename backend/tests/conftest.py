import os

# ponytail: app.config imports a module-level `settings = Settings()` singleton,
# which requires these env vars even when a test only needs Settings(...) with
# explicit kwargs. Set harmless defaults here (not a .env file) so importing
# app.config doesn't crash under pytest. Explicit kwargs in tests still win.
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("OPENALEX_API_KEY", "test-key")
os.environ.setdefault("ADMIN_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test/test")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.field import Field, Subfield


@pytest.fixture
def db():
    """빈 인메모리 sqlite 세션.

    다섯 개 테스트 파일이 이 세 줄을 각자 갖고 있었다. test_api.py·test_visitors.py는
    여기 오지 않는다 — 그쪽은 StaticPool + dependency_overrides가 필요해(FastAPI가
    동기 엔드포인트를 워커 스레드에서 돌린다) 조건이 다르다.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


@pytest.fixture
def ctx(db):
    """(세션, 세부기술) — 분야·세부기술 하나가 심어진 상태.

    Subfield.active는 모델 기본값이 True라 따로 지정하지 않는다.
    """
    f = Field(name="양자", slug="quantum", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="양자컴퓨팅", query="quantum computing")
    db.add(sf)
    db.commit()
    return db, sf
