from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str

    openai_api_key: str = ""
    openai_model: str = "gpt-5.5"
    # 画面で選択できるモデル（カンマ区切り）。openai_model は常に含める
    openai_models: str = "gpt-5.5,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.4,gpt-5.4-mini,gpt-5.4-nano,gpt-4.1,gpt-4.1-mini"
    openai_timeout_seconds: float = 120.0
    # 自動再試行は既定で行わない（重複課金を避ける）。対象は 429 / 5xx / 接続エラーのみ
    openai_max_retries: int = 0
    max_output_tokens: int | None = None
    prompt_version: str = "v1"
    system_prompt: str = (
        "あなたは日本語で回答するアシスタントです。"
        "Web検索結果を使う場合は出典を明示し、取得できなかった情報は推測で補わずその旨を述べてください。"
    )

    max_prompt_chars: int = 20_000
    # 文脈上限（文字数ベースの簡易判定）。超過時は送信せずエラーとして通知する
    max_context_chars: int = 200_000

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "http://host.docker.internal:3000"
    langfuse_public_url: str = "http://localhost:3000"
    langfuse_project_id: str = ""

    @property
    def available_models(self) -> list[str]:
        models = [m.strip() for m in self.openai_models.split(",") if m.strip()]
        return [self.openai_model, *(m for m in models if m != self.openai_model)]

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
