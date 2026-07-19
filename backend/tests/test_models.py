from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.field import Field, Subfield
from app.models.analysis import Analysis


def test_subfield_holds_queries_and_analysis_is_unique_per_year():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    f = Field(name="반도체·디스플레이", slug="semiconductor", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="HBM", query="HBM memory", query_kci=None)
    db.add(sf)
    db.flush()

    db.add(Analysis(subfield_id=sf.id, year=2025, status="pending", query_hash="abc"))
    db.commit()

    assert db.query(Subfield).one().query == "HBM memory"
    assert db.query(Analysis).one().status == "pending"
