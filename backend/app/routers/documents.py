"""資料Q&A の API（段階 1〜3: PDF 登録・抽出・レビュー・根拠表示）。すべてログイン必須。"""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
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
    AuditEvent,
    Document,
    DocumentVersion,
    Element,
    Extraction,
    Issuer,
    Job,
    Page,
    TableCell,
)
from app.models import User, utcnow
from app.routers.auth import current_user, require_csrf_header
from app.services.pdf.extract import PARSER_VERSION
from app.services.pdf.storage import StorageError, path_for, save_upload

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
