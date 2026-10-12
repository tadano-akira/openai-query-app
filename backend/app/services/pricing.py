"""モデル別単価による推定料金の計算。

単価は https://developers.openai.com/api/docs/pricing の Standard tier（2026-10-12 確認）。
OpenAI の請求額そのものではなく、API が返す使用量からの推定値として扱う。
"""

from dataclasses import dataclass

# 入力がこのトークン数を超える呼び出しは長文脈単価（定義があるモデルのみ）
LONG_CONTEXT_THRESHOLD = 272_000
# Web 検索ツールの呼び出し料金（全モデル共通、$10 / 1k calls）。検索結果の本文は入力トークンとして課金される
WEB_SEARCH_CALL_USD = 0.01


@dataclass(frozen=True)
class Price:
    """100 万トークンあたりの USD 単価。"""

    input: float
    cached_input: float
    output: float


@dataclass(frozen=True)
class ModelPrice:
    standard: Price
    long_context: Price | None = None


PRICES: dict[str, ModelPrice] = {
    "gpt-5.5": ModelPrice(Price(5.00, 0.50, 30.00), Price(10.00, 1.00, 45.00)),
    "gpt-5.6-sol": ModelPrice(Price(4.00, 0.40, 20.00)),
    "gpt-5.6-terra": ModelPrice(Price(2.00, 0.20, 12.00)),
    "gpt-5.6-luna": ModelPrice(Price(0.20, 0.02, 1.20)),
    "gpt-5.4": ModelPrice(Price(2.50, 0.25, 15.00), Price(5.00, 0.50, 22.50)),
    "gpt-5.4-mini": ModelPrice(Price(0.75, 0.075, 4.50)),
    "gpt-5.4-nano": ModelPrice(Price(0.20, 0.02, 1.25)),
    "gpt-4.1": ModelPrice(Price(2.00, 0.50, 8.00)),
    "gpt-4.1-mini": ModelPrice(Price(0.40, 0.10, 1.60)),
}


def call_cost_usd(
    model: str, input_tokens: int | None, cached_input_tokens: int | None, output_tokens: int | None
) -> float | None:
    """API 呼び出し 1 回のトークン料金。単価未定義のモデルは None。

    出力トークンは推論トークンを含む。使用量が返らなかった呼び出しは 0 とする。
    """
    price = PRICES.get(model)
    if price is None:
        return None
    inp, cached, out = input_tokens or 0, cached_input_tokens or 0, output_tokens or 0
    p = price.long_context if price.long_context and inp > LONG_CONTEXT_THRESHOLD else price.standard
    cached = min(cached, inp)
    return ((inp - cached) * p.input + cached * p.cached_input + out * p.output) / 1_000_000


def run_cost_usd(
    model: str, calls: list[tuple[int | None, int | None, int | None]], search_calls: int
) -> float | None:
    """実行 1 回の推定料金 = 各 API 呼び出しのトークン料金 + Web 検索の呼び出し料金。"""
    costs = [call_cost_usd(model, *c) for c in calls]
    if any(c is None for c in costs):
        return None
    return sum(costs) + search_calls * WEB_SEARCH_CALL_USD
