"""answers: answer runs, claims, claim evidence

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-18 11:17:22.578450+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("search_run_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("answer_kind", sa.String(length=30), nullable=True),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("candidates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("llm_outputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("timings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("langfuse_trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'insufficient_evidence', 'needs_clarification', 'needs_review', 'conflict', 'failed', 'cancelled')",
            name="ck_answer_runs_status",
        ),
        sa.ForeignKeyConstraint(["search_run_id"], ["search_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_answer_runs_created_at"), "answer_runs", ["created_at"], unique=False)
    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["answer_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_claims_run_id"), "claims", ["run_id"], unique=False)
    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("quote_start", sa.Integer(), nullable=False),
        sa.Column("quote_end", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["element_id"],
            ["elements.id"],
        ),
        sa.PrimaryKeyConstraint("claim_id", "element_id"),
    )
    op.add_column("llm_calls", sa.Column("answer_run_id", sa.Uuid(), nullable=True))
    op.add_column("llm_calls", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.create_foreign_key(None, "llm_calls", "answer_runs", ["answer_run_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint(None, "llm_calls", type_="foreignkey")
    op.drop_column("llm_calls", "output_tokens")
    op.drop_column("llm_calls", "answer_run_id")
    op.drop_table("claim_evidence")
    op.drop_index(op.f("ix_claims_run_id"), table_name="claims")
    op.drop_table("claims")
    op.drop_index(op.f("ix_answer_runs_created_at"), table_name="answer_runs")
    op.drop_table("answer_runs")
