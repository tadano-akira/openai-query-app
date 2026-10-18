"""資料Q&A の API（PDF 登録・抽出・レビュー・根拠表示・検索・回答）。すべてログイン必須。"""

import uuid
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.doc_models import (
    DOCUMENT_TYPES,
    SCOPES,
    AnswerRun,
    AuditEvent,
    Chunk,
    ChunkElement,
    Document,
    DocumentVersion,
    Element,
    Extraction,
    Issuer,
    Job,
    Page,
    RetrievalHit,
    SearchRun,
    TableCell,
)
from app.models import User, utcnow
from app.routers.auth import current_user, require_csrf_header
from app.services.answers import service as answers
from app.services.answers import usage as answer_usage
from app.services.chat import ChatError
from app.services.pdf.extract import PARSER_VERSION
from app.services.pdf.storage import StorageError, path_for, save_upload
from app.services.search import retrieval
from app.services.tracing import Tracer

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_csrf_header), Depends(current_user)])

DocumentType = Literal[DOCUMENT_TYPES]  # type: ignore[valid-type]
Scope = Literal[SCOPES]  # type: ignore[valid-type]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def _audit(session: AsyncSession, user: User, action: str, target_type: str, target_id: uuid.UUID, **details):
    session.add(
        AuditEvent(
            actor=user.email, action=action, target_type=target_type, target_id=target_id, details=details
        )
    )


# ---- 企業 ------------------------------------------------------------------------------


class IssuerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=20)


class IssuerOut(BaseModel):
    id: uuid.UUID
    name: str
    code: str | None


@router.get("/issuers", response_model=list[IssuerOut])
async def list_issuers(session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(Issuer).order_by(Issuer.code.nulls_last(), Issuer.name))
    return [IssuerOut(id=i.id, name=i.name, code=i.code) for i in rows]


@router.post("/issuers", response_model=IssuerOut, status_code=201)
async def create_issuer(
    body: IssuerIn, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
):
    code = (body.code or "").strip() or None
    if code and await session.scalar(select(Issuer).where(Issuer.code == code)):
        raise _err(409, "DUPLICATE_ISSUER", f"証券コード {code} の企業は登録済みです")
    issuer = Issuer(name=body.name.strip(), code=code)
    session.add(issuer)
    await session.flush()
    _audit(session, user, "issuer.create", "issuer", issuer.id, name=issuer.name, code=code)
    await session.commit()
    return IssuerOut(id=issuer.id, name=issuer.name, code=issuer.code)


# ---- 資料・版 ----------------------------------------------------------------------------


class ExtractionOut(BaseModel):
    id: uuid.UUID
    status: str
    quality: str | None
    stats: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    parser_version: str
    approved_at: datetime | None
    approved_by: str | None
    approval_memo: str | None
    active: bool
    created_at: datetime
    completed_at: datetime | None
    index_status: str
    index_error: str | None
    index_model: str | None
    indexed_at: datetime | None


class VersionOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    sha256: str
    file_size: int
    original_filename: str | None
    page_count: int | None
    source_url: str | None
    fetched_at: datetime
    published_on: date | None
    period_start: date | None
    period_end: date | None
    period_label: str | None
    scope: str
    accounting_standard: str | None
    currency: str | None
    official_status: str
    official_confirmed_by: str | None
    official_confirmed_at: datetime | None
    official_memo: str | None
    supersedes_id: uuid.UUID | None
    correction_reason: str | None
    created_at: datetime
    extractions: list[ExtractionOut]


class DocumentOut(BaseModel):
    id: uuid.UUID
    issuer: IssuerOut
    title: str
    document_type: str
    created_at: datetime
    versions: list[VersionOut]


def _extraction_out(e: Extraction) -> ExtractionOut:
    return ExtractionOut(
        id=e.id,
        status=e.status,
        quality=e.quality,
        stats=e.stats,
        error_code=e.error_code,
        error_message=e.error_message,
        parser_version=e.parser_version,
        approved_at=e.approved_at,
        approved_by=e.approved_by,
        approval_memo=e.approval_memo,
        active=e.active,
        created_at=e.created_at,
        completed_at=e.completed_at,
        index_status=e.index_status,
        index_error=e.index_error,
        index_model=e.index_model,
        indexed_at=e.indexed_at,
    )


