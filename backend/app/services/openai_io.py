"""OpenAI Responses API の入出力変換（副作用なし）。"""

import ipaddress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import openai


class InvalidSearchUrl(ValueError):
    pass


def normalize_search_domain(url: str) -> str:
    """検索先URLから allowed_domains に渡すホスト名を取り出す（スキームなし・小文字）。"""
    raw = url.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise InvalidSearchUrl("URL は http または https で指定してください")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or "." not in host:
        raise InvalidSearchUrl("URL からドメインを取得できません")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise InvalidSearchUrl("IP アドレスではなく公開サイトのドメインを指定してください")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise InvalidSearchUrl("ローカルのホストは指定できません")
    return host


def supports_reasoning_params(model: str) -> bool:
    """text.verbosity / reasoning.effort を受け付けるモデルか（gpt-4.1 系は 400 になる。2026-10-12 確認）。"""
    return model.startswith("gpt-5")


def build_request(
    *,
    model: str,
    instructions: str,
    context: list[dict[str, str]],
    prompt: str,
    web_search_enabled: bool,
    searched_domain: str | None,
    max_output_tokens: int | None,
    verbosity: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """responses.create に渡す引数を組み立てる。この dict をそのまま送信内容のスナップショットにする。"""
    req: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": [*context, {"role": "user", "content": prompt}],
        "store": False,
    }
    if web_search_enabled:
        tool: dict[str, Any] = {"type": "web_search"}
        if searched_domain:
            tool["filters"] = {"allowed_domains": [searched_domain]}
        req["tools"] = [tool]
        # 検索ONでは検索を必ず実行させる。実際に実行されたかは応答側で確認する
        req["tool_choice"] = "required"
        req["include"] = ["web_search_call.action.sources"]
    if max_output_tokens:
        req["max_output_tokens"] = max_output_tokens
    # モードの API パラメータは対応モデルにだけ付ける（非対応モデルは指示文のみ適用）
    if supports_reasoning_params(model):
        if verbosity:
            req["text"] = {"verbosity": verbosity}
        if reasoning_effort:
            req["reasoning"] = {"effort": reasoning_effort}
    return req


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ParsedResponse:
    response_id: str | None
    status: str | None
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    search_info: dict[str, Any] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    incomplete_reason: str | None = None


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def parse_usage(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    raw = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    return Usage(
        input_tokens=_get(usage, "input_tokens"),
        output_tokens=_get(usage, "output_tokens"),
        total_tokens=_get(usage, "total_tokens"),
        cached_input_tokens=_get(_get(usage, "input_tokens_details"), "cached_tokens"),
        reasoning_tokens=_get(_get(usage, "output_tokens_details"), "reasoning_tokens"),
        raw=raw,
    )


def _is_safe_link(url: str | None) -> bool:
    return bool(url) and urlsplit(url).scheme in ("http", "https")


def parse_response(resp: Any) -> ParsedResponse:
    texts: list[str] = []
    citations: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    offset = 0

    for item in _get(resp, "output") or []:
        item_type = _get(item, "type")
        if item_type == "message":
            for part in _get(item, "content") or []:
                if _get(part, "type") != "output_text":
                    continue
                text = _get(part, "text") or ""
                for ann in _get(part, "annotations") or []:
                    if _get(ann, "type") != "url_citation" or not _is_safe_link(_get(ann, "url")):
                        continue
                    # 複数パートを連結するため、引用位置を連結後の本文基準に補正する
                    citations.append(
                        {
                            "url": _get(ann, "url"),
                            "title": _get(ann, "title"),
                            "start_index": offset + (_get(ann, "start_index") or 0),
                            "end_index": offset + (_get(ann, "end_index") or 0),
                        }
                    )
                texts.append(text)
                offset += len(text)
        elif item_type == "web_search_call":
            action = _get(item, "action")
            sources = [
                {"url": _get(s, "url")}
                for s in (_get(action, "sources") or [])
                if _is_safe_link(_get(s, "url"))
            ]
            calls.append(
                {
                    "id": _get(item, "id"),
                    "status": _get(item, "status"),
                    "action": _get(action, "type"),
                    "query": _get(action, "query"),
                    "queries": _get(action, "queries"),
                    "url": _get(action, "url"),
                    "sources": sources,
                }
            )

    return ParsedResponse(
        response_id=_get(resp, "id"),
        status=_get(resp, "status"),
        answer="".join(texts),
        citations=citations,
        search_info={
            "executed": any(c["status"] == "completed" for c in calls),
            "call_count": len(calls),
            "calls": calls,
        },
        usage=parse_usage(_get(resp, "usage")),
        incomplete_reason=_get(_get(resp, "incomplete_details"), "reason"),
    )


@dataclass
class ClassifiedError:
    code: str
    message: str
    retriable: bool


def classify_error(exc: BaseException) -> ClassifiedError:
    """例外をアプリのエラー区分へ変換する。メッセージに機密情報を含めない。"""
    if isinstance(exc, openai.APITimeoutError):
        return ClassifiedError("timeout", "OpenAI API の応答がタイムアウトしました", True)
    if isinstance(exc, openai.APIConnectionError):
        return ClassifiedError("network_error", "OpenAI API に接続できませんでした", True)
    if isinstance(exc, openai.RateLimitError):
        code = _get(exc.body, "code") if isinstance(exc.body, dict) else None
        if code == "insufficient_quota":
            return ClassifiedError("quota_exceeded", "OpenAI API の利用枠が不足しています", False)
        return ClassifiedError("rate_limited", "OpenAI API の利用制限に達しました", True)
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ClassifiedError(
            "auth_error", "OpenAI API の認証・権限エラーです。APIキーを確認してください", False
        )
    if isinstance(exc, openai.BadRequestError):
        code = _get(exc.body, "code") if isinstance(exc.body, dict) else None
        if code == "context_length_exceeded":
            return ClassifiedError("context_limit_exceeded", "会話の文脈がモデルの上限を超えました", False)
        return ClassifiedError("bad_request", f"OpenAI API がリクエストを拒否しました: {exc.message}", False)
    if isinstance(exc, openai.NotFoundError):
        return ClassifiedError("bad_request", f"モデル等が見つかりません: {exc.message}", False)
    if isinstance(exc, openai.APIStatusError):
        retriable = exc.status_code >= 500
        return ClassifiedError(
            "upstream_error" if retriable else "bad_request",
            f"OpenAI API エラー (HTTP {exc.status_code})",
            retriable,
        )
    return ClassifiedError("internal_error", "回答生成中に予期しないエラーが発生しました", False)
