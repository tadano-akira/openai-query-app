import uuid

from app.models import ApiCall, Run, RunStatus
from app.schemas import ApiCallOut, ConversationStats, ErrorInfo, RunDetailOut, RunOut, Timings, Usage
from app.services.prompts import DEFAULT_MODE


def run_to_out(
    run: Run, *, message_id: uuid.UUID | None, persisted: bool = True, langfuse_url: str | None = None
) -> RunOut:
    return RunOut(
        conversation_id=run.conversation_id,
        turn_id=run.turn_id,
        request_id=run.id,
        message_id=message_id,
        retry_of_run_id=run.retry_of_run_id,
        status=run.status,
        outcome=run.outcome,
        persisted=persisted,
        prompt=run.prompt,
        answer=run.answer,
        citations=run.citations or [],
        web_search_enabled=run.web_search_enabled,
        search_url=run.search_url,
        searched_domain=run.searched_domain,
        search_info=run.search_info,
        model=run.model,
        mode=run.mode or DEFAULT_MODE,
        prompt_version=run.prompt_version,
        context_message_count=len(run.context_message_ids or []),
        created_at=run.created_at,
        generated_at=run.completed_at if run.status == RunStatus.SUCCEEDED else None,
        usage=Usage(
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            total_tokens=run.total_tokens,
            cached_input_tokens=run.cached_input_tokens,
            reasoning_tokens=run.reasoning_tokens,
        ),
        estimated_cost_usd=run.estimated_cost_usd,
        timings=Timings(
            backend_duration_ms=run.backend_duration_ms,
            openai_duration_ms=run.openai_duration_ms,
            db_duration_ms=run.db_duration_ms,
            client_duration_ms=run.client_duration_ms,
        ),
        error=ErrorInfo(code=run.error_code, message=run.error_message) if run.error_code else None,
        openai_response_id=run.openai_response_id,
        langfuse_trace_id=run.langfuse_trace_id,
        langfuse_status=run.langfuse_status,
        langfuse_url=langfuse_url,
    )


def run_to_detail(run: Run, *, message_id: uuid.UUID | None, langfuse_url: str | None) -> RunDetailOut:
    base = run_to_out(run, message_id=message_id, langfuse_url=langfuse_url)
    return RunDetailOut(
        **base.model_dump(),
        request_snapshot=run.request_snapshot,
        context_message_ids=run.context_message_ids or [],
        api_calls=[ApiCallOut.model_validate(c) for c in run.api_calls],
    )


def _avg(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def conversation_stats(runs: list[Run], calls: list[ApiCall]) -> ConversationStats:
    succeeded = [r for r in runs if r.status == RunStatus.SUCCEEDED]
    client = [r.client_duration_ms for r in succeeded if r.client_duration_ms is not None]
    openai_ms = [r.openai_duration_ms for r in succeeded if r.openai_duration_ms is not None]
    starts = [r.created_at for r in runs]
    ends = [r.completed_at for r in runs if r.completed_at]
    return ConversationStats(
        run_count=len(runs),
        succeeded_count=len(succeeded),
        failed_count=sum(r.status in (RunStatus.FAILED, RunStatus.INTERRUPTED) for r in runs),
        api_call_count=len(calls),
        unknown_usage_call_count=sum(c.total_tokens is None for c in calls),
        input_tokens=sum(c.input_tokens or 0 for c in calls),
        output_tokens=sum(c.output_tokens or 0 for c in calls),
        total_tokens=sum(c.total_tokens or 0 for c in calls),
        estimated_cost_usd=sum(r.estimated_cost_usd or 0 for r in runs),
        unknown_cost_run_count=sum(
            r.estimated_cost_usd is None and r.status != RunStatus.RUNNING for r in runs
        ),
        avg_client_duration_ms=_avg(client),
        client_duration_sample_count=len(client),
        avg_openai_duration_ms=_avg(openai_ms),
        openai_duration_sample_count=len(openai_ms),
        total_backend_duration_ms=sum(r.backend_duration_ms or 0 for r in runs),
        elapsed_ms=round((max(ends) - min(starts)).total_seconds() * 1000) if starts and ends else None,
    )