def _version_out(v: DocumentVersion) -> VersionOut:
    return VersionOut(
        **{k: getattr(v, k) for k in VersionOut.model_fields if k != "extractions"},
        extractions=[_extraction_out(e) for e in v.extractions],
    )


def _document_out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        issuer=IssuerOut(id=d.issuer.id, name=d.issuer.name, code=d.issuer.code),
        title=d.title,
        document_type=d.document_type,
        created_at=d.created_at,
        versions=[_version_out(v) for v in d.versions],
    )


_DOC_LOAD = (
    selectinload(Document.issuer),
    selectinload(Document.versions).selectinload(DocumentVersion.extractions),
)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(session: AsyncSession = Depends(get_session)):
    docs = await session.scalars(
        select(Document)
        .where(Document.deleted_at.is_(None))
        .options(*_DOC_LOAD)
        .order_by(Document.created_at.desc())
    )
    return [_document_out(d) for d in docs]


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    doc = await session.scalar(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None)).options(*_DOC_LOAD)
    )
    if doc is None:
        raise _err(404, "NOT_FOUND", "資料が見つかりません")
    return _document_out(doc)


class UploadOut(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    extraction_id: uuid.UUID
    job_id: uuid.UUID


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise _err(422, "validation_error", f"日付の形式が不正です: {value}") from e


async def _queue_extraction(session: AsyncSession, version_id: uuid.UUID) -> tuple[Extraction, Job]:
    extraction = Extraction(version_id=version_id, parser_version=PARSER_VERSION, config={}, status="queued")
    session.add(extraction)
    await session.flush()
    job = Job(kind="extract", payload={"extraction_id": str(extraction.id)})
    session.add(job)
    await session.flush()
    return extraction, job


@router.post("/documents", response_model=UploadOut, status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    issuer_id: uuid.UUID = Form(...),
    title: str = Form(..., min_length=1, max_length=300),
    document_type: DocumentType = Form(...),
    period_label: str | None = Form(None, max_length=100),
    period_start: str | None = Form(None),
    period_end: str | None = Form(None),
    published_on: str | None = Form(None),
    scope: Scope = Form("unknown"),
    accounting_standard: str | None = Form(None, max_length=40),
    currency: str | None = Form("JPY", max_length=10),
    source_url: str | None = Form(None, max_length=2000),
    # 既存資料の新しい版として登録する場合（差し替え・訂正）
    document_id: uuid.UUID | None = Form(None),
    supersedes_id: uuid.UUID | None = Form(None),
    correction_reason: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    settings = get_settings()
    issuer = await session.get(Issuer, issuer_id)
    if issuer is None:
        raise _err(404, "NOT_FOUND", "企業が見つかりません")
    if source_url and not source_url.startswith("https://"):
        raise _err(422, "validation_error", "掲載元 URL は https:// で始まる公式の URL を入力してください")
    starts, ends, published = _date(period_start), _date(period_end), _date(published_on)
    try:
        stored = await save_upload(file, settings.pdf_storage_root, settings.max_pdf_bytes)
    except StorageError as e:
        raise _err(e.status_code, e.code, e.message) from e

    # 同じ企業に同じ内容の PDF が既にあれば重複として止める
    dup = await session.scalar(
        select(DocumentVersion)
        .join(Document)
        .where(
            DocumentVersion.sha256 == stored.sha256,
            Document.issuer_id == issuer_id,
            Document.deleted_at.is_(None),
        )
    )
    if dup is not None:
        raise HTTPException(
            409,
            detail={
                "code": "DUPLICATE_DOCUMENT",
                "message": "同じ内容の PDF がこの企業に登録済みです",
                "document_id": str(dup.document_id),
                "version_id": str(dup.id),
            },
        )

    if document_id is not None:
        doc = await session.get(Document, document_id)
        if doc is None or doc.deleted_at is not None or doc.issuer_id != issuer_id:
            raise _err(404, "NOT_FOUND", "差し替え対象の資料が見つかりません")
    else:
        doc = Document(issuer_id=issuer_id, title=title.strip(), document_type=document_type)
        session.add(doc)
        await session.flush()
    version = DocumentVersion(
        document_id=doc.id,
        sha256=stored.sha256,
        storage_key=stored.storage_key,
        file_size=stored.size,
        original_filename=(file.filename or "")[:300] or None,
        source_url=source_url or None,
        published_on=published,
        period_start=starts,
        period_end=ends,
        period_label=(period_label or "").strip() or None,
        scope=scope,
        accounting_standard=accounting_standard or None,
        currency=currency or None,
        supersedes_id=supersedes_id,
        correction_reason=correction_reason or None,
    )
    session.add(version)
    await session.flush()
    extraction, job = await _queue_extraction(session, version.id)
    _audit(
        session, user, "document_version.upload", "document_version", version.id,
        sha256=stored.sha256, size=stored.size, filename=version.original_filename, document_id=str(doc.id),
    )  # fmt: skip
    await session.commit()
    return UploadOut(document_id=doc.id, version_id=version.id, extraction_id=extraction.id, job_id=job.id)


class VersionPatch(BaseModel):
    period_label: str | None = Field(default=None, max_length=100)
    period_start: date | None = None
    period_end: date | None = None
    published_on: date | None = None
    scope: Scope | None = None
    accounting_standard: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, max_length=10)
    source_url: str | None = Field(default=None, max_length=2000)


async def _version(session: AsyncSession, version_id: uuid.UUID) -> DocumentVersion:
    v = await session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.id == version_id)
        .options(selectinload(DocumentVersion.extractions), selectinload(DocumentVersion.document))
    )
    if v is None or v.document.deleted_at is not None:
        raise _err(404, "NOT_FOUND", "資料の版が見つかりません")
    return v


