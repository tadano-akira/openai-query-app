from datetime import UTC, datetime

import pytest

from app.schemas import UsagePeriod, UsageSummary
from app.services import pricing
from app.services.usage import exceeded_reason, period_starts


def test_call_cost_splits_cached_input_and_includes_output():
    # gpt-5.5: input $5 / cached $0.50 / output $30 per 1M
    cost = pricing.call_cost_usd("gpt-5.5", 10_000, 4_000, 1_000)
    assert cost == pytest.approx((6_000 * 5 + 4_000 * 0.5 + 1_000 * 30) / 1_000_000)


def test_call_cost_uses_long_context_price_above_threshold():
    cost = pricing.call_cost_usd("gpt-5.5", 300_000, 0, 0)
    assert cost == pytest.approx(300_000 * 10 / 1_000_000)


def test_run_cost_adds_web_search_calls_and_unknown_model_is_none():
    cost = pricing.run_cost_usd("gpt-5.6-luna", [(1_000_000, 0, 0), (None, None, None)], search_calls=2)
    assert cost == pytest.approx(0.20 + 2 * pricing.WEB_SEARCH_CALL_USD)
    assert pricing.run_cost_usd("unknown-model", [(1, 0, 1)], 0) is None


def test_period_starts_in_tokyo():
    # 2026-10-31 16:00 UTC = 2026-11-01 01:00 JST → 当日・当月とも JST の 11/1 0:00
    day, month = period_starts("Asia/Tokyo", datetime(2026, 10, 31, 16, 0, tzinfo=UTC))
    assert day.isoformat() == "2026-11-01T00:00:00+09:00"
    assert month == day


def _usage(runs=0, run_limit=100, day=0.0, day_limit=3.0, month=0.0, month_limit=30.0) -> UsageSummary:
    return UsageSummary(
        timezone="Asia/Tokyo",
        today=UsagePeriod(run_count=runs, run_limit=run_limit, cost_usd=day, cost_limit_usd=day_limit),
        month=UsagePeriod(run_count=runs, run_limit=None, cost_usd=month, cost_limit_usd=month_limit),
        unknown_cost_run_count=0,
    )


def test_exceeded_reason():
    assert exceeded_reason(_usage(runs=99, day=2.99, month=29.99)) is None
    assert "実行回数" in exceeded_reason(_usage(runs=100))
    assert "本日の推定利用額" in exceeded_reason(_usage(day=3.0))
    assert "今月の推定利用額" in exceeded_reason(_usage(month=30.0))
    # 上限 null は無制限
    assert (
        exceeded_reason(
            _usage(runs=10_000, run_limit=None, day=99, day_limit=None, month=999, month_limit=None)
        )
        is None
    )
