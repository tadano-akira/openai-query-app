from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str

    openai_api_key: str = ""
    openai_model: str = "gpt-5.5"
    # 画面で選択できるモデル（カンマ区切り）。openai_model は常に含める
    openai_models: str = "gpt-5.5,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.4,gpt-5.4-mini,gpt-5.4-nano,gpt-4.1,gpt-4.1-mini"
    # Cloudflare Tunnel 経由の配信は 100 秒で打ち切られるため、それより短くする
    openai_timeout_seconds: float = 90.0
    # 自動再試行は既定で行わない（重複課金を避ける）。対象は 429 / 5xx / 接続エラーのみ
    openai_max_retries: int = 0
    # 推論トークンを含む出力上限
    max_output_tokens: int | None = 8_000
    # システム指示は backend/prompts/（base.md と modes/*.md）で管理する

    max_prompt_chars: int = 20_000
    # 文脈上限（文字数ベースの簡易判定）。超過時は送信せずエラーとして通知する
    max_context_chars: int = 60_000

    # 利用上限（0 は無制限）。料金はモデル別単価からの推定値で判定する
    daily_run_limit: int = 100
    daily_cost_limit_usd: float = 3.0
    monthly_cost_limit_usd: float = 30.0
    # 「1日」「1か月」の区切りに使うタイムゾーン
    usage_timezone: str = "Asia/Tokyo"

    # ログイン（メールアドレス＋パスワード、利用者は 1 人。アカウントは python -m app.cli で作る）
    # 最後に使ってからこの日数でログイン状態が切れる
    session_days: int = 30
    # HTTPS で配信するとき（t430）は true にして Cookie を HTTPS 限定にする
    cookie_secure: bool = False
    # 連続でこの回数失敗すると、login_lock_minutes 分ログインできなくする
    login_max_failures: int = 5
    login_lock_minutes: int = 15

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
