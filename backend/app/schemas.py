import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MessageCreate(BaseModel):
    prompt: str = Field(min_length=1)
    web_search_enabled: bool = True
    search_url: str | None = None
    # 未指定時は既定モデル。選択可能なモデルかはサービス側で検証する
    model: str | None = None
    # 回答スタイルのモード（prompts/modes/<id>.md）。未指定時は standard
    mode: str | None = None

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("プロンプトが空です")
        return v

    @field_validator("search_url")
    @classmethod
    def empty_url_to_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None

    @model_validator(mode="after")
    def url_requires_search(self) -> "MessageCreate":
        if self.search_url and not self.web_search_enabled:
            raise ValueError("Web検索OFFのときは検索先URLを指定できません")
        return self


class RetryCreate(BaseModel):
    # 未指定時は再実行元と同じモデル・モード
    model: str | None = None
    mode: str | None = None


class ModeOut(BaseModel):
    id: str
    label: str
    description: str
    # 対応モデル（gpt-5 系）にだけ付ける API パラメータ
    verbosity: str | None
    reasoning_effort: str | None


class ModeList(BaseModel):
    modes: list[ModeOut]
    default: str


class ModeStat(BaseModel):
    """成功した実行のモード×モデル×検索有無ごとの平均。"""

    mode: str
    model: str
    web_search_enabled: bool
    run_count: int
    avg_input_tokens: float | None
    avg_output_tokens: float | None
    avg_reasoning_tokens: float | None
    avg_answer_chars: float | None
    avg_cost_usd: float | None
    avg_openai_duration_ms: float | None


class ModelList(BaseModel):
    models: list[str]
    default: str


class UsagePeriod(BaseModel):
    run_count: int
    run_limit: int | None
    cost_usd: float
    cost_limit_usd: float | None


class UsageSummary(BaseModel):
    """推定料金による利用状況。上限 0 は無制限（null で返す）"""

    timezone: str
    today: UsagePeriod
    month: UsagePeriod
    unknown_cost_run_count: int


class ClientMetrics(BaseModel):
    client_duration_ms: int = Field(ge=0, le=24 * 3600 * 1000)


class ConversationCreate(BaseModel):
    title: str | None = None


class Usage(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


class Timings(BaseModel):
    backend_duration_ms: int | None
    openai_duration_ms: int | None
    db_duration_ms: int | None
    client_duration_ms: int | None


class ErrorInfo(BaseModel):
    code: str
    message: str | None


class ApiCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt: int
    model: str
    status: str
    started_at: datetime
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None
    openai_response_id: str | None
    error_code: str | None
    error_message: str | None


class RunOut(BaseModel):
    conversation_id: uuid.UUID
    turn_id: uuid.UUID
    request_id: uuid.UUID
    message_id: uuid.UUID | None
    retry_of_run_id: uuid.UUID | None
    status: str
    outcome: str | None
    persisted: bool
    prompt: str
    answer: str | None
    citations: list[dict[str, Any]]
    web_search_enabled: bool
    search_url: str | None
    searched_domain: str | None
    search_info: dict[str, Any] | None
    model: str
    mode: str
    prompt_version: str
    context_message_count: int
    created_at: datetime
    generated_at: datetime | None
    usage: Usage
    estimated_cost_usd: float | None
    timings: Timings
    error: ErrorInfo | None
    openai_response_id: str | None
    langfuse_trace_id: str | None
    langfuse_status: str | None
    langfuse_url: str | None


class RunDetailOut(RunOut):
    request_snapshot: dict[str, Any] | None
    context_message_ids: list[str]
    api_calls: list[ApiCallOut]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    citations: list[dict[str, Any]] | None
    run_id: uuid.UUID | None
    created_at: datetime


class TurnOut(BaseModel):
    id: uuid.UUID
    seq: int
    user_message: MessageOut | None
    adopted_message_id: uuid.UUID | None
    runs: list[RunOut]
    is_latest: bool


class ConversationStats(BaseModel):
    run_count: int
    succeeded_count: int
    failed_count: int
    api_call_count: int
    unknown_usage_call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    unknown_cost_run_count: int
    avg_client_duration_ms: float | None
    client_duration_sample_count: int
    avg_openai_duration_ms: float | None
    openai_duration_sample_count: int
    total_backend_duration_ms: int
    elapsed_ms: int | None


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    total_tokens: int
    unknown_usage_call_count: int
    # この会話の実行で使ったモデル（初回使用順）
    models: list[str] = []


class ConversationList(BaseModel):
    items: list[ConversationSummary]
    total: int
    limit: int
    offset: int


class ConversationDetail(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    models: list[str]
    turns: list[TurnOut]
    stats: ConversationStats
