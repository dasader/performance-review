"""strip HTML/MathML tags and decode entities in existing papers.title/abstract/journal

OpenAlex가 논문 제목·초록에 HTML 태그를 섞어 보내는 경우가 있었다(예:
`Hf <sub>0.5</sub> Zr <sub>0.5</sub> O <sub>2</sub>`). app.clients.openalex._parse_work가
이제 수집 시점에 벗기지만(app.clients._html.strip_html), 이미 저장된 578건 중 다수는
그대로 남아 있어 여기서 한 번 정리한다.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.clients._html import strip_html

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_papers = sa.table(
    "papers",
    sa.column("id", sa.Integer),
    sa.column("title", sa.Text),
    sa.column("abstract", sa.Text),
    sa.column("journal", sa.Text),
)


def upgrade() -> None:
    """수집 시점 헬퍼(app.clients._html.strip_html)와 완전히 동일한 규칙으로 기존
    행을 정리한다 — 로직을 SQL로 다시 구현하면 두 규칙이 갈라질 위험이 있어 헬퍼를
    그대로 임포트해 파이썬에서 적용한다."""
    bind = op.get_bind()
    rows = bind.execute(sa.select(_papers.c.id, _papers.c.title, _papers.c.abstract, _papers.c.journal)).fetchall()
    for row in rows:
        new_title = strip_html(row.title)
        new_abstract = strip_html(row.abstract)
        new_journal = strip_html(row.journal) if row.journal else row.journal
        if new_title == row.title and new_abstract == row.abstract and new_journal == row.journal:
            continue
        bind.execute(
            _papers.update()
            .where(_papers.c.id == row.id)
            .values(title=new_title, abstract=new_abstract, journal=new_journal)
        )


def downgrade() -> None:
    """되돌릴 수 없음(no-op).

    strip_html이 태그를 지우는 순간 원본 태그 정보는 사라진다(예: 어느 문자 구간이
    <sub>였는지) — 정리 전 상태를 복원할 방법이 없다. 필요하면 백업에서 복구해야 한다.
    """
    pass
