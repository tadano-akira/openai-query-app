import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus:
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class User(Base):
    """ログインできる利用者（当面は本人 1 名）。パスワードは Argon2 のハッシュだけを保存する"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # 小文字に正規化して保存する
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 連続失敗回数と、ロック解除日時（総当たり対策）
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """ログイン状態。Cookie のトークンそのものではなく SHA-256 だけを保存する（DB が漏れても乗っ取れない）"""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(300))

    user: Mapped[User] = relationship()


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # 画面上の削除は非表示にするだけ（データは残す）
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    turns: Mapped[list["Turn"]] = relationship(back_populates="conversation", order_by="Turn.seq")


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    user_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", use_alter=True, deferrable=True, initially="DEFERRED")
    )
    # 次の文脈に使う「最新の成功回答」
    adopted_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", use_alter=True, deferrable=True, initially="DEFERRED")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="turns")
    user_message: Mapped["Message | None"] = relationship(foreign_keys=[user_message_id])
    adopted_message: Mapped["Message | None"] = relationship(foreign_keys=[adopted_message_id])
    runs: Mapped[list["Run"]] = relationship(back_populates="turn", order_by="Run.created_at")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", use_alter=True, deferrable=True, initially="DEFERRED")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"), index=True)
    retry_of_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"))
    status: Mapped[str] = mapped_column(String(20), index=True)
    # 結果の区分: answered / search_not_executed / incomplete など（通信障害とは別に扱う）
    outcome: Mapped[str | None] = mapped_column(String(40))

    # 入力・実行条件
    prompt: Mapped[str] = mapped_column(Text)
    web_search_enabled: Mapped[bool] = mapped_column(Boolean)
    search_url: Mapped[str | None] = mapped_column(Text)
    searched_domain: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(100))
    # 回答スタイルのモード（prompts/modes/<id>.md）。導入前の実行は standard
    mode: Mapped[str | None] = mapped_column(String(40))
    # システム指示（基本ルール＋モード）と API パラメータのハッシュ
    prompt_version: Mapped[str] = mapped_column(String(40))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    context_message_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    request_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # 出力
    answer: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    search_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # 時刻（UTC）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 時間（ms）。未取得は null
    backend_duration_ms: Mapped[int | None] = mapped_column(Integer)
    openai_duration_ms: Mapped[int | None] = mapped_column(Integer)
    db_duration_ms: Mapped[int | None] = mapped_column(Integer)
    client_duration_ms: Mapped[int | None] = mapped_column(Integer)

    # 使用量（実行内の API 呼び出しの合計。不明は null）
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)
    # モデル別単価による推定料金（USD）。単価未定義のモデルは null
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6, asdecimal=False))

    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)

    openai_response_id: Mapped[str | None] = mapped_column(String(100))
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64))
    # disabled / queued / failed
    langfuse_status: Mapped[str | None] = mapped_column(String(20))

    turn: Mapped[Turn] = relationship(back_populates="runs")
    api_calls: Mapped[list["ApiCall"]] = relationship(back_populates="run", order_by="ApiCall.attempt")


class ApiCall(Base):
    __tablename__ = "api_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)
    usage_raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    openai_response_id: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="api_calls")
