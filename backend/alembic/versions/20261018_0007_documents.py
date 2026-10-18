"""document q&a: issuers, documents, versions, extractions, pages, elements, jobs, audit

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18 09:13:06.392724+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(length=254), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_target_id"), "audit_events", ["target_id"], unique=False)
    op.create_table(
        "issuers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_jobs_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_status_lease", "jobs", ["status", "lease_until"], unique=False)
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issuer_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "document_type IN ('tanshin_q1', 'tanshin_q2', 'tanshin_q3', 'tanshin_fy', 'securities_report', 'semiannual_report', 'quarterly_report', 'other')",
            name="ck_documents_type",
        ),
        sa.ForeignKeyConstraint(
            ["issuer_id"],
            ["issuers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_documents_issuer_id"), "documents", ["issuer_id"], unique=False)
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("original_filename", sa.String(length=300), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("acquisition", sa.String(length=20), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("period_label", sa.String(length=100), nullable=True),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("accounting_standard", sa.String(length=40), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("official_status", sa.String(length=20), nullable=False),
        sa.Column("official_confirmed_by", sa.String(length=254), nullable=True),
        sa.Column("official_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("official_memo", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("correction_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "official_status IN ('unconfirmed', 'confirmed', 'rejected')",
            name="ck_document_versions_official",
        ),
        sa.CheckConstraint(
            "scope IN ('consolidated', 'standalone', 'mixed', 'unknown')", name="ck_document_versions_scope"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["document_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_document_versions_document_id"), "document_versions", ["document_id"], unique=False
    )
    op.create_index(
        "ix_document_versions_period",
        "document_versions",
        ["document_id", "period_end", "published_on"],
        unique=False,
    )
    op.create_index(op.f("ix_document_versions_sha256"), "document_versions", ["sha256"], unique=False)
    op.create_table(
        "extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("parser_version", sa.String(length=100), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("quality", sa.String(length=30), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=254), nullable=True),
        sa.Column("approval_memo", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "quality IS NULL OR quality IN ('ok', 'needs_review', 'partial', 'unsupported_no_text')",
            name="ck_extractions_quality",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_extractions_status"
        ),
        sa.CheckConstraint("NOT active OR approved_at IS NOT NULL", name="ck_extractions_active_approved"),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_extractions_version_id"), "extractions", ["version_id"], unique=False)
    op.create_index(
        "uq_extractions_active_per_version",
        "extractions",
        ["version_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("printed_page_label", sa.String(length=40), nullable=True),
        sa.Column("pdf_page_label", sa.String(length=40), nullable=True),
        sa.Column("geometry", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("quality", sa.String(length=20), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("image_count", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("table_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("quality IN ('ok', 'blank', 'non_text', 'needs_review')", name="ck_pages_quality"),
        sa.CheckConstraint("page_number >= 1", name="ck_pages_number"),
        sa.ForeignKeyConstraint(["extraction_id"], ["extractions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("extraction_id", "page_number"),
    )
    op.create_table(
        "elements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("block_seq", sa.Integer(), nullable=True),
        sa.Column("line_seq", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("quads_pdf", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("bbox_mupdf", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("glyphs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("kind IN ('line', 'table', 'cell')", name="ck_elements_kind"),
        sa.ForeignKeyConstraint(["extraction_id"], ["extractions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["elements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_elements_extraction_id"), "elements", ["extraction_id"], unique=False)
    op.create_index("ix_elements_page_kind_line", "elements", ["page_id", "kind", "line_seq"], unique=False)
    op.create_table(
        "table_cells",
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("table_element_id", sa.Uuid(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("col_index", sa.Integer(), nullable=False),
        sa.Column("row_span", sa.Integer(), nullable=False),
        sa.Column("col_span", sa.Integer(), nullable=False),
        sa.Column("header_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.CheckConstraint("row_index >= 0 AND col_index >= 0", name="ck_table_cells_index"),
        sa.CheckConstraint("row_span >= 1 AND col_span >= 1", name="ck_table_cells_span"),
        sa.ForeignKeyConstraint(["element_id"], ["elements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["table_element_id"], ["elements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("element_id"),
    )
    op.create_index(
        op.f("ix_table_cells_table_element_id"), "table_cells", ["table_element_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_table_cells_table_element_id"), table_name="table_cells")
    op.drop_table("table_cells")
    op.drop_index("ix_elements_page_kind_line", table_name="elements")
    op.drop_index(op.f("ix_elements_extraction_id"), table_name="elements")
    op.drop_table("elements")
    op.drop_table("pages")
    op.drop_index(
        "uq_extractions_active_per_version", table_name="extractions", postgresql_where=sa.text("active")
    )
    op.drop_index(op.f("ix_extractions_version_id"), table_name="extractions")
    op.drop_table("extractions")
    op.drop_index(op.f("ix_document_versions_sha256"), table_name="document_versions")
    op.drop_index("ix_document_versions_period", table_name="document_versions")
    op.drop_index(op.f("ix_document_versions_document_id"), table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index(op.f("ix_documents_issuer_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_jobs_status_lease", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("issuers")
    op.drop_index(op.f("ix_audit_events_target_id"), table_name="audit_events")
    op.drop_table("audit_events")
