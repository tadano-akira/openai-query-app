"""メッセージ送信・再実行の処理本体。"""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db import SessionLocal
from app.models import ApiCall, Conversation, Message, Run, RunStatus, Turn, utcnow
from app.services import openai_io, pricing, prompts
from app.services.serializers import run_to_out
from app.services.tracing import LangfuseStatus, Tracer
from app.services.usage import exceeded_reason, usage_summary

log = logging.getLogger(__name__)

TITLE_MAX = 40
DEFAULT_TITLE = "新規チャット"


class ChatError(Exception):
    """実行を作成する前に失敗した場合のエラー（HTTP エラーとして返す）。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# 同時実行は 1 件（FastAPI は 1 ワーカー前提）
_run_lock = asyncio.Lock()


@asynccontextmanager
async def single_run():
    if _run_lock.locked():
        raise ChatError(409, "busy", "他の質問を処理中です。完了してから送信してください")
    async with _run_lock:
        yield


@dataclass
class DbTimer:
    ms: float = 0.0

    @asynccontextmanager
    async def measure(self):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.ms += (time.perf_counter() - t) * 1000


@dataclass
class Prepared:
    run: Run
    conversation: Conversation
    turn: Turn
    context: list[dict[str, str]]
    request: dict
    db: DbTimer = field(default_factory=DbTimer)


def build_context(turns: list[Turn]) -> tuple[list[dict[str, str]], list[str]]:
    """過去ターンの確定したユーザー／アシスタントメッセージだけで文脈を作る。

    成功回答のないターン（失敗のみ）は文脈に含めない。
    """
    context: list[dict[str, str]] = []
    ids: list[str] = []
    for t in sorted(turns, key=lambda x: x.seq):
        if t.user_message is None or t.adopted_message is None:
            continue
        context.append({"role": "user", "content": t.user_message.content})
        context.append({"role": "assistant", "content": t.adopted_message.content})
        ids.extend([str(t.user_message.id), str(t.adopted_message.id)])
    return context, ids


def _context_chars(context: list[dict[str, str]], prompt: str, instructions: str) -> int:
    return sum(len(m["content"]) for m in context) + len(prompt) + len(instructions)


class ChatService:
    def __init__(self, settings: Settings, tracer: Tracer) -> None:
        self.settings = settings
        self.tracer = tracer
        self.openai = (
            AsyncOpenAI(
                api_key=settings.openai_api_key,
                timeout=settings.openai_timeout_seconds,
                max_retries=0,  # 再試行はアプリ側で制御し、試行ごとに記録する
            )
            if settings.openai_api_key
            else None
        )

    # ---- 入口 ---------------------------------------------------------------

    async def send_message(
        self,
        conversation_id: uuid.UUID,
        prompt: str,
        web_search_enabled: bool,
        search_url: str | None,
        t0: float,
        model: str | None = None,
        mode: str | None = None,
    ):
        model = self._model(model)
        instr = self._instructions(mode)
        searched_domain = self._domain(search_url) if web_search_enabled and search_url else None
        if len(prompt) > self.settings.max_prompt_chars:
            raise ChatError(
                422,
                "prompt_too_long",
                f"プロンプトは {self.settings.max_prompt_chars} 文字以内で入力してください",
            )

        async with single_run(), SessionLocal() as session:
            db = DbTimer()
            async with db.measure():
                conv = await self._load_conversation(session, conversation_id)
                await self._check_usage(session)
            context, context_ids = build_context(conv.turns)
            self._check_context(context, prompt, instr.text)

            turn = Turn(id=uuid.uuid4(), conversation_id=conv.id, seq=len(conv.turns) + 1)
            user_msg = Message(
                id=uuid.uuid4(), conversation_id=conv.id, turn_id=turn.id, role="user", content=prompt
            )
            turn.user_message_id = user_msg.id
            run = self._new_run(
                conv, turn, prompt, model, instr, web_search_enabled, search_url, searched_domain, context_ids
            )
            request = self._build_request(model, instr, context, prompt, web_search_enabled, searched_domain)
            run.request_snapshot = request

            if conv.title == DEFAULT_TITLE and not conv.turns:
                conv.title = prompt.strip().splitlines()[0][:TITLE_MAX]
            conv.updated_at = utcnow()

            await self._commit_initial(session, db, [turn, user_msg, run])
            return await self._execute(session, Prepared(run, conv, turn, context, request, db), t0)

    async def retry_turn(
        self, turn_id: uuid.UUID, t0: float, model: str | None = None, mode: str | None = None
    ):
        if model is not None:
            model = self._model(model)
        if mode is not None:
            self._instructions(mode)  # ロック取得前に検証する
        async with single_run(), SessionLocal() as session:
            db = DbTimer()
            async with db.measure():
                turn = await session.get(Turn, turn_id, options=[selectinload(Turn.runs)])
                if turn is None:
                    raise ChatError(404, "not_found", "ターンが見つかりません")
                conv = await self._load_conversation(session, turn.conversation_id)
                await self._check_usage(session)
            if max(t.seq for t in conv.turns) != turn.seq:
                raise ChatError(409, "not_latest_turn", "再実行できるのは直近のターンだけです")
            if not turn.runs or turn.user_message is None:
                raise ChatError(409, "no_previous_run", "再実行元の実行がありません")

            base = turn.runs[-1]
            model = model or base.model
            instr = self._instructions(mode or base.mode)
            prompt = turn.user_message.content
            context, context_ids = build_context([t for t in conv.turns if t.seq < turn.seq])
            self._check_context(context, prompt, instr.text)

            run = self._new_run(
                conv,
                turn,
                prompt,
                model,
                instr,
                base.web_search_enabled,
                base.search_url,
                base.searched_domain,
                context_ids,
            )
            run.retry_of_run_id = base.id
            request = self._build_request(
                model, instr, context, prompt, base.web_search_enabled, base.searched_domain
            )
            run.request_snapshot = request
            conv.updated_at = utcnow()

            await self._commit_initial(session, db, [run])
            return await self._execute(session, Prepared(run, conv, turn, context, request, db), t0)

    # ---- 内部処理 -------------------------------------------------------------

    def _model(self, model: str | None) -> str:
        if model is None:
            return self.settings.openai_model
        if model not in self.settings.available_models:
            raise ChatError(422, "invalid_model", f"選択できないモデルです: {model}")
        return model

    def _instructions(self, mode: str | None) -> prompts.Instructions:
        try:
            return prompts.build_instructions(mode)
        except prompts.PromptError as e:
            if isinstance(e, prompts.UnknownMode):
                raise ChatError(422, "invalid_mode", str(e)) from e
            # ファイルの破損などは設定不備として扱う
            log.exception("failed to load prompts")
            raise ChatError(503, "config_error", f"システム指示を読み込めません: {e}") from e

    def _domain(self, search_url: str) -> str:
        try:
            return openai_io.normalize_search_domain(search_url)
        except openai_io.InvalidSearchUrl as e:
            raise ChatError(422, "invalid_search_url", str(e)) from e

    def _check_context(self, context: list[dict[str, str]], prompt: str, instructions: str) -> None:
        size = _context_chars(context, prompt, instructions)
        if size > self.settings.max_context_chars:
            raise ChatError(
                422,
                "context_limit_exceeded",
                f"会話の文脈が上限（{self.settings.max_context_chars:,} 文字）を超えています（{size:,} 文字）。"
                "新規チャットで続けてください",
            )

    async def _check_usage(self, session: AsyncSession) -> None:
        reason = exceeded_reason(await usage_summary(session, self.settings))
        if reason:
            raise ChatError(429, "usage_limit_exceeded", reason)

    async def _load_conversation(self, session: AsyncSession, conversation_id: uuid.UUID) -> Conversation:
        conv = await session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.hidden_at.is_(None))
            .options(
                selectinload(Conversation.turns).selectinload(Turn.user_message),
                selectinload(Conversation.turns).selectinload(Turn.adopted_message),
            )
        )
        if conv is None:
            raise ChatError(404, "not_found", "会話が見つかりません")
        return conv

    def _new_run(
        self, conv, turn, prompt, model, instr, web_search_enabled, search_url, searched_domain, context_ids
    ) -> Run:
        now = utcnow()
        run = Run(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            turn_id=turn.id,
            status=RunStatus.RUNNING,
            prompt=prompt,
            web_search_enabled=web_search_enabled,
            search_url=search_url if web_search_enabled else None,
            searched_domain=searched_domain,
            model=model,
            mode=instr.mode.id,
            prompt_version=instr.version,
            settings={
                "timeout_seconds": self.settings.openai_timeout_seconds,
                "max_retries": self.settings.openai_max_retries,
                "max_output_tokens": self.settings.max_output_tokens,
                # 実際に API へ付けたモードのパラメータ（非対応モデルでは null）
                **self._mode_params(model, instr.mode),
            },
            context_message_ids=context_ids,
            created_at=now,
            started_at=now,
        )
        run.langfuse_trace_id = self.tracer.new_trace_id(seed=str(run.id))
        if not self.tracer.enabled:
            run.langfuse_status = LangfuseStatus.DISABLED
        return run

    @staticmethod
    def _mode_params(model: str, mode: prompts.Mode) -> dict[str, str | None]:
        ok = openai_io.supports_reasoning_params(model)
        return {
            "verbosity": mode.verbosity if ok else None,
            "reasoning_effort": mode.reasoning_effort if ok else None,
        }

    def _build_request(self, model, instr, context, prompt, web_search_enabled, searched_domain) -> dict:
        return openai_io.build_request(
            model=model,
            instructions=instr.text,
            verbosity=instr.mode.verbosity,
            reasoning_effort=instr.mode.reasoning_effort,
            context=context,
            prompt=prompt,
            web_search_enabled=web_search_enabled,
            searched_domain=searched_domain,
            max_output_tokens=self.settings.max_output_tokens,
        )

    async def _commit_initial(self, session: AsyncSession, db: DbTimer, objs: list) -> None:
        """objs を親から順に flush してから commit する（relationship 未定義の FK の挿入順を保証）。"""
        try:
            async with db.measure():
                for obj in objs:
                    session.add(obj)
                    await session.flush()
                await session.commit()
        except SQLAlchemyError as e:
            log.exception("failed to persist run before execution")
            raise ChatError(503, "db_error", "履歴を保存できないため実行を中止しました") from e

    async def _call_openai(
        self, p: Prepared
    ) -> tuple[object | None, list[ApiCall], openai_io.ClassifiedError | None]:
        run = p.run
        calls: list[ApiCall] = []
        if self.openai is None:
            return (
                None,
                calls,
                openai_io.ClassifiedError("config_error", "OPENAI_API_KEY が設定されていません", False),
            )

        max_attempts = 1 + max(0, self.settings.openai_max_retries)
        error: openai_io.ClassifiedError | None = None
        for attempt in range(1, max_attempts + 1):
            call = ApiCall(
                id=uuid.uuid4(),
                run_id=run.id,
                attempt=attempt,
                model=run.model,
                status="running",
                started_at=utcnow(),
            )
            calls.append(call)
            with self.tracer.generation(
                name="openai.responses.create",
                model=run.model,
                input=p.request["input"],
                metadata={
                    "attempt": attempt,
                    "api_call_id": str(call.id),
                    "tools": p.request.get("tools"),
                    "mode": run.mode,
                    "text": p.request.get("text"),
                    "reasoning": p.request.get("reasoning"),
                },
            ) as gen:
                t = time.perf_counter()
                try:
                    resp = await self.openai.responses.create(**p.request)
                except Exception as e:  # noqa: BLE001 - 分類して記録する
                    call.duration_ms = round((time.perf_counter() - t) * 1000)
                    error = openai_io.classify_error(e)
                    if error.code == "internal_error":
                        log.exception("unexpected error calling OpenAI")
                    call.status = "failed"
                    call.error_code, call.error_message = error.code, error.message
                    gen.update(level="ERROR", status_message=f"{error.code}: {error.message}")
                else:
                    call.duration_ms = round((time.perf_counter() - t) * 1000)
                    parsed = openai_io.parse_response(resp)
                    u = parsed.usage
                    call.status = "succeeded"
                    call.openai_response_id = parsed.response_id
                    call.input_tokens, call.output_tokens, call.total_tokens = (
                        u.input_tokens,
                        u.output_tokens,
                        u.total_tokens,
                    )
                    call.cached_input_tokens, call.reasoning_tokens = (
                        u.cached_input_tokens,
                        u.reasoning_tokens,
                    )
                    call.usage_raw = u.raw
                    usage_details = {
                        k: v
                        for k, v in {
                            "input": u.input_tokens,
                            "output": u.output_tokens,
                            "total": u.total_tokens,
                            "input_cached_tokens": u.cached_input_tokens,
                            "output_reasoning_tokens": u.reasoning_tokens,
                        }.items()
                        if v is not None
                    }
                    gen.update(
                        output={
                            "text": parsed.answer,
                            "citations": parsed.citations,
                            "search": parsed.search_info,
                        },
                        usage_details=usage_details,
                        metadata={"openai_response_id": parsed.response_id, "status": parsed.status},
                    )
                    return resp, calls, None
            if not error.retriable or attempt == max_attempts:
                break
            await asyncio.sleep(min(2 ** (attempt - 1), 8))
        return None, calls, error

    async def _execute(self, session: AsyncSession, p: Prepared, t0: float):
        run, conv, turn, db = p.run, p.conversation, p.turn, p.db
        metadata = {
            "conversation_id": str(conv.id),
            "turn_id": str(turn.id),
            "run_id": str(run.id),
            "mode": run.mode or prompts.DEFAULT_MODE,
            "prompt_version": run.prompt_version,
        }

        with self.tracer.run_span(
            trace_id=run.langfuse_trace_id,
            session_id=str(conv.id),
            name="chat-run",
            input={"prompt": run.prompt, "web_search": run.web_search_enabled, "domain": run.searched_domain},
            metadata=metadata,
        ) as root:
            resp, calls, error = await self._call_openai(p)
            parsed = openai_io.parse_response(resp) if resp is not None else None
            if parsed is not None:
                root.update(output={"answer": parsed.answer})
            else:
                root.update(level="ERROR", status_message=error.code if error else "unknown")
        if self.tracer.enabled and run.langfuse_trace_id and run.langfuse_status is None:
            run.langfuse_status = LangfuseStatus.QUEUED
        elif run.langfuse_status is None:
            run.langfuse_status = LangfuseStatus.FAILED

        # ---- 結果を実行へ反映
        run.completed_at = utcnow()
        durations = [c.duration_ms for c in calls if c.duration_ms is not None]
        run.openai_duration_ms = sum(durations) if durations else None
        for f in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens"):
            known = [getattr(c, f) for c in calls if getattr(c, f) is not None]
            setattr(run, f, sum(known) if known else None)
        run.estimated_cost_usd = pricing.run_cost_usd(
            run.model,
            [(c.input_tokens, c.cached_input_tokens, c.output_tokens) for c in calls],
            parsed.search_info.get("call_count", 0) if parsed is not None else 0,
        )

        assistant_msg: Message | None = None
        if parsed is not None:
            run.status = RunStatus.SUCCEEDED
            run.openai_response_id = parsed.response_id
            run.answer = parsed.answer
            run.citations = parsed.citations
            run.search_info = parsed.search_info
            if parsed.status == "incomplete":
                run.outcome = f"incomplete:{parsed.incomplete_reason or 'unknown'}"
            elif run.web_search_enabled and not parsed.search_info.get("executed"):
                run.outcome = "search_not_executed"
            else:
                run.outcome = "answered"
            assistant_msg = Message(
                id=uuid.uuid4(),
                conversation_id=conv.id,
                turn_id=turn.id,
                role="assistant",
                content=parsed.answer,
                citations=parsed.citations,
                run_id=run.id,
            )
            turn.adopted_message_id = assistant_msg.id
        else:
            run.status = RunStatus.FAILED
            run.error_code = error.code if error else "internal_error"
            run.error_message = error.message if error else None
        conv.updated_at = utcnow()

        persisted = True
        session.add_all([*calls, *([assistant_msg] if assistant_msg else [])])
        try:
            async with db.measure():
                await session.commit()
        except SQLAlchemyError:
            # 回答は返すが「履歴保存済み」とは扱わない。
            # rollback すると属性が expire されるため、セッションの破棄は async with の終了に任せる
            log.exception("failed to persist run result run_id=%s", run.id)
            persisted = False

        # バックエンド処理時間 = 受付〜結果保存完了（この追記とシリアライズは含まない）
        run.backend_duration_ms = round((time.perf_counter() - t0) * 1000)
        run.db_duration_ms = round(db.ms)
        if persisted:
            try:
                await session.commit()
            except SQLAlchemyError:
                log.warning("failed to persist timings run_id=%s", run.id, exc_info=True)

        return run_to_out(
            run,
            message_id=assistant_msg.id if assistant_msg else None,
            persisted=persisted,
            langfuse_url=self.tracer.trace_url(run.langfuse_trace_id),
        )


async def mark_interrupted_runs() -> int:
    """再起動時に処理中のまま残った実行を中断状態にする。"""
    async with SessionLocal() as session:
        runs = (await session.scalars(select(Run).where(Run.status == RunStatus.RUNNING))).all()
        for r in runs:
            r.status = RunStatus.INTERRUPTED
            r.error_code = "interrupted"
            r.error_message = "アプリの再起動により処理が中断されました"
            r.completed_at = utcnow()
        await session.commit()
        return len(runs)
