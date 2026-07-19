"""모든 모델을 한 곳에서 import — Alembic autogenerate와 FK 해석이 이 파일에 의존한다."""

from app.models.analysis import Analysis, AnalysisPaper  # noqa: F401
from app.models.budget import OpenAlexUsage  # noqa: F401
from app.models.field import Field, Subfield  # noqa: F401
from app.models.paper import Paper, PaperExtraction  # noqa: F401
