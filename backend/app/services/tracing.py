"""Langfuse 連携。記録はベストエフォートとし、失敗しても回答処理を止めない。"""

import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any

import httpx
from langfuse import Langfuse, propagate_attributes

from app.config import Settings

log = logging.getLogger(__name__)


class LangfuseStatus:
    DISABLED = "disabled"  # 認証情報が未設定
    QUEUED = "queued"  # SDK の送信キューへ投入済み（反映は非同期）
    FAILED = "failed"  # トレース作成時に例外


class _Observation:
    """observation.update の失敗を握りつぶすラッパー。"""

    def __init__(self, obs: Any | None) -> None:
        self._obs = obs

    def update(self, **kwargs: Any) -> None:
        if self._obs is None:
            return
        try:
            self._obs.update(**kwargs)
        except Exception:
            log.warning("langfuse observation update failed", exc_info=True)


class Tracer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: Langfuse | None = None
        if settings.langfuse_enabled:
            try:
                self.client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    base_url=settings.langfuse_base_url,
                )
            except Exception:
                log.exception("langfuse client init failed")

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def new_trace_id(self, seed: str) -> str | None:
        if self.client is None:
            return None
        try:
            return self.client.create_trace_id(seed=seed)
        except Exception:
            log.warning("langfuse create_trace_id failed", exc_info=True)
            return None

    def trace_url(self, trace_id: str | None) -> str | None:
        if not trace_id or not self.settings.langfuse_project_id:
            return None
        base = self.settings.langfuse_public_url.rstrip("/")
        return f"{base}/project/{self.settings.langfuse_project_id}/traces/{trace_id}"

    @contextmanager
    def _observe(self, enter: Any) -> Iterator[_Observation]:
        stack = ExitStack()
        try:
            obs = enter(stack)
        except Exception:
            log.warning("langfuse observation start failed", exc_info=True)
            stack.close()
            yield _Observation(None)
            return
        with stack:
            yield _Observation(obs)

    def run_span(
        self, *, trace_id: str | None, session_id: str, name: str, input: Any, metadata: dict[str, str]
    ):
        """実行単位のトレース（ルート span）。会話IDを session_id として束ねる。"""
        if self.client is None or trace_id is None:
            return self._observe(lambda _s: None)
        client = self.client

        def enter(stack: ExitStack) -> Any:
            obs = stack.enter_context(
                client.start_as_current_observation(
                    trace_context={"trace_id": trace_id},
                    name=name,
                    as_type="span",
                    input=input,
                    metadata=metadata,
                )
            )
            stack.enter_context(
                propagate_attributes(session_id=session_id, trace_name=name, metadata=metadata)
            )
            return obs

        return self._observe(enter)

    def generation(self, *, name: str, model: str, input: Any, metadata: dict[str, Any]):
        """API 呼び出し単位の generation。run_span の内側で使う。"""
        if self.client is None:
            return self._observe(lambda _s: None)
        client = self.client
        return self._observe(
            lambda stack: stack.enter_context(
                client.start_as_current_observation(
                    name=name, as_type="generation", model=model, input=input, metadata=metadata
                )
            )
        )

    async def trace_exists(self, trace_id: str) -> bool | None:
        """Langfuse に反映済みか確認する。確認できない場合は None。"""
        if not self.settings.langfuse_enabled:
            return None
        # Langfuse v4 (events_only) では /api/public/traces が無いため v2 observations で確認する
        url = f"{self.settings.langfuse_base_url.rstrip('/')}/api/public/v2/observations"
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                r = await http.get(
                    url,
                    params={"traceId": trace_id, "fields": "core", "limit": 1},
                    auth=(self.settings.langfuse_public_key, self.settings.langfuse_secret_key),
                )
            if r.status_code != 200:
                return None
            return bool(r.json().get("data"))
        except (httpx.HTTPError, ValueError):
            return None

    def flush(self) -> None:
        if self.client is None:
            return
        try:
            self.client.flush()
        except Exception:
            log.warning("langfuse flush failed", exc_info=True)

    def shutdown(self) -> None:
        if self.client is None:
            return
        try:
            self.client.shutdown()
        except Exception:
            log.warning("langfuse shutdown failed", exc_info=True)
