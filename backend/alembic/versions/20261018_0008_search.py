"""search: pgvector / pg_trgm, chunks, embeddings, search runs, llm calls

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-18 10:02:55.956293+00:00
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ベクトル検索（pgvector）と日本語を含む部分一致・類似度検索（pg_trgm）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "search_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scope_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expanded_terms", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("timings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_search_runs_created_at"), "search_runs", ["created_at"], unique=False)
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=12, scale=6, asdecimal=False), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("extraction_id", sa.Uuid(), nullable=True),
        sa.Column("search_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["extraction_id"], ["extractions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["search_run_id"], ["search_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_calls_created_at"), "llm_calls", ["created_at"], unique=False)
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('text', 'table')", name="ck_chunks_kind"),
        sa.ForeignKeyConstraint(["extraction_id"], ["extractions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("extraction_id", "seq"),
    )
    op.create_index(op.f("ix_chunks_extraction_id"), "chunks", ["extraction_id"], unique=False)
    op.create_index(
        "ix_chunks_search_text_trgm",
        "chunks",
        ["search_text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
    op.create_table(
        "chunk_elements",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["element_id"], ["elements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chunk_id", "element_id", "start_offset"),
    )
    op.create_index(op.f("ix_chunk_elements_element_id"), "chunk_elements", ["element_id"], unique=False)
    op.create_table(
        "embeddings",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("model_key", sa.String(length=100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chunk_id", "model_key"),
    )
    op.create_table(
        "retrieval_hits",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("lexical_rank", sa.Integer(), nullable=True),
        sa.Column("lexical_score", sa.Float(), nullable=True),
        sa.Column("vector_rank", sa.Integer(), nullable=True),
        sa.Column("vector_score", sa.Float(), nullable=True),
        sa.Column("rrf_score", sa.Float(), nullable=False),
        sa.Column("final_rank", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
        ),
        sa.ForeignKeyConstraint(["run_id"], ["search_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "chunk_id"),
    )
    op.add_column(
        "extractions", sa.Column("index_status", sa.String(length=20), server_default="none", nullable=False)
    )
    op.add_column("extractions", sa.Column("index_error", sa.Text(), nullable=True))
    op.add_column("extractions", sa.Column("index_model", sa.String(length=100), nullable=True))
    op.add_column("extractions", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("extractions", "indexed_at")
    op.drop_column("extractions", "index_model")
    op.drop_column("extractions", "index_error")
    op.drop_column("extractions", "index_status")
    op.drop_table("retrieval_hits")
    op.drop_table("embeddings")
    op.drop_index(op.f("ix_chunk_elements_element_id"), table_name="chunk_elements")
    op.drop_table("chunk_elements")
    op.drop_index(
        "ix_chunks_search_text_trgm",
        table_name="chunks",
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
    op.drop_index(op.f("ix_chunks_extraction_id"), table_name="chunks")
    op.drop_table("chunks")
    op.drop_index(op.f("ix_llm_calls_created_at"), table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index(op.f("ix_search_runs_created_at"), table_name="search_runs")
    op.drop_table("search_runs")
