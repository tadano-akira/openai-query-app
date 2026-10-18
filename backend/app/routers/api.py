import time
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.models import ApiCall, Conversation, Message, Run, Turn
from app.routers.auth import current_user, require_csrf_header
from app.schemas import (
    CharacterOut,
    ClientMetrics,
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationSummary,
    MessageCreate,
    MessageOut,
    ModeList,
    ModelList,
    ModeOut,
    ModeStat,
    RetryCreate,
    RunDetailOut,
    RunOut,
    TurnOut,
    UsageSummary,
)
from app.services.character import IMAGE_TYPES, ImageKind, load_character
from app.services.chat import DEFAULT_TITLE, ChatError, ChatService
from app.services.prompts import DEFAULT_MODE, PromptError, load_modes
from app.services.serializers import conversation_stats, run_to_detail, run_to_out
from app.services.tracing import Tracer
from app.services.usage import usage_summary

# ここに定義する API はすべてログイン必須（ログイン・ヘルスチェックは routers/auth.py）
router = APIRouter(dependencies=[Depends(require_csrf_header), Depends(current_user)])


def chat_service(request: Request) -> ChatService:
    return request.app.state.chat


def tracer(request: Request) -> Tracer:
    return request.app.state.tracer


NOT_FOUND = {"code": "not_found", "message": "会話が見つかりません"}


async def _models_by_conversation(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """会話ごとに実行で使ったモデルを初回使用順で返す。"""
    if not ids:
        return {}
    first_used = func.min(Run.created_at)
    rows = await session.execute(
        select(Run.conversation_id, Run.model)
        .where(Run.conversation_id.in_(ids))
        .group_by(Run.conversation_id, Run.model)
        .order_by(Run.conversation_id, first_used)
    )
    result: dict[uuid.UUID, list[str]] = {}
    for cid, model in rows:
        result.setdefault(cid, []).append(model)
    return result


@router.get("/api/v1/models", response_model=ModelList)
async def list_models():
    settings = get_settings()
    return ModelList(models=settings.available_models, default=settings.openai_model)


@router.get("/api/v1/character", response_model=CharacterOut)
async def get_character():
    c = load_character()

    def url(kind: ImageKind) -> str | None:
        img = c.image(kind)
        return f"/api/v1/character/{kind}?v={img.version}" if img else None

    return CharacterOut(name=c.name, avatar_url=url("avatar"), portrait_url=url("portrait"))


@router.get("/api/v1/character/{kind}")
async def get_character_image(kind: ImageKind):
    img = load_character().image(kind)
    if img is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "画像が設定されていません"})
    return FileResponse(
        img.path,
        media_type=IMAGE_TYPES[img.path.suffix.lower()],
        headers={
            # URL に内容の版が付くので長くキャッシュしてよい
            "Cache-Control": "public, max-age=86400",
            # SVG を直接開かれてもスクリプトを実行させない
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/v1/modes", response_model=ModeList)
async def list_modes():
    try:
        modes = load_modes()
    except PromptError as e:
        raise HTTPException(503, detail={"code": "config_error", "message": str(e)}) from e
    return ModeList(
        modes=[
            ModeOut(
                id=m.id,
                label=m.label,
                description=m.description,
                verbosity=m.verbosity,
                reasoning_effort=m.reasoning_effort,
            )
            for m in modes
        ],
        default=DEFAULT_MODE,
    )


@router.get("/api/v1/stats/modes", response_model=list[ModeStat])
async def mode_stats(session: AsyncSession = Depends(get_session)):
    """モード別の実績（成功した実行の平均）。モード間の出力量・料金・時間の比較に使う。"""
    mode = func.coalesce(Run.mode, DEFAULT_MODE)
    rows = await session.execute(
        select(
            mode,
            Run.model,
            Run.web_search_enabled,
            func.count(),
            func.avg(Run.input_tokens),
            func.avg(Run.output_tokens),
            func.avg(Run.reasoning_tokens),
            func.avg(func.length(Run.answer)),
            func.avg(Run.estimated_cost_usd),
            func.avg(Run.openai_duration_ms),
        )
        .where(Run.status == "succeeded")
        .group_by(mode, Run.model, Run.web_search_enabled)
        .order_by(mode, Run.model, Run.web_search_enabled)
    )
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    return [
        ModeStat(
            mode=r[0],
            model=r[1],
            web_search_enabled=r[2],
            run_count=r[3],
            avg_input_tokens=f(r[4]),
            avg_output_tokens=f(r[5]),
            avg_reasoning_tokens=f(r[6]),
            avg_answer_chars=f(r[7]),
            avg_cost_usd=f(r[8]),
            avg_openai_duration_ms=f(r[9]),
        )
        for r in rows
    ]


@router.get("/api/v1/usage", response_model=UsageSummary)
async def get_usage(session: AsyncSession = Depends(get_session)):
    """当日・当月の実行回数と推定利用額、上限値。"""
    return await usage_summary(session, get_settings())


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
        .where(Conversation.hidden_at.is_(None))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    total = await session.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.hidden_at.is_(None))
    )
    rows = rows.all()
    models = await _models_by_conversation(session, [c.id for c, *_ in rows])
    items = [
        ConversationSummary(
            id=c.id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
            message_count=n or 0,
            total_tokens=int(tokens or 0),
            unknown_usage_call_count=unknown or 0,
            models=models.get(c.id, []),
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
        .where(Conversation.id == conversation_id, Conversation.hidden_at.is_(None))
        .options(
            selectinload(Conversation.turns).selectinload(Turn.user_message),
            selectinload(Conversation.turns).selectinload(Turn.runs).selectinload(Run.api_calls),
        )
    )
    if conv is None:
        raise HTTPException(404, detail=NOT_FOUND)

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
    models = list(dict.fromkeys(r.model for r in sorted(runs, key=lambda r: r.created_at)))
    calls = [c for r in runs for c in r.api_calls]
    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        models=models,
        turns=turns,
        stats=conversation_stats(runs, calls),
    )


@router.delete("/api/v1/conversations/{conversation_id}", status_code=204)
async def hide_conversation(conversation_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """会話を一覧から非表示にする。データは削除しない。"""
    result = await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.hidden_at.is_(None))
        .values(hidden_at=func.now())
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(404, detail=NOT_FOUND)


def _chat_error(e: ChatError) -> HTTPException:
    return HTTPException(e.status_code, detail={"code": e.code, "message": e.message})


@router.post("/api/v1/conversations/{conversation_id}/messages", response_model=RunOut)
async def send_message(
    conversation_id: uuid.UUID, body: MessageCreate, chat: ChatService = Depends(chat_service)
):
    t0 = time.perf_counter()
    try:
        return await chat.send_message(
            conversation_id,
            body.prompt,
            body.web_search_enabled,
            body.search_url,
            t0,
            model=body.model,
            mode=body.mode,
        )
    except ChatError as e:
        raise _chat_error(e) from e


@router.post("/api/v1/turns/{turn_id}/retry", response_model=RunOut)
async def retry_turn(
    turn_id: uuid.UUID,
    body: RetryCreate | None = Body(None),
    chat: ChatService = Depends(chat_service),
):
    t0 = time.perf_counter()
    try:
        return await chat.retry_turn(
            turn_id, t0, model=body.model if body else None, mode=body.mode if body else None
        )
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
