"""secret detections

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18 01:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secret_detections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("findings", postgresql.JSONB(), nullable=False),
        sa.Column("prompt_chars", sa.Integer(), nullable=False),
    )
    op.create_index("ix_secret_detections_created_at", "secret_detections", ["created_at"])


def downgrade() -> None:
    op.drop_table("secret_detections")
