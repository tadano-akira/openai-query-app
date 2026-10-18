"""資料Q&A の検索（設計書 §9.2）。字句検索とベクトル検索の結果を Reciprocal Rank Fusion で統合する。

検索対象（スコープ）は SQL で絞り、LLM には任せない:
既定（active）かつ承認済み・索引作成済みの抽出版で、原本版が公式性確認済み、資料が削除されていないもの。
質問ごとに対象の抽出版と順位を search_runs / retrieval_hits に記録する。
"""

import json
import math
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from openai import AsyncOpenAI
from sqlalchemy import Float, and_, bindparam, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import SessionLocal
from app.doc_models import (
    Chunk,
    Document,
    DocumentVersion,
    Embedding,
    Extraction,
    Issuer,
    LlmCall,
    RetrievalHit,
    SearchRun,
)
from app.services.chat import ChatError, guard_secrets
from app.services.pricing import embedding_cost_usd
from app.services.search.chunking import normalize
from app.services.usage import exceeded_reason, usage_summary

SYNONYMS_PATH = Path(__file__).resolve().parents[3] / "search" / "synonyms.json"
QUESTION_MAX = 1000
# 字句スコアがこれ未満の候補は字句検索の順位に入れない
LEXICAL_MIN_SCORE = 0.15
# 質問から取り出す語（漢字・カタカナ・英数字の 2 文字以上の連なり）
_TERM = re.compile(r"[一-龥々〆ヵヶ]{2,}|[ァ-ヴー・]{2,}|[A-Za-z0-9][A-Za-z0-9.\-]+")


def load_synonyms() -> dict[str, list[str]]:
    data = json.loads(SYNONYMS_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, list)}


