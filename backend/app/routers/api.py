import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import ApiCall, Conversation, Message, Run, Turn
from app.schemas import (
    ClientMetrics,
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationSummary,
    MessageCreate,
    MessageOut,
    RunDetailOut,
    RunOut,
    TurnOut,
)
from app.services.chat import DEFAULT_TITLE, ChatError, ChatService
from app.services.serializers import conversation_stats, run_to_detail, run_to_out
from app.services.tracing import Tracer

router = APIRouter()


def chat_service(request: Request) -> ChatService:
    return request.app.state.chat


def tracer(request: Request) -> Tracer:
    return request.app.state.tracer


@router.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(503, detail={"code": "db_error", "message": "DB に接続できません"}) from e
    return {"status": "ok"}


@router.post("/api/v1/conversations", response_model=ConversationSummary, status_code=201)
async def create_conversation(body: ConversationCreate, session: AsyncSession = Depends(get_session)):
    conv = Conversation(title=(body.title or "").strip()[:200] or DEFAULT_TITLE)
    session.add(conv)
    await session.commit()
    return ConversationSummary(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=0,
        total_tokens=0,
        unknown_usage_call_count=0,
    )


@router.get("/api/v1/conversations", response_model=ConversationList)
async def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    msg_count = (
        select(Message.conversation_id, func.count().label("n")).group_by(Message.conversation_id).subquery()
    )
    usage = (
        select(
            Run.conversation_id,
            func.coalesce(func.sum(ApiCall.total_tokens), 0).label("tokens"),
            func.count().filter(ApiCall.total_tokens.is_(None)).label("unknown"),
        )
        .join(ApiCall, ApiCall.run_id == Run.id)
        .group_by(Run.conversation_id)
        .subquery()
    )
    rows = await session.execute(
        select(Conversation, msg_count.c.n, usage.c.tokens, usage.c.unknown)
        .outerjoin(msg_count, msg_count.c.conversation_id == Conversation.id)
        .outerjoin(usage, usage.c.conversation_id == Conversation.id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    total = await session.scalar(select(func.count()).select_from(Conversation))
    items = [
        ConversationSummary(
            id=c.id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
            message_count=n or 0,
            total_tokens=int(tokens or 0),
            unknown_usage_call_count=unknown or 0,
        )
        for c, n, tokens, unknown in rows
    ]
    return ConversationList(items=items, total=total or 0, limit=limit, offset=offset)


@router.get("/api/v1/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, session: AsyncSession = Depends(get_session), tr: Tracer = Depends(tracer)
):
    conv = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(
            selectinload(Conversation.turns).selectinload(Turn.user_message),
            selectinload(Conversation.turns).selectinload(Turn.runs).selectinload(Run.api_calls),
        )
    )
    if conv is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "会話が見つかりません"})

    msg_by_run = dict(
        (
            await session.execute(
                select(Message.run_id, Message.id).where(
                    Message.conversation_id == conv.id, Message.run_id.is_not(None)
                )
            )
        ).all()
    )
    last_seq = max((t.seq for t in conv.turns), default=0)
    turns = [
        TurnOut(
            id=t.id,
            seq=t.seq,
            user_message=MessageOut.model_validate(t.user_message) if t.user_message else None,
            adopted_message_id=t.adopted_message_id,
            runs=[
                run_to_out(r, message_id=msg_by_run.get(r.id), langfuse_url=tr.trace_url(r.langfuse_trace_id))
                for r in t.runs
            ],
            is_latest=t.seq == last_seq,
        )
        for t in conv.turns
    ]
    runs = [r for t in conv.turns for r in t.runs]
    calls = [c for r in runs for c in r.api_calls]
    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        turns=turns,
        stats=conversation_stats(runs, calls),
    )


def _chat_error(e: ChatError) -> HTTPException:
    return HTTPException(e.status_code, detail={"code": e.code, "message": e.message})


@router.post("/api/v1/conversations/{conversation_id}/messages", response_model=RunOut)
async def send_message(
    conversation_id: uuid.UUID, body: MessageCreate, chat: ChatService = Depends(chat_service)
):
    t0 = time.perf_counter()
    try:
        return await chat.send_message(
            conversation_id, body.prompt, body.web_search_enabled, body.search_url, t0
        )
    except ChatError as e:
        raise _chat_error(e) from e


@router.post("/api/v1/turns/{turn_id}/retry", response_model=RunOut)
async def retry_turn(turn_id: uuid.UUID, chat: ChatService = Depends(chat_service)):
    t0 = time.perf_counter()
    try:
        return await chat.retry_turn(turn_id, t0)
    except ChatError as e:
        raise _chat_error(e) from e


@router.get("/api/v1/runs/{run_id}", response_model=RunDetailOut)
async def get_run(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session), tr: Tracer = Depends(tracer)
):
    run = await session.get(Run, run_id, options=[selectinload(Run.api_calls)])
    if run is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "実行が見つかりません"})
    message_id = await session.scalar(select(Message.id).where(Message.run_id == run.id))
    return run_to_detail(run, message_id=message_id, langfuse_url=tr.trace_url(run.langfuse_trace_id))


@router.patch("/api/v1/runs/{run_id}/client-metrics", status_code=204)
async def put_client_metrics(
    run_id: uuid.UUID, body: ClientMetrics, session: AsyncSession = Depends(get_session)
):
    # 初回の値のみ採用する（再描画などで上書きしない）
    result = await session.execute(
        update(Run)
        .where(Run.id == run_id, Run.client_duration_ms.is_(None))
        .values(client_duration_ms=body.client_duration_ms)
    )
    await session.commit()
    if result.rowcount == 0 and await session.get(Run, run_id) is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "実行が見つかりません"})


@router.get("/api/v1/runs/{run_id}/langfuse")
async def langfuse_trace_status(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session), tr: Tracer = Depends(tracer)
):
    """トレースの送信状態と Langfuse 側への反映有無を返す。"""
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "実行が見つかりません"})
    exists = await tr.trace_exists(run.langfuse_trace_id) if run.langfuse_trace_id else None
    return {
        "trace_id": run.langfuse_trace_id,
        "status": run.langfuse_status,
        "reflected": exists,
        "url": tr.trace_url(run.langfuse_trace_id),
    }
