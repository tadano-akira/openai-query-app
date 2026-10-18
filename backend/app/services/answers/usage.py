"""資料Q&A の回答ごとのトークン使用量・推定料金・所要時間と、その累計（チャットの計測値と同じ考え方）。

1 回の回答の使用量 = 回答の LLM 呼び出し（answer_selection、作り直しを含む）＋ 検索の質問の埋め込み（embedding_query）。
使用量が返らなかった呼び出し（エラーなど）は「不明」として数え、0 とはみなさない。
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import Float, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.doc_models import AnswerRun, LlmCall

SUCCESS_STATUSES = ("completed", "needs_review", "insufficient_evidence", "needs_clarification", "conflict")


@dataclass
class AnswerUsage:
    llm_call_count: int = 0
    unknown_usage_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    # 入力のうち、検索の質問の埋め込みの分
    embedding_tokens: int = 0
    # 回答の LLM 呼び出しの所要時間の合計（作り直しを含む）
    api_ms: int | None = None
    estimated_cost_usd: float = 0.0
    unknown_cost_call_count: int = 0


def _calls_of_runs():
    """回答と、その回答に含まれる呼び出し（回答の LLM・検索の埋め込み）の組"""
    return select(AnswerRun.id.label("run_id"), LlmCall).join(
        LlmCall,
        or_(
            LlmCall.answer_run_id == AnswerRun.id,
            and_(LlmCall.search_run_id.is_not(None), LlmCall.search_run_id == AnswerRun.search_run_id),
        ),
    )


async def run_usage(session: AsyncSession, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, AnswerUsage]:
    out = {rid: AnswerUsage() for rid in run_ids}
    if not run_ids:
        return out
    rows = (await session.execute(_calls_of_runs().where(AnswerRun.id.in_(run_ids)))).all()
    for run_id, call in rows:
        u = out[run_id]
        u.llm_call_count += 1
        if call.input_tokens is None:
            u.unknown_usage_call_count += 1
        else:
            u.input_tokens += call.input_tokens
            u.output_tokens += call.output_tokens or 0
            if call.purpose == "embedding_query":
                u.embedding_tokens += call.input_tokens
        if call.estimated_cost_usd is None:
            if call.input_tokens is not None:
                u.unknown_cost_call_count += 1
        else:
            u.estimated_cost_usd += float(call.estimated_cost_usd)
        if call.purpose == "answer_selection" and call.latency_ms is not None:
            u.api_ms = (u.api_ms or 0) + call.latency_ms
    for u in out.values():
        u.total_tokens = u.input_tokens + u.output_tokens
    return out


@dataclass
class AnswerStats:
    run_count: int
    succeeded_count: int
    failed_count: int
    status_counts: dict[str, int]
    api_call_count: int
    unknown_usage_call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    unknown_cost_call_count: int
    avg_duration_ms: float | None
    duration_sample_count: int
    avg_api_ms: float | None
    api_sample_count: int
    total_duration_ms: int


async def stats(session: AsyncSession) -> AnswerStats:
    """資料Q&A のすべての回答の累計"""
    total_ms = cast(AnswerRun.timings["total_ms"].astext, Float)
    runs = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(AnswerRun.status.in_(SUCCESS_STATUSES)),
                func.count().filter(AnswerRun.status == "failed"),
                func.avg(total_ms),
                func.count(total_ms),
                func.coalesce(func.sum(total_ms), 0),
            )
        )
    ).one()
    status_counts = dict(
        (await session.execute(select(AnswerRun.status, func.count()).group_by(AnswerRun.status))).all()
    )
    calls = _calls_of_runs().subquery()
    c = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(calls.c.input_tokens.is_(None)),
                func.coalesce(func.sum(calls.c.input_tokens), 0),
                func.coalesce(func.sum(calls.c.output_tokens), 0),
                func.coalesce(func.sum(calls.c.estimated_cost_usd), 0),
                func.count().filter(calls.c.estimated_cost_usd.is_(None), calls.c.input_tokens.is_not(None)),
            )
        )
    ).one()
    # 回答ごとの LLM 所要時間（作り直しを含む合計）の平均
    per_run_api = (
        select(func.sum(calls.c.latency_ms).label("ms"))
        .where(calls.c.purpose == "answer_selection", calls.c.latency_ms.is_not(None))
        .group_by(calls.c.run_id)
        .subquery()
    )
    api_avg, api_n = (await session.execute(select(func.avg(per_run_api.c.ms), func.count()))).one()
    return AnswerStats(
        run_count=runs[0],
        succeeded_count=runs[1],
        failed_count=runs[2],
        status_counts={k: int(v) for k, v in status_counts.items()},
        api_call_count=c[0],
        unknown_usage_call_count=c[1],
        input_tokens=int(c[2]),
        output_tokens=int(c[3]),
        total_tokens=int(c[2]) + int(c[3]),
        estimated_cost_usd=float(c[4]),
        unknown_cost_call_count=c[5],
        avg_duration_ms=float(runs[3]) if runs[3] is not None else None,
        duration_sample_count=runs[4],
        avg_api_ms=float(api_avg) if api_avg is not None else None,
        api_sample_count=api_n,
        total_duration_ms=int(runs[5]),
    )
