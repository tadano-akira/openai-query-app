"""抽出版の検索用索引（チャンク・埋め込み）を作る。ワーカーから同期的に呼ぶ。

チャンクは抽出版から決定的に作れるので、作り直すときは削除してから作る。
埋め込みはモデルごとに保存し、別モデルのベクトルと混ぜない。
全チャンクの埋め込みが揃ってからコミットし、部分的な索引を検索対象にしない。
"""

import logging
import time
import uuid
from collections.abc import Callable

from openai import OpenAI
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.doc_models import Chunk, ChunkElement, Element, Embedding, Extraction, LlmCall, Page, TableCell
from app.models import utcnow
from app.services.pricing import embedding_cost_usd
from app.services.search.chunking import CellIn, LineIn, PageIn, TableIn, build_chunks, normalize
from app.services.usage import period_starts, period_stmts

log = logging.getLogger(__name__)

EMBED_BATCH = 64


class IndexError_(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_pages(session: Session, extraction_id: uuid.UUID) -> list[PageIn]:
    pages = session.scalars(select(Page).where(Page.extraction_id == extraction_id)).all()
    elements = session.scalars(select(Element).where(Element.extraction_id == extraction_id)).all()
    cells = {
        c.element_id: c
        for c in session.scalars(
            select(TableCell).where(TableCell.element_id.in_([e.id for e in elements if e.kind == "cell"]))
        )
    }
    by_page: dict[uuid.UUID, list[Element]] = {}
    for e in elements:
        by_page.setdefault(e.page_id, []).append(e)
    out = []
    for p in pages:
        els = by_page.get(p.id, [])
        lines = tuple(
            LineIn(e.id, e.line_seq or 0, e.raw_text, tuple(e.bbox_mupdf)) for e in els if e.kind == "line"
        )
        tables = []
        for t in (e for e in els if e.kind == "table"):
            tcells = tuple(
                CellIn(
                    e.id,
                    cells[e.id].row_index,
                    cells[e.id].col_index,
                    e.raw_text,
                    tuple(uuid.UUID(h) for h in cells[e.id].header_ids),
                )
                for e in els
                if e.kind == "cell" and e.parent_id == t.id and e.id in cells
            )
            tables.append(TableIn(t.id, tuple(t.bbox_mupdf), tcells))
        out.append(PageIn(p.id, p.page_number, lines, tuple(tables)))
    return out


def _month_cost_exceeded(session: Session, settings: Settings) -> str | None:
    if settings.monthly_cost_limit_usd <= 0:
        return None
    _, month = period_starts(settings.usage_timezone)
    runs_stmt, calls_stmt = period_stmts(month)
    cost = float(session.execute(runs_stmt).one()[1]) + float(session.execute(calls_stmt).one()[0])
    if cost >= settings.monthly_cost_limit_usd:
        return f"今月の推定利用額が上限（${settings.monthly_cost_limit_usd:.2f}）に達しているため、索引を作成しません"
    return None


def embed_texts(
    client: OpenAI, record: Callable[[LlmCall], None], model: str, texts: list[str], purpose: str, **link
) -> list[list[float]]:
    """埋め込みを取得し、呼び出しごとに使用量と推定料金を記録する。
    record は索引作成とは別のトランザクションで即座に保存する（失敗して巻き戻っても料金の記録を残すため）"""
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        started = time.perf_counter()
        try:
            res = client.embeddings.create(model=model, input=batch)
        except Exception as e:
            record(
                LlmCall(
                    purpose=purpose,
                    model=model,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    error_code=type(e).__name__[:60],
                    **link,
                )
            )
            raise IndexError_("EMBEDDING_FAILED", f"埋め込みの取得に失敗しました: {e}") from e
        tokens = res.usage.prompt_tokens if res.usage else None
        record(
            LlmCall(
                purpose=purpose,
                model=model,
                input_tokens=tokens,
                estimated_cost_usd=embedding_cost_usd(model, tokens),
                latency_ms=round((time.perf_counter() - started) * 1000),
                **link,
            )
        )
        vectors += [d.embedding for d in sorted(res.data, key=lambda d: d.index)]
    return vectors


def build_index(
    session: Session, settings: Settings, extraction_id: uuid.UUID, record: Callable[[LlmCall], None]
) -> dict:
    """チャンクを作り直し、埋め込みを付けて保存する。成功したら index_status=ready"""
    ext = session.get(Extraction, extraction_id)
    if ext is None:
        raise IndexError_("NOT_FOUND", "抽出版がありません")
    if ext.status != "succeeded":
        raise IndexError_("EXTRACTION_NOT_READY", "解析が完了していない抽出版には索引を作れません")
    if reason := _month_cost_exceeded(session, settings):
        raise IndexError_("USAGE_LIMIT_EXCEEDED", reason)
    if not settings.openai_api_key:
        raise IndexError_("CONFIG_ERROR", "OPENAI_API_KEY が設定されていません")

    model = settings.embedding_model
    chunks = build_chunks(load_pages(session, extraction_id))
    session.execute(delete(Chunk).where(Chunk.extraction_id == extraction_id))
    rows, parts = [], []
    for seq, c in enumerate(chunks, start=1):
        cid = uuid.uuid4()
        rows.append(
            {
                "id": cid,
                "extraction_id": extraction_id,
                "page_id": c.page_id,
                "page_number": c.page_number,
                "seq": seq,
                "kind": c.kind,
                "heading": c.heading,
                "text": c.text,
                # 見出しも字句検索の対象にする
                "search_text": normalize(f"{c.heading or ''} {c.text}"),
                "token_count": c.tokens,
            }
        )
        seen = set()
        for p in c.parts:
            key = (p.element_id, p.start)
            if key not in seen:
                seen.add(key)
                parts.append(
                    {
                        "chunk_id": cid,
                        "element_id": p.element_id,
                        "start_offset": p.start,
                        "end_offset": p.end,
                        "role": p.role,
                    }
                )
    if rows:
        session.execute(insert(Chunk), rows)
        session.execute(insert(ChunkElement), parts)
    # 埋め込みの入力には見出しを前置する（本文だけでは文脈が分からない行があるため）
    texts = [f"{r['heading']}\n{r['text']}" if r["heading"] else r["text"] for r in rows]
    client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds)
    vectors = embed_texts(client, record, model, texts, "embedding_index", extraction_id=extraction_id)
    if vectors:
        session.execute(
            insert(Embedding),
            [
                {"chunk_id": r["id"], "model_key": model, "dimensions": len(v), "embedding": v}
                for r, v in zip(rows, vectors, strict=True)
            ],
        )
    ext.index_status = "ready"
    ext.index_error = None
    ext.index_model = model
    ext.indexed_at = utcnow()
    stats = {
        "chunks": len(rows),
        "text_chunks": sum(r["kind"] == "text" for r in rows),
        "table_chunks": sum(r["kind"] == "table" for r in rows),
        "tokens_estimated": sum(r["token_count"] for r in rows),
    }
    log.info("indexed extraction %s: %s", extraction_id, stats)
    return stats
