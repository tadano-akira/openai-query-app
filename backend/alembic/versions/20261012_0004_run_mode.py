"""run mode

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12 11:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("mode", sa.String(40), nullable=True))
    # 導入前の実行は基本ルールのみ（standard と同じ指示）だった
    op.execute("UPDATE runs SET mode = 'standard'")


def downgrade() -> None:
    op.drop_column("runs", "mode")
