"""資料Q&A のバックグラウンドワーカー（python -m app.worker）。

- jobs テーブルから FOR UPDATE SKIP LOCKED で 1 件取得し、running と lease を設定してすぐコミットする。
- PDF 解析は独立した子プロセスで実行し、時間制限を超えたら止める。親は heartbeat で lease を延ばす。
- 抽出結果は 1 トランザクションで書き込み、全ページ成功したときだけ succeeded にする（部分的な結果を残さない）。
- ワーカーが落ちた場合、lease が切れたジョブは別のワーカー（または再起動後）が再取得する。
"""

import logging
import multiprocessing as mp
import os
import signal
import socket
import threading
import time
import uuid
from datetime import timedelta

import pymupdf
from sqlalchemy import create_engine, insert, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.doc_models import DocumentVersion, Element, Extraction, Job, Page, TableCell
from app.models import utcnow
from app.services.pdf import extract
from app.services.pdf.storage import path_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(
        create_engine(get_settings().database_url, pool_pre_ping=True), expire_on_commit=False
    )


class JobError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- 子プロセス: 抽出して DB へ書き込む ---------------------------------------------------


def _fail_extraction(session: Session, extraction: Extraction, code: str, message: str) -> None:
    extraction.status = "failed"
    extraction.error_code = code
    extraction.error_message = message
    extraction.completed_at = utcnow()
    session.commit()


def run_extraction(extraction_id: str) -> None:
    """子プロセスで実行する。成功・失敗とも抽出版の状態に書き込む（例外は親に伝わらない前提にしない）。"""
    settings = get_settings()
    Session_ = _sessionmaker()
    with Session_() as session:
        ext = session.get(Extraction, uuid.UUID(extraction_id))
        if ext is None:
            raise JobError("NOT_FOUND", "抽出版がありません")
        if ext.status == "succeeded":
            return  # 前回コミット済み（親がジョブ完了を記録する前に落ちた場合）
        version = session.get(DocumentVersion, ext.version_id)
        ext.status = "running"
        session.commit()

        path = path_for(settings.pdf_storage_root, version.storage_key)
        try:
            doc = pymupdf.open(path)
        except Exception as e:  # noqa: BLE001 - 壊れた PDF は入力不備として記録する
            _fail_extraction(session, ext, "INVALID_PDF", f"PDF を開けません: {e}")
            return
        with doc:
            if doc.needs_pass or doc.is_encrypted:
                # 暗号化 PDF は初期版では扱わない（パスワードは収集しない）
                _fail_extraction(session, ext, "ENCRYPTED_PDF", "暗号化された PDF には対応していません")
                return
            if doc.page_count == 0:
                _fail_extraction(session, ext, "INVALID_PDF", "ページがありません")
                return
            if doc.page_count > settings.max_pdf_pages:
                _fail_extraction(
                    session,
                    ext,
                    "FILE_LIMIT_EXCEEDED",
                    f"ページ数が上限（{settings.max_pdf_pages}）を超えています（{doc.page_count}）",
                )
                return

            qualities: list[str] = []
            totals = {"pages": doc.page_count, "chars": 0, "lines": 0, "tables": 0, "cells": 0}
            for result in extract.extract_pages(doc):
                _write_page(session, ext.id, result)
                qualities.append(result.quality)
                totals["chars"] += result.char_count
                totals["lines"] += len(result.lines)
                totals["tables"] += len(result.tables)
                totals["cells"] += sum(len(t.cells) for t in result.tables)
            totals["page_quality"] = {q: qualities.count(q) for q in sorted(set(qualities))}
            version.page_count = doc.page_count

        ext.quality = extract.classify_document(qualities)
        ext.stats = totals
        ext.status = "succeeded"
        ext.completed_at = utcnow()
        session.commit()


def _write_page(session: Session, extraction_id: uuid.UUID, r: extract.PageResult) -> None:
    page_id = uuid.uuid4()
    session.add(
        Page(
            id=page_id,
            extraction_id=extraction_id,
            page_number=r.page_number,
            pdf_page_label=r.pdf_page_label,
            geometry=r.geometry,
            quality=r.quality,
            char_count=r.char_count,
            image_count=r.image_count,
            line_count=len(r.lines),
            table_count=len(r.tables),
        )
    )
    session.flush()
    elements = [
        {
            "id": uuid.uuid4(),
            "extraction_id": extraction_id,
            "page_id": page_id,
            "parent_id": None,
            "kind": "line",
            "block_seq": line.block_seq,
            "line_seq": line.line_seq,
            "raw_text": line.text,
            "search_text": extract.search_text(line.text),
            "quads_pdf": [line.quad],
            "bbox_mupdf": [round(v, 2) for v in line.bbox],
            "glyphs": line.glyphs,
        }
        for line in r.lines
    ]
    cells_rows = []
    for table in r.tables:
        table_id = uuid.uuid4()
        elements.append(
            {
                "id": table_id,
                "extraction_id": extraction_id,
                "page_id": page_id,
                "parent_id": None,
                "kind": "table",
                "block_seq": None,
                "line_seq": None,
                "raw_text": "",
                "search_text": "",
                "quads_pdf": [table.quad],
                "bbox_mupdf": [round(v, 2) for v in table.bbox],
                "glyphs": None,
            }
        )
        ids = {(c.row, c.col): uuid.uuid4() for c in table.cells}
        for cell in table.cells:
            elements.append(
                {
                    "id": ids[(cell.row, cell.col)],
                    "extraction_id": extraction_id,
                    "page_id": page_id,
                    "parent_id": table_id,
                    "kind": "cell",
                    "block_seq": None,
                    "line_seq": None,
                    "raw_text": cell.text,
                    "search_text": extract.search_text(cell.text),
                    "quads_pdf": [cell.quad],
                    "bbox_mupdf": [round(v, 2) for v in cell.bbox],
                    "glyphs": None,
                }
            )
            # 見出しの自動推定: 列見出し（上から header_rows 行）と行見出し（1 列目）
            headers = []
            if cell.row >= table.header_rows:
                headers += [str(ids[(r, cell.col)]) for r in range(table.header_rows) if (r, cell.col) in ids]
            if cell.col > 0 and (cell.row, 0) in ids:
                headers.append(str(ids[(cell.row, 0)]))
            cells_rows.append(
                {
                    "element_id": ids[(cell.row, cell.col)],
                    "table_element_id": table_id,
                    "row_index": cell.row,
                    "col_index": cell.col,
                    "row_span": 1,
                    "col_span": 1,
                    "header_ids": headers,
                    "review_status": "unreviewed",
                }
            )
    if elements:
        session.execute(insert(Element), elements)
    if cells_rows:
        session.execute(insert(TableCell), cells_rows)