def query_terms(question: str, synonyms: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """字句検索に使う語（質問から取り出した語）と、同義語辞書で追加した語を返す"""
    q = unicodedata.normalize("NFKC", question)
    base = list(dict.fromkeys(m.group().lower() for m in _TERM.finditer(q)))
    expanded: list[str] = []
    for key, values in synonyms.items():
        if key in q:
            expanded += [unicodedata.normalize("NFKC", v).lower() for v in values]
    expanded = [t for t in dict.fromkeys(expanded) if t not in base]
    return base, expanded


def rrf(rankings: list[list[uuid.UUID]], k: int) -> dict[uuid.UUID, float]:
    """Reciprocal Rank Fusion。rank は 1 始まり"""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return scores


@dataclass
class Filters:
    issuer_ids: list[uuid.UUID] = field(default_factory=list)
    document_types: list[str] = field(default_factory=list)
    version_ids: list[uuid.UUID] = field(default_factory=list)


async def scope(session: AsyncSession, settings: Settings, f: Filters) -> list[dict]:
    """検索対象の抽出版（質問受付時点のスナップショット）"""
    stmt = (
        select(
            Extraction.id,
            DocumentVersion.id,
            Document.id,
            Document.title,
            Issuer.name,
            DocumentVersion.period_label,
        )
        .join(DocumentVersion, DocumentVersion.id == Extraction.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(Issuer, Issuer.id == Document.issuer_id)
        .where(
            Extraction.active,
            Extraction.approved_at.is_not(None),
            Extraction.index_status == "ready",
            Extraction.index_model == settings.embedding_model,
            DocumentVersion.official_status == "confirmed",
            Document.deleted_at.is_(None),
        )
    )
    if f.issuer_ids:
        stmt = stmt.where(Document.issuer_id.in_(f.issuer_ids))
    if f.document_types:
        stmt = stmt.where(Document.document_type.in_(f.document_types))
    if f.version_ids:
        stmt = stmt.where(DocumentVersion.id.in_(f.version_ids))
    rows = (await session.execute(stmt)).all()
    return [
        {
            "extraction_id": str(e),
            "version_id": str(v),
            "document_id": str(d),
            "title": t,
            "issuer": i,
            "period_label": p,
        }
        for e, v, d, t, i, p in rows
    ]


async def lexical(
    session: AsyncSession,
    extraction_ids: list[uuid.UUID],
    question: str,
    terms: list[str],
    expanded: list[str],
    top_k: int,
) -> list[tuple[uuid.UUID, float]]:
    """pg_trgm の類似度（質問とチャンク内の最も似た部分）＋ 語の出現。

    語の重みは IDF（多くのチャンクに出る語ほど小さい）× 種類（質問の語 1.0、同義語 0.5）。
    会社名のように対象資料のどこにでも出る語で順位が決まらないようにする。
    """
    q = normalize(question)
    words = [(normalize(t), 1.0) for t in terms] + [(normalize(t), 0.5) for t in expanded]
    words = [(w, k) for w, k in words if w]
    patterns = ["%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for w, _ in words]
    in_scope = Chunk.extraction_id.in_(extraction_ids)
    # 対象チャンク数と、各語を含むチャンク数（df）
    counts = (
        await session.execute(
            select(
                func.count(),
                *[
                    func.count().filter(Chunk.search_text.like(bindparam(f"d{i}", pat)))
                    for i, pat in enumerate(patterns)
                ],
            ).where(in_scope)
        )
    ).one()
    n = int(counts[0]) or 1
    idf = [math.log((n + 1) / (int(df) + 1)) / math.log(n + 1) for df in counts[1:]]
    total = sum(kind * w for (_, kind), w in zip(words, idf, strict=True)) or 1.0
    score = func.coalesce(func.word_similarity(bindparam("q", q), Chunk.search_text), literal(0.0, Float))
    for i, ((_, kind), pat, w) in enumerate(zip(words, patterns, idf, strict=True)):
        if w <= 0:
            continue
        score = score + case((Chunk.search_text.like(bindparam(f"t{i}", pat)), kind * w / total), else_=0.0)
    labeled = score.label("score")
    rows = (
        await session.execute(
            select(Chunk.id, labeled).where(in_scope).order_by(labeled.desc(), Chunk.seq).limit(top_k)
        )
    ).all()
    return [(cid, float(s)) for cid, s in rows if s >= LEXICAL_MIN_SCORE]


async def vector(
    session: AsyncSession, extraction_ids: list[uuid.UUID], model: str, query_vec: list[float], top_k: int
) -> list[tuple[uuid.UUID, float]]:
    """コサイン距離による完全探索（少量のうちは近似索引を使わない）"""
    dist = Embedding.embedding.cosine_distance(query_vec)
    rows = (
        await session.execute(
            select(Chunk.id, (1 - dist).label("similarity"))
            .join(Embedding, and_(Embedding.chunk_id == Chunk.id, Embedding.model_key == model))
            .where(Chunk.extraction_id.in_(extraction_ids))
            .order_by(dist)
            .limit(top_k)
        )
    ).all()
    return [(cid, float(s)) for cid, s in rows]


async def _embed_query(settings: Settings, question: str, run_id: uuid.UUID) -> list[float]:
    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds)
    started = time.perf_counter()
    call = LlmCall(purpose="embedding_query", model=settings.embedding_model, search_run_id=run_id)
    try:
        res = await client.embeddings.create(model=settings.embedding_model, input=[question])
        call.input_tokens = res.usage.prompt_tokens if res.usage else None
        call.estimated_cost_usd = embedding_cost_usd(settings.embedding_model, call.input_tokens)
        return res.data[0].embedding
    except Exception as e:
        call.error_code = type(e).__name__[:60]
        raise ChatError(503, "upstream_error", f"質問の埋め込みを取得できませんでした: {e}") from e
    finally:
        call.latency_ms = round((time.perf_counter() - started) * 1000)
        # 検索の成否と関係なく、料金の記録は別のトランザクションで残す
        async with SessionLocal() as s:
            s.add(call)
            await s.commit()


async def search(session: AsyncSession, settings: Settings, question: str, f: Filters) -> SearchRun:
    question = question.strip()
    if not question:
        raise ChatError(422, "validation_error", "質問を入力してください")
    if len(question) > QUESTION_MAX:
        raise ChatError(422, "prompt_too_long", f"質問は {QUESTION_MAX} 文字以内で入力してください")
    t0 = time.perf_counter()
    targets = await scope(session, settings, f)
    run = SearchRun(
        question=question,
        filters={
            "issuer_ids": [str(x) for x in f.issuer_ids],
            "document_types": f.document_types,
            "version_ids": [str(x) for x in f.version_ids],
        },
        scope_snapshot=targets,
        embedding_model=settings.embedding_model,
        status="running",
    )
    if not targets:
        run.status = "no_scope"
        session.add(run)
        await session.commit()
        return run

    # 質問は埋め込みのため OpenAI へ送るので、チャットと同じく機密情報の検知と利用上限を通す
    await guard_secrets(settings, question, None, "search")
    if reason := exceeded_reason(await usage_summary(session, settings)):
        raise ChatError(429, "usage_limit_exceeded", reason)
    if not settings.openai_api_key:
        raise ChatError(503, "config_error", "OPENAI_API_KEY が設定されていません")

    # 埋め込みの呼び出し記録（別トランザクション）から参照できるよう、先に検索の記録を確定する
    session.add(run)
    await session.commit()
    ids = [uuid.UUID(t["extraction_id"]) for t in targets]
    terms, expanded = query_terms(question, load_synonyms())
    run.expanded_terms = expanded

    t1 = time.perf_counter()
    lex = await lexical(session, ids, question, terms, expanded, settings.lexical_top_k)
    t2 = time.perf_counter()
    qvec = await _embed_query(settings, question, run.id)
    t3 = time.perf_counter()
    vec = await vector(session, ids, settings.embedding_model, qvec, settings.vector_top_k)
    t4 = time.perf_counter()

    scores = rrf([[c for c, _ in lex], [c for c, _ in vec]], settings.rrf_k)
    lex_rank = {c: (i, s) for i, (c, s) in enumerate(lex, start=1)}
    vec_rank = {c: (i, s) for i, (c, s) in enumerate(vec, start=1)}
    top = sorted(scores.items(), key=lambda x: -x[1])[: settings.context_top_k]
    for rank, (cid, score) in enumerate(top, start=1):
        session.add(
            RetrievalHit(
                run_id=run.id,
                chunk_id=cid,
                lexical_rank=lex_rank.get(cid, (None, None))[0],
                lexical_score=lex_rank.get(cid, (None, None))[1],
                vector_rank=vec_rank.get(cid, (None, None))[0],
                vector_score=vec_rank.get(cid, (None, None))[1],
                rrf_score=score,
                final_rank=rank,
            )
        )
    run.status = "succeeded"
    run.timings = {
        "total_ms": round((time.perf_counter() - t0) * 1000),
        "lexical_ms": round((t2 - t1) * 1000),
        "embedding_ms": round((t3 - t2) * 1000),
        "vector_ms": round((t4 - t3) * 1000),
        "lexical_candidates": len(lex),
        "vector_candidates": len(vec),
        "terms": terms,
    }
    await session.commit()
    return run
