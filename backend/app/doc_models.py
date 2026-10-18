"""資料Q&A（PDF 根拠提示型 RAG）のテーブル。設計書「PDF_Evidence_RAG_System_Design.md」の段階 1〜3 分。

- 原本（document_versions）と抽出結果（extractions）を分け、どちらも作成後に上書きしない。
- ページ番号は 1 始まりの物理ページ。座標は PDF user space（y 軸上向き）の四辺形で持つ。
- 検索・回答・数値（chunks / financial_facts 等）は後続の段階で追加する。
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import utcnow

DOCUMENT_TYPES = (
    "tanshin_q1",  # 第1四半期決算短信
    "tanshin_q2",  # 第2四半期（中間期）決算短信
    "tanshin_q3",  # 第3四半期決算短信
    "tanshin_fy",  # 通期決算短信
    "securities_report",  # 有価証券報告書
    "semiannual_report",  # 半期報告書
    "quarterly_report",  # 四半期報告書（2024 年の制度廃止前）
    "other",
)
SCOPES = ("consolidated", "standalone", "mixed", "unknown")
OFFICIAL_STATUSES = ("unconfirmed", "confirmed", "rejected")
# 抽出処理の状態
EXTRACTION_STATUSES = ("queued", "running", "succeeded", "failed")
# 抽出結果の品質（自動判定。正確性の証明ではない）
ANSWER_STATUSES = (
    "running",
    "completed",
    "insufficient_evidence",
    "needs_clarification",
    "needs_review",
    "conflict",
    "failed",
    "cancelled",
)
INDEX_STATUSES = ("none", "queued", "running", "ready", "failed")
CHUNK_KINDS = ("text", "table")
EXTRACTION_QUALITIES = ("ok", "needs_review", "partial", "unsupported_no_text")
PAGE_QUALITIES = ("ok", "blank", "non_text", "needs_review")
ELEMENT_KINDS = ("line", "table", "cell")
JOB_STATUSES = ("queued", "running", "succeeded", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Issuer(Base):
    """企業"""

    __tablename__ = "issuers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    # 証券コード等（任意）
    code: Mapped[str | None] = mapped_column(String(20), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    """論理的な資料系列（例: トヨタの 2026 年 3 月期 有価証券報告書）。差し替えは版を追加する"""

    __tablename__ = "documents"
    __table_args__ = (CheckConstraint(_in("document_type", DOCUMENT_TYPES), name="ck_documents_type"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issuer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("issuers.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    document_type: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # 論理削除（検索対象から外す。原本・抽出結果は残す）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    issuer: Mapped[Issuer] = relationship()
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", order_by="DocumentVersion.created_at"
    )


class DocumentVersion(Base):
    """原本 PDF のバイト列とメタデータの版。原本は SHA-256 で識別し、変更しない"""

    __tablename__ = "document_versions"
    __table_args__ = (
        CheckConstraint(_in("scope", SCOPES), name="ck_document_versions_scope"),
        CheckConstraint(_in("official_status", OFFICIAL_STATUSES), name="ck_document_versions_official"),
        Index("ix_document_versions_period", "document_id", "period_end", "published_on"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    # 保存先（sha256 から作るキー。元のファイル名は使わない）
    storage_key: Mapped[str] = mapped_column(String(200))
    file_size: Mapped[int] = mapped_column(BigInteger)
    original_filename: Mapped[str | None] = mapped_column(String(300))
    page_count: Mapped[int | None] = mapped_column(Integer)
    # 取得方法（初期は upload のみ）と、利用者が入力する公式の掲載元 URL
    acquisition: Mapped[str] = mapped_column(String(20), default="upload")
    source_url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_on: Mapped[date | None] = mapped_column(Date)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    # 表示用の期の名前（例: 2026年3月期 第2四半期）
    period_label: Mapped[str | None] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(20), default="unknown")
    accounting_standard: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str | None] = mapped_column(String(10))
    official_status: Mapped[str] = mapped_column(String(20), default="unconfirmed")
    official_confirmed_by: Mapped[str | None] = mapped_column(String(254))
    official_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    official_memo: Mapped[str | None] = mapped_column(Text)
    # 訂正資料の場合の訂正対象版と理由
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id"))
    correction_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="versions")
    extractions: Mapped[list["Extraction"]] = relationship(
        back_populates="version", order_by="Extraction.created_at"
    )


class Extraction(Base):
    """原本を特定の解析バージョン・設定で解析した結果。再解析は新しい抽出版を作る"""

    __tablename__ = "extractions"
    __table_args__ = (
        CheckConstraint(_in("status", EXTRACTION_STATUSES), name="ck_extractions_status"),
        CheckConstraint(
            f"quality IS NULL OR {_in('quality', EXTRACTION_QUALITIES)}", name="ck_extractions_quality"
        ),
        # 承認済みのものだけを既定の抽出版（active）にできる
        CheckConstraint("NOT active OR approved_at IS NOT NULL", name="ck_extractions_active_approved"),
        Index(
            "uq_extractions_active_per_version",
            "version_id",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)
    parser_version: Mapped[str] = mapped_column(String(100))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    quality: Mapped[str | None] = mapped_column(String(30))
    # ページ数・文字数・表数などの集計
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(254))
    approval_memo: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 検索用の索引（チャンク・埋め込み）。既定にしたときに作る
    index_status: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    index_error: Mapped[str | None] = mapped_column(Text)
    index_model: Mapped[str | None] = mapped_column(String(100))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    version: Mapped[DocumentVersion] = relationship(back_populates="extractions")


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("extraction_id", "page_number"),
        CheckConstraint("page_number >= 1", name="ck_pages_number"),
        CheckConstraint(_in("quality", PAGE_QUALITIES), name="ck_pages_quality"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id", ondelete="CASCADE"))
    # 1 始まりの物理ページ番号
    page_number: Mapped[int] = mapped_column(Integer)
    # 紙面の印刷番号（利用者が確認できない場合は null）と、PDF 内部のページラベル
    printed_page_label: Mapped[str | None] = mapped_column(String(40))
    pdf_page_label: Mapped[str | None] = mapped_column(String(40))
    # rotation、MediaBox/CropBox（PDF user space）、表示範囲、UserUnit
    geometry: Mapped[dict[str, Any]] = mapped_column(JSONB)
    quality: Mapped[str] = mapped_column(String(20))
    char_count: Mapped[int] = mapped_column(Integer)
    image_count: Mapped[int] = mapped_column(Integer)
    line_count: Mapped[int] = mapped_column(Integer)
    table_count: Mapped[int] = mapped_column(Integer)


class Element(Base):
    """根拠になる要素（行・表・セル）。原文・文字座標・位置（四辺形）を持つ"""

    __tablename__ = "elements"
    __table_args__ = (
        CheckConstraint(_in("kind", ELEMENT_KINDS), name="ck_elements_kind"),
        Index("ix_elements_page_kind_line", "page_id", "kind", "line_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"))
    # セルの親は表
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("elements.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))
    # ページ内のシステム付与番号（原本の公式行番号ではない）。行のみ
    block_seq: Mapped[int | None] = mapped_column(Integer)
    line_seq: Mapped[int | None] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)
    # 検索用（NFKC・空白整理）。後続の段階で原文との区間対応を追加する
    search_text: Mapped[str] = mapped_column(Text)
    # 位置: [[[x, y] × 4], ...]（PDF user space の四辺形。左上・右上・左下・右下の順）
    quads_pdf: Mapped[list[Any]] = mapped_column(JSONB)
    # PyMuPDF の回転前座標の矩形 [x0, y0, x1, y1]（CropBox 左上原点・y 下向き）
    bbox_mupdf: Mapped[list[float]] = mapped_column(JSONB)
    # 行のみ: 1 文字ごとの矩形（PDF user space の [x0, y0, x1, y1]）。部分引用のハイライト用
    glyphs: Mapped[list[Any] | None] = mapped_column(JSONB)


class TableCell(Base):
    __tablename__ = "table_cells"
    __table_args__ = (
        CheckConstraint("row_index >= 0 AND col_index >= 0", name="ck_table_cells_index"),
        CheckConstraint("row_span >= 1 AND col_span >= 1", name="ck_table_cells_span"),
    )

    element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("elements.id", ondelete="CASCADE"), primary_key=True
    )
    table_element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("elements.id", ondelete="CASCADE"), index=True
    )
    row_index: Mapped[int] = mapped_column(Integer)
    col_index: Mapped[int] = mapped_column(Integer)
    row_span: Mapped[int] = mapped_column(Integer, default=1)
    col_span: Mapped[int] = mapped_column(Integer, default=1)
    # 見出しセルの element id（列見出し・行見出し）。自動推定で、レビュー対象
    header_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed")


class Job(Base):
    """永続ジョブ。ワーカーが FOR UPDATE SKIP LOCKED で取得し、lease を更新しながら処理する"""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="ck_jobs_status"),
        Index("ix_jobs_status_lease", "status", "lease_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """登録・メタデータ修正・公式性確認・承認・削除の監査記録"""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[uuid.UUID] = mapped_column(index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---- 段階 4: 検索 -------------------------------------------------------------------------


class Chunk(Base):
    """検索用のまとまり（本文の段落群・表の行群）。引用の最小単位ではなく、根拠は chunk_elements で要素に戻す"""

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint(_in("kind", CHUNK_KINDS), name="ck_chunks_kind"),
        UniqueConstraint("extraction_id", "seq"),
        Index(
            "ix_chunks_search_text_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"))
    page_number: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))
    # 直近の見出し（本文）または表題（表）
    heading: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    # NFKC・小文字・空白整理（字句検索用）
    search_text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)


class ChunkElement(Base):
    """チャンクと根拠要素の対応。offset はチャンク本文内の位置（コードポイント、半開区間）"""

    __tablename__ = "chunk_elements"

    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("elements.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    start_offset: Mapped[int] = mapped_column(Integer, primary_key=True)
    end_offset: Mapped[int] = mapped_column(Integer)
    # content（本文・値）/ header（列見出し・行見出し）/ unit（単位注記）/ title（表題）
    role: Mapped[str] = mapped_column(String(10))


class Embedding(Base):
    """チャンクの埋め込み。モデルごとに分け、別モデルのベクトルを混ぜて検索しない"""

    __tablename__ = "embeddings"

    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    model_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    dimensions: Mapped[int] = mapped_column(Integer)
    # 次元はモデルごとに異なるため列では固定しない（少量のうちは exact search。HNSW は必要になってから）
    embedding: Mapped[list[float]] = mapped_column(Vector())


class SearchRun(Base):
    """検索 1 回の記録。対象にした抽出版の集合（スコープ）と順位を固定して残す"""

    __tablename__ = "search_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # 対象の抽出版（id・資料・版）。質問受付時点の集合
    scope_snapshot: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    # 同義語辞書で追加した検索語
    expanded_terms: Mapped[list[str]] = mapped_column(JSONB, default=list)
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(60))
    timings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RetrievalHit(Base):
    __tablename__ = "retrieval_hits"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("search_runs.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id"), primary_key=True)
    lexical_rank: Mapped[int | None] = mapped_column(Integer)
    lexical_score: Mapped[float | None] = mapped_column(Float)
    vector_rank: Mapped[int | None] = mapped_column(Integer)
    vector_score: Mapped[float | None] = mapped_column(Float)
    rrf_score: Mapped[float] = mapped_column(Float)
    final_rank: Mapped[int] = mapped_column(Integer)


class LlmCall(Base):
    """資料Q&A の外部モデル呼び出し（埋め込み等）の観測と推定料金。利用上限の集計に含める"""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    purpose: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6, asdecimal=False))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(60))
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extractions.id", ondelete="SET NULL"))
    search_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("search_runs.id", ondelete="SET NULL"))
    answer_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("answer_runs.id", ondelete="SET NULL"))
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ---- 段階 5: 根拠の選択・検証・回答 ------------------------------------------------------------


class AnswerRun(Base):
    """質問 1 回の回答。検索結果・LLM に渡した候補（run 内 ID の対応表）・検証結果・回答を固定して残す"""

    __tablename__ = "answer_runs"
    __table_args__ = (CheckConstraint(_in("status", ANSWER_STATUSES), name="ck_answer_runs_status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(Text)
    search_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("search_runs.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30))
    # direct（資料の直接記載）/ calculation_pending（計算が必要。段階 6 で対応）/ none
    answer_kind: Mapped[str | None] = mapped_column(String(30))
    # 現在の処理段階（scope_resolution → retrieval → evidence_selection → validation → rendering）
    stage: Mapped[str] = mapped_column(String(30))
    # run 内 ID（E1…）→ 要素 ID と、LLM に渡した候補の内容
    candidates: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    # 検証済みの LLM 出力（試行ごと）と検証エラー
    llm_outputs: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    validation_errors: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    # サーバーが原文から組み立てた回答（画面表示用）
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    timings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64))
    # 質問に適用した指標ポリシー（段階 6）。ポリシーのファイルが変わっても回答時点の定義を再現できる
    metric_policy_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    claims: Mapped[list["Claim"]] = relationship(order_by="Claim.seq", cascade="all, delete-orphan")


class Claim(Base):
    """検証可能な 1 つの主張（1 つの値または 1 つの記載）"""

    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    # value（数値の記載）/ text（文章の記載）
    kind: Mapped[str] = mapped_column(String(10))
    # LLM の短い説明（値そのものはここに書かせない。数字は根拠の見出しにあるものだけ許す）
    note: Mapped[str] = mapped_column(Text)
    # quoted（承認済み抽出版の原文をそのまま引用。数値の突合は段階 6 の financial_facts で行う）
    verification_status: Mapped[str] = mapped_column(String(20))

    evidence: Mapped[list["ClaimEvidence"]] = relationship(cascade="all, delete-orphan")


class ClaimEvidence(Base):
    """主張と原文の対応。role は value（値・本文）/ context（行・列見出し、単位、表題）"""

    __tablename__ = "claim_evidence"

    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True)
    element_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elements.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    # 引用範囲（原文のコードポイント、半開区間）。初期版は要素全体
    quote_start: Mapped[int] = mapped_column(Integer)
    quote_end: Mapped[int] = mapped_column(Integer)


# ---- 段階 6: 数値の根拠（financial_facts）と計算 ------------------------------------------------

FACT_STATUSES = ("verified", "unverified")


class FinancialFact(Base):
    """表の値のセル 1 つを数値として解釈したもの（設計書 §8.2・§8.3）。

    基準値 normalized_value = 原文の数値部分（Decimal）× unit_multiplier。空欄・「—」は null（0 にしない）。
    計算の入力にできるのは verified（承認済み抽出版・数値として解釈でき・単位と期が根拠から特定できた）だけ。
    """

    __tablename__ = "financial_facts"
    __table_args__ = (CheckConstraint(_in("review_status", FACT_STATUSES), name="ck_financial_facts_status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id"), index=True)
    value_element_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elements.id"), unique=True)
    label_element_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    period_element_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    unit_element_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    raw_value: Mapped[str] = mapped_column(Text)
    # 原文の単位での数値（例: 17,868,651 → 17868651）と、基準単位（円・人など）での値
    display_value: Mapped[Decimal | None] = mapped_column(Numeric(38, 6))
    normalized_value: Mapped[Decimal | None] = mapped_column(Numeric(38, 6))
    unit_label: Mapped[str | None] = mapped_column(String(40))
    unit_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(38, 6))
    # money / count / percent
    quantity: Mapped[str | None] = mapped_column(String(20))
    currency: Mapped[str | None] = mapped_column(String(10))
    label_path: Mapped[str | None] = mapped_column(Text)
    period_label: Mapped[str | None] = mapped_column(Text)
    period_end: Mapped[date | None] = mapped_column(Date)
    scope: Mapped[str | None] = mapped_column(String(20))
    metric_key: Mapped[str | None] = mapped_column(String(60))
    review_status: Mapped[str] = mapped_column(String(20))
    # unverified の理由
    issues: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    parser_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Calculation(Base):
    """回答の計算（設計書 §10.5）。式は構造化した AST で保存し、eval は使わない"""

    __tablename__ = "calculations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_runs.id", ondelete="CASCADE"), index=True)
    # 例: {"op": "sum", "args": [{"fact": "<uuid>"}, {"fact": "<uuid>"}]}
    expression: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_fact_ids: Mapped[list[Any]] = mapped_column(JSONB)
    # 基準単位での結果（例: 円）と、表示単位での結果
    result: Mapped[Decimal] = mapped_column(Numeric(38, 6))
    result_display: Mapped[str] = mapped_column(Text)
    output_unit: Mapped[str | None] = mapped_column(String(40))
    output_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(38, 6))
    currency: Mapped[str | None] = mapped_column(String(10))
    rounding: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # 適用した指標ポリシー（なければ質問に基づく計算）
    policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