def _child_main(extraction_id: str) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    run_extraction(extraction_id)


# ---- 親プロセス: ジョブの取得・監視 --------------------------------------------------------


def claim_job(Session_: sessionmaker[Session]) -> Job | None:
    settings = get_settings()
    now = utcnow()
    with Session_() as session, session.begin():
        job = session.scalar(
            select(Job)
            .where(
                or_(
                    Job.status == "queued",
                    # 落ちたワーカーのジョブ（lease 切れ）を再取得する
                    (Job.status == "running") & (Job.lease_until < now),
                )
            )
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_at = now
        job.lease_until = now + timedelta(seconds=settings.job_lease_seconds)
        job.worker_id = WORKER_ID
    return job


def _heartbeat(Session_: sessionmaker[Session], job_id: uuid.UUID, stop: threading.Event) -> None:
    settings = get_settings()
    while not stop.wait(settings.job_heartbeat_seconds):
        with Session_() as session, session.begin():
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.worker_id == WORKER_ID)
                .values(lease_until=utcnow() + timedelta(seconds=settings.job_lease_seconds))
            )


def _finish(
    Session_: sessionmaker[Session], job: Job, status: str, code: str | None = None, msg: str | None = None
):
    with Session_() as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(status=status, error_code=code, error_message=msg, finished_at=utcnow(), lease_until=None)
        )


def _mark_extraction_failed(Session_: sessionmaker[Session], extraction_id: str, code: str, msg: str) -> None:
    with Session_() as session, session.begin():
        session.execute(
            update(Extraction)
            .where(Extraction.id == uuid.UUID(extraction_id), Extraction.status != "succeeded")
            .values(status="failed", error_code=code, error_message=msg, completed_at=utcnow())
        )


def process(Session_: sessionmaker[Session], job: Job) -> None:
    settings = get_settings()
    if job.kind != "extract":
        _finish(Session_, job, "failed", "UNKNOWN_JOB", f"未対応のジョブです: {job.kind}")
        return
    extraction_id = job.payload["extraction_id"]
    if job.attempts > settings.job_max_attempts:
        _mark_extraction_failed(
            Session_, extraction_id, "RETRY_EXHAUSTED", "解析を再試行しましたが完了しませんでした"
        )
        _finish(Session_, job, "failed", "RETRY_EXHAUSTED", "再試行回数の上限に達しました")
        return

    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(Session_, job.id, stop), daemon=True)
    beat.start()
    started = time.monotonic()
    ctx = mp.get_context("spawn")
    child = ctx.Process(target=_child_main, args=(extraction_id,), daemon=True)
    child.start()
    child.join(settings.parser_timeout_seconds)
    stop.set()
    if child.is_alive():
        child.terminate()
        child.join(5)
        if child.is_alive():
            child.kill()
        msg = f"解析が {settings.parser_timeout_seconds} 秒以内に終わりませんでした"
        _mark_extraction_failed(Session_, extraction_id, "PARSE_TIMEOUT", msg)
        _finish(Session_, job, "failed", "PARSE_TIMEOUT", msg)
        log.error("job %s timed out", job.id)
        return
    if child.exitcode != 0:
        msg = f"解析プロセスが異常終了しました（exit code {child.exitcode}）"
        _mark_extraction_failed(Session_, extraction_id, "PARSE_ERROR", msg)
        _finish(Session_, job, "failed", "PARSE_ERROR", msg)
        log.error("job %s failed: exit code %s", job.id, child.exitcode)
        return
    with Session_() as session:
        ext = session.get(Extraction, uuid.UUID(extraction_id))
        ok = ext is not None and ext.status == "succeeded"
        code, message = (
            (None, None)
            if ok
            else (ext.error_code if ext else "NOT_FOUND", ext.error_message if ext else None)
        )
    _finish(Session_, job, "succeeded" if ok else "failed", code, message)
    log.info(
        "job %s %s in %.1fs", job.id, "succeeded" if ok else f"failed ({code})", time.monotonic() - started
    )


def main() -> None:
    settings = get_settings()
    Session_ = _sessionmaker()
    log.info("worker %s started", WORKER_ID)
    running = True

    def _stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while running:
        try:
            job = claim_job(Session_)
        except Exception:
            log.exception("failed to claim job")
            time.sleep(settings.worker_poll_seconds)
            continue
        if job is None:
            time.sleep(settings.worker_poll_seconds)
            continue
        try:
            process(Session_, job)
        except Exception as e:  # noqa: BLE001 - ワーカー自体は止めない
            log.exception("job %s crashed", job.id)
            _finish(Session_, job, "failed", "WORKER_ERROR", str(e)[:500])


if __name__ == "__main__":
    main()