@router.get("/document-versions/{version_id}", response_model=VersionOut)
async def get_version(version_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return _version_out(await _version(session, version_id))


@router.patch("/document-versions/{version_id}", response_model=VersionOut)
async def patch_version(
    version_id: uuid.UUID,
    body: VersionPatch,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """メタデータの修正。変更前後の値を監査記録に残す。公式性確認済みの版は確認を取り消す"""
    v = await _version(session, version_id)
    changes = body.model_dump(exclude_unset=True)
    if "source_url" in changes and changes["source_url"] and not changes["source_url"].startswith("https://"):
        raise _err(422, "validation_error", "掲載元 URL は https:// で始まる公式の URL を入力してください")
    before = {k: getattr(v, k) for k in changes}
    for k, value in changes.items():
        setattr(v, k, value)
    if changes and v.official_status == "confirmed":
        # 対象期などが変わったら、公式性・メタデータの確認をやり直す
        v.official_status = "unconfirmed"
    _audit(
        session, user, "document_version.update", "document_version", v.id,
        before={k: str(val) if val is not None else None for k, val in before.items()},
        after={k: str(val) if val is not None else None for k, val in changes.items()},
    )  # fmt: skip
    await session.commit()
    return _version_out(await _version(session, version_id))


class OfficialIn(BaseModel):
    status: Literal["confirmed", "rejected", "unconfirmed"]
    memo: str | None = Field(default=None, max_length=2000)


@router.post("/document-versions/{version_id}/official", response_model=VersionOut)
async def set_official(
    version_id: uuid.UUID,
    body: OfficialIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """公式性の確認（利用者が掲載元と内容を確認した結果）。未確認・却下の版は検索対象にしない"""
    v = await _version(session, version_id)
    if body.status == "confirmed" and not v.source_url:
        raise _err(422, "SOURCE_URL_REQUIRED", "公式の掲載元 URL を入力してから確認してください")
    v.official_status = body.status
    v.official_confirmed_by = user.email if body.status != "unconfirmed" else None
    v.official_confirmed_at = utcnow() if body.status != "unconfirmed" else None
    v.official_memo = body.memo
    _audit(
        session, user, f"document_version.official.{body.status}", "document_version", v.id, memo=body.memo
    )
    await session.commit()
    return _version_out(await _version(session, version_id))


@router.get("/document-versions/{version_id}/pdf")
async def get_pdf(version_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """認証付きで原本を配信する（Range 要求にも対応）"""
    v = await _version(session, version_id)
    path = path_for(get_settings().pdf_storage_root, v.storage_key)
    if not path.is_file():
        raise _err(404, "NOT_FOUND", "原本ファイルがありません")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/document-versions/{version_id}/extract", response_model=UploadOut, status_code=202)
async def reextract(
    version_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
):
    """再解析。新しい抽出版を作り、既存の抽出版は変更しない"""
    v = await _version(session, version_id)
    extraction, job = await _queue_extraction(session, v.id)
    _audit(session, user, "extraction.queue", "extraction", extraction.id, version_id=str(v.id))
    await session.commit()
    return UploadOut(document_id=v.document_id, version_id=v.id, extraction_id=extraction.id, job_id=job.id)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
):
    """論理削除（検索対象から外す）。原本・抽出結果・監査記録は残す"""
    result = await session.execute(
        update(Document)
        .where(Document.id == document_id, Document.deleted_at.is_(None))
        .values(deleted_at=func.now())
    )
    if result.rowcount == 0:
        raise _err(404, "NOT_FOUND", "資料が見つかりません")
    _audit(session, user, "document.delete", "document", document_id)
    await session.commit()


# ---- ジョブ・抽出レビュー -----------------------------------------------------------------


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if job is None:
        raise _err(404, "NOT_FOUND", "ジョブが見つかりません")
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "attempts": job.attempts,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


async def _extraction(session: AsyncSession, extraction_id: uuid.UUID) -> Extraction:
    e = await session.scalar(
        select(Extraction)
        .where(Extraction.id == extraction_id)
        .options(selectinload(Extraction.version).selectinload(DocumentVersion.document))
    )
    if e is None or e.version.document.deleted_at is not None:
        raise _err(404, "NOT_FOUND", "抽出版が見つかりません")
    return e


class PageSummary(BaseModel):
    page_number: int
    pdf_page_label: str | None
    printed_page_label: str | None
    quality: str
    char_count: int
    image_count: int
    line_count: int
    table_count: int


class ReviewOut(BaseModel):
    extraction: ExtractionOut
    version_id: uuid.UUID
    pages: list[PageSummary]


@router.get("/extractions/{extraction_id}/review", response_model=ReviewOut)
async def review(extraction_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    e = await _extraction(session, extraction_id)
    pages = await session.scalars(select(Page).where(Page.extraction_id == e.id).order_by(Page.page_number))
    return ReviewOut(
        extraction=_extraction_out(e),
        version_id=e.version_id,
        pages=[PageSummary(**{k: getattr(p, k) for k in PageSummary.model_fields}) for p in pages],
    )


class ElementOut(BaseModel):
    id: uuid.UUID
    kind: str
    block_seq: int | None
    line_seq: int | None
    text: str
    quads: list[Any]


class CellOut(ElementOut):
    row: int
    col: int
    header_ids: list[str]


class TableOut(ElementOut):
    cells: list[CellOut]


class PageDetail(BaseModel):
    extraction_id: uuid.UUID
    version_id: uuid.UUID
    page_number: int
    page_count: int
    pdf_page_label: str | None
    printed_page_label: str | None
    quality: str
    geometry: dict[str, Any]
    lines: list[ElementOut]
    tables: list[TableOut]


def _element_out(el: Element) -> ElementOut:
    return ElementOut(
        id=el.id,
        kind=el.kind,
        block_seq=el.block_seq,
        line_seq=el.line_seq,
        text=el.raw_text,
        quads=el.quads_pdf,
    )


@router.get("/extractions/{extraction_id}/pages/{page_number}", response_model=PageDetail)
async def page_detail(
    extraction_id: uuid.UUID, page_number: int, session: AsyncSession = Depends(get_session)
):
    e = await _extraction(session, extraction_id)
    page = await session.scalar(
        select(Page).where(Page.extraction_id == e.id, Page.page_number == page_number)
    )
    if page is None:
        raise _err(404, "NOT_FOUND", "ページが見つかりません")
    elements = (await session.scalars(select(Element).where(Element.page_id == page.id))).all()
    cells = {
        c.element_id: c
        for c in await session.scalars(
            select(TableCell).where(TableCell.element_id.in_([el.id for el in elements if el.kind == "cell"]))
        )
    }
    lines = sorted((el for el in elements if el.kind == "line"), key=lambda el: el.line_seq or 0)
    tables = []
    for t in sorted((el for el in elements if el.kind == "table"), key=lambda el: -el.quads_pdf[0][0][1]):
        tcells = [
            CellOut(
                **_element_out(el).model_dump(),
                row=cells[el.id].row_index,
                col=cells[el.id].col_index,
                header_ids=cells[el.id].header_ids,
            )  # fmt: skip
            for el in elements
            if el.kind == "cell" and el.parent_id == t.id and el.id in cells
        ]
        tables.append(
            TableOut(**_element_out(t).model_dump(), cells=sorted(tcells, key=lambda c: (c.row, c.col)))
        )
    page_count = await session.scalar(
        select(func.count()).select_from(Page).where(Page.extraction_id == e.id)
    )
    return PageDetail(
        extraction_id=e.id,
        version_id=e.version_id,
        page_number=page.page_number,
        page_count=page_count or 0,
        pdf_page_label=page.pdf_page_label,
        printed_page_label=page.printed_page_label,
        quality=page.quality,
        geometry=page.geometry,
        lines=[_element_out(el) for el in lines],
        tables=tables,
    )


class ApproveIn(BaseModel):
    memo: str | None = Field(default=None, max_length=2000)


@router.post("/extractions/{extraction_id}/approve", response_model=ExtractionOut)
async def approve(
    extraction_id: uuid.UUID,
    body: ApproveIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """レビュー結果の承認。承認済みでも、既定の抽出版にするには activate が必要"""
    e = await _extraction(session, extraction_id)
    if e.status != "succeeded":
        raise _err(409, "EXTRACTION_NOT_READY", "解析が完了していない抽出版は承認できません")
    if e.quality == "unsupported_no_text":
        raise _err(
            409, "UNSUPPORTED_NO_TEXT", "文字情報を抽出できない資料は承認できません。OCR は利用しません。"
        )
    if e.approved_at is not None:
        raise _err(409, "ALREADY_APPROVED", "承認済みです")
    e.approved_at = utcnow()
    e.approved_by = user.email
    e.approval_memo = body.memo
    _audit(session, user, "extraction.approve", "extraction", e.id, memo=body.memo, quality=e.quality)
    await session.commit()
    return _extraction_out(e)


@router.post("/extractions/{extraction_id}/activate", response_model=ExtractionOut)
async def activate(
    extraction_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
):
    """承認済みの抽出版を、その原本版の既定の抽出版にする（旧抽出版は残る）"""
    e = await _extraction(session, extraction_id)
    if e.approved_at is None:
        raise _err(409, "NOT_APPROVED", "承認していない抽出版は既定にできません")
    await session.execute(
        update(Extraction)
        .where(Extraction.version_id == e.version_id, Extraction.active)
        .values(active=False)
    )
    await session.flush()
    e.active = True
    _audit(session, user, "extraction.activate", "extraction", e.id, version_id=str(e.version_id))
    # 既定にした抽出版は検索対象になるので、索引がなければ作る
    if not (
        e.index_status in ("ready", "queued", "running") and e.index_model == get_settings().embedding_model
    ):
        await _queue_index(session, e)
    await session.commit()
    return _extraction_out(e)


async def _queue_index(session: AsyncSession, e: Extraction) -> Job:
    e.index_status = "queued"
    e.index_error = None
    e.index_model = get_settings().embedding_model
    job = Job(kind="index", payload={"extraction_id": str(e.id)})
    session.add(job)
    await session.flush()
    return job


@router.post("/extractions/{extraction_id}/index", response_model=ExtractionOut, status_code=202)
async def index_extraction(
    extraction_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
):
    """検索用索引（チャンク・埋め込み）を作り直す。承認済みの抽出版のみ"""
    e = await _extraction(session, extraction_id)
    if e.approved_at is None:
        raise _err(409, "NOT_APPROVED", "承認していない抽出版の索引は作れません")
    if e.index_status in ("queued", "running"):
        raise _err(409, "INDEX_IN_PROGRESS", "索引を作成中です")
    await _queue_index(session, e)
    _audit(session, user, "extraction.index", "extraction", e.id, model=get_settings().embedding_model)
    await session.commit()
    return _extraction_out(e)


# ---- 根拠 ---------------------------------------------------------------------------------


class EvidenceOut(BaseModel):
    element_id: uuid.UUID
    kind: str
    text: str
    page_number: int
    pdf_page_label: str | None
    printed_page_label: str | None
    line_seq: int | None
    quads: list[Any]
    # 表のセルの場合: 見出しセルなど関連する根拠（項目・期の確認用）
    related: list[ElementOut]
    table_id: uuid.UUID | None
    row: int | None
    col: int | None
    extraction_id: uuid.UUID
    extraction_approved: bool
    version_id: uuid.UUID
    sha256: str
    document_id: uuid.UUID
    document_title: str
    issuer_name: str
    period_label: str | None
    viewer_url: str


@router.get("/evidence/{element_id}", response_model=EvidenceOut)
async def evidence(element_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """根拠 ID から、固定された原本版・抽出版・ページ・位置を解決する"""
    el = await session.get(Element, element_id)
    if el is None:
        raise _err(404, "NOT_FOUND", "根拠が見つかりません")
    page = await session.get(Page, el.page_id)
    e = await _extraction(session, el.extraction_id)
    v = e.version
    doc = await session.scalar(
        select(Document).where(Document.id == v.document_id).options(selectinload(Document.issuer))
    )
    related: list[ElementOut] = []
    cell = await session.get(TableCell, el.id) if el.kind == "cell" else None
    if cell and cell.header_ids:
        heads = await session.scalars(
            select(Element).where(Element.id.in_([uuid.UUID(h) for h in cell.header_ids]))
        )
        related = [_element_out(h) for h in heads]
    return EvidenceOut(
        element_id=el.id,
        kind=el.kind,
        text=el.raw_text,
        page_number=page.page_number,
        pdf_page_label=page.pdf_page_label,
        printed_page_label=page.printed_page_label,
        line_seq=el.line_seq,
        quads=el.quads_pdf,
        related=related,
        table_id=el.parent_id if el.kind == "cell" else None,
        row=cell.row_index if cell else None,
        col=cell.col_index if cell else None,
        extraction_id=e.id,
        extraction_approved=e.approved_at is not None,
        version_id=v.id,
        sha256=v.sha256,
        document_id=doc.id,
        document_title=doc.title,
        issuer_name=doc.issuer.name,
        period_label=v.period_label,
        viewer_url=f"/viewer/evidence/{el.id}",
    )


# ---- 段階 4: 検索 -----------------------------------------------------------------------------


class SearchIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    issuer_ids: list[uuid.UUID] = []
    document_types: list[DocumentType] = []
    version_ids: list[uuid.UUID] = []


class HitEvidence(BaseModel):
    element_id: uuid.UUID
    role: str
    text: str


class SearchHitOut(BaseModel):
    rank: int
    chunk_id: uuid.UUID
    kind: str
    heading: str | None
    text: str
    page_number: int
    pdf_page_label: str | None
    extraction_id: uuid.UUID
    version_id: uuid.UUID
    document_title: str
    issuer_name: str
    period_label: str | None
    lexical_rank: int | None
    lexical_score: float | None
    vector_rank: int | None
    vector_score: float | None
    rrf_score: float
    evidence: list[HitEvidence]


class SearchRunOut(BaseModel):
    id: uuid.UUID
    question: str
    status: str
    scope: list[dict[str, Any]]
    expanded_terms: list[str]
    embedding_model: str | None
    timings: dict[str, Any]
    created_at: datetime
    hits: list[SearchHitOut]


async def _search_run_out(session: AsyncSession, run: SearchRun) -> SearchRunOut:
    rows = (
        await session.execute(
            select(RetrievalHit, Chunk, Page.pdf_page_label, DocumentVersion, Document.title, Issuer.name)
            .join(Chunk, Chunk.id == RetrievalHit.chunk_id)
            .join(Page, Page.id == Chunk.page_id)
            .join(Extraction, Extraction.id == Chunk.extraction_id)
            .join(DocumentVersion, DocumentVersion.id == Extraction.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(Issuer, Issuer.id == Document.issuer_id)
            .where(RetrievalHit.run_id == run.id)
            .order_by(RetrievalHit.final_rank)
        )
    ).all()
    chunk_ids = [row[1].id for row in rows]
    parts = (
        await session.execute(
            select(ChunkElement, Element.raw_text)
            .join(Element, Element.id == ChunkElement.element_id)
            .where(ChunkElement.chunk_id.in_(chunk_ids))
            .order_by(ChunkElement.start_offset)
        )
    ).all()
    by_chunk: dict[uuid.UUID, list[HitEvidence]] = {}
    for ce, text_ in parts:
        by_chunk.setdefault(ce.chunk_id, []).append(
            HitEvidence(element_id=ce.element_id, role=ce.role, text=text_)
        )
    hits = [
        SearchHitOut(
            rank=h.final_rank,
            chunk_id=c.id,
            kind=c.kind,
            heading=c.heading,
            text=c.text,
            page_number=c.page_number,
            pdf_page_label=label,
            extraction_id=c.extraction_id,
            version_id=v.id,
            document_title=title,
            issuer_name=issuer,
            period_label=v.period_label,
            lexical_rank=h.lexical_rank,
            lexical_score=h.lexical_score,
            vector_rank=h.vector_rank,
            vector_score=h.vector_score,
            rrf_score=h.rrf_score,
            evidence=by_chunk.get(c.id, []),
        )
        for h, c, label, v, title, issuer in rows
    ]
    return SearchRunOut(
        id=run.id,
        question=run.question,
        status=run.status,
        scope=run.scope_snapshot,
        expanded_terms=run.expanded_terms,
        embedding_model=run.embedding_model,
        timings=run.timings,
        created_at=run.created_at,
        hits=hits,
    )


@router.post("/search", response_model=SearchRunOut)
async def search(body: SearchIn, session: AsyncSession = Depends(get_session)):
    """承認済み・既定・公式性確認済みの抽出版だけを対象に、字句＋ベクトル検索する（回答生成は段階 5）"""
    try:
        run = await retrieval.search(
            session,
            get_settings(),
            body.question,
            retrieval.Filters(body.issuer_ids, list(body.document_types), body.version_ids),
        )
    except ChatError as e:
        raise HTTPException(e.status_code, detail={"code": e.code, "message": e.message, **e.extra}) from e
    return await _search_run_out(session, run)


@router.get("/search-runs/{run_id}", response_model=SearchRunOut)
async def get_search_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    run = await session.get(SearchRun, run_id)
    if run is None:
        raise _err(404, "NOT_FOUND", "検索結果が見つかりません")
    return await _search_run_out(session, run)


class ChunkOut(BaseModel):
    chunk_id: uuid.UUID
    kind: str
    heading: str | None
    text: str
    page_number: int
    extraction_id: uuid.UUID
    version_id: uuid.UUID
    elements: list[ElementOut]
    roles: dict[str, str]


@router.get("/chunks/{chunk_id}", response_model=ChunkOut)
async def get_chunk(chunk_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """チャンクを構成する根拠要素（原本上でまとめて強調するため）"""
    c = await session.get(Chunk, chunk_id)
    if c is None:
        raise _err(404, "NOT_FOUND", "チャンクが見つかりません")
    e = await _extraction(session, c.extraction_id)
    rows = (
        await session.execute(
            select(ChunkElement.role, Element)
            .join(Element, Element.id == ChunkElement.element_id)
            .where(ChunkElement.chunk_id == c.id)
            .order_by(ChunkElement.start_offset)
        )
    ).all()
    seen: dict[uuid.UUID, Element] = {}
    roles: dict[str, str] = {}
    for role, el in rows:
        seen.setdefault(el.id, el)
        roles.setdefault(str(el.id), role)
    return ChunkOut(
        chunk_id=c.id,
        kind=c.kind,
        heading=c.heading,
        text=c.text,
        page_number=c.page_number,
        extraction_id=c.extraction_id,
        version_id=e.version_id,
        elements=[_element_out(el) for el in seen.values()],
        roles=roles,
    )


# ---- 回答（段階 5: 根拠 ID の選択・検証・状態管理） ----------------------------------------------


class AnswerIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    issuer_ids: list[uuid.UUID] = []
    document_types: list[DocumentType] = []
    version_ids: list[uuid.UUID] = []
    model: str | None = None


class AnswerRunSummary(BaseModel):
    id: uuid.UUID
    question: str
    status: str
    answer_kind: str | None
    model: str
    created_at: datetime


class AnswerRunOut(AnswerRunSummary):
    stage: str
    prompt_version: str
    search_run_id: uuid.UUID | None
    result: dict[str, Any] | None
    candidates: list[Any]
    llm_outputs: list[Any]
    validation_errors: list[Any]
    error_code: str | None
    error_message: str | None
    timings: dict[str, Any]
    completed_at: datetime | None
    langfuse_url: str | None
    estimated_cost_usd: float | None
    usage: dict[str, Any]


def _tracer(request: Request) -> Tracer:
    return request.app.state.tracer


async def _answer_run_out(session: AsyncSession, run: AnswerRun, tr: Tracer) -> AnswerRunOut:
    usage = (await answer_usage.run_usage(session, [run.id]))[run.id]
    return AnswerRunOut(
        id=run.id,
        question=run.question,
        status=run.status,
        answer_kind=run.answer_kind,
        model=run.model,
        created_at=run.created_at,
        stage=run.stage,
        prompt_version=run.prompt_version,
        search_run_id=run.search_run_id,
        result=run.result,
        candidates=run.candidates,
        llm_outputs=run.llm_outputs,
        validation_errors=run.validation_errors,
        error_code=run.error_code,
        error_message=run.error_message,
        timings=run.timings,
        completed_at=run.completed_at,
        langfuse_url=tr.trace_url(run.langfuse_trace_id),
        estimated_cost_usd=usage.estimated_cost_usd if usage.llm_call_count else None,
        usage=asdict(usage),
    )


@router.post("/answers", response_model=AnswerRunOut)
async def create_answer(
    body: AnswerIn, session: AsyncSession = Depends(get_session), tr: Tracer = Depends(_tracer)
):
    """検索した根拠の候補から LLM に ID を選ばせ、検証に合格したものだけで回答を組み立てる"""
    try:
        run = await answers.answer(
            session,
            get_settings(),
            tr,
            body.question,
            retrieval.Filters(body.issuer_ids, list(body.document_types), body.version_ids),
            body.model,
        )
    except ChatError as e:
        raise HTTPException(e.status_code, detail={"code": e.code, "message": e.message, **e.extra}) from e
    return await _answer_run_out(session, run, tr)


@router.get("/answer-stats")
async def get_answer_stats(session: AsyncSession = Depends(get_session)):
    """資料Q&A のすべての回答の累計（実行回数・トークン・推定料金・所要時間）"""
    return asdict(await answer_usage.stats(session))


@router.get("/answer-runs", response_model=list[AnswerRunSummary])
async def list_answer_runs(session: AsyncSession = Depends(get_session)):
    runs = await session.scalars(select(AnswerRun).order_by(AnswerRun.created_at.desc()).limit(30))
    return [AnswerRunSummary.model_validate(r, from_attributes=True) for r in runs]


@router.get("/answer-runs/{run_id}", response_model=AnswerRunOut)
async def get_answer_run(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session), tr: Tracer = Depends(_tracer)
):
    run = await session.get(AnswerRun, run_id)
    if run is None:
        raise _err(404, "NOT_FOUND", "回答が見つかりません")
    return await _answer_run_out(session, run, tr)
