"""推定料金による利用状況の集計と上限判定。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.doc_models import LlmCall
from app.models import Run, RunStatus
from app.schemas import UsagePeriod, UsageSummary


def period_starts(tz_name: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """指定タイムゾーンでの当日 0 時と当月 1 日 0 時（aware datetime）。"""
    tz = ZoneInfo(tz_name)
    local = (now or datetime.now(tz)).astimezone(tz)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day.replace(day=1)


def period_stmts(since: datetime):
    """期間内のチャット実行（回数・推定額・単価不明件数）と、資料Q&A の外部呼び出し（埋め込み等）の推定額。
    同期（ワーカー）・非同期（API）の両方から使う"""
    runs = select(
        func.count(),
        func.coalesce(func.sum(Run.estimated_cost_usd), 0),
        func.count().filter(Run.estimated_cost_usd.is_(None), Run.status != RunStatus.RUNNING),
    ).where(Run.created_at >= since)
    calls = select(
        func.coalesce(func.sum(LlmCall.estimated_cost_usd), 0),
        func.count().filter(LlmCall.estimated_cost_usd.is_(None)),
    ).where(LlmCall.created_at >= since)
    return runs, calls


async def _period(session: AsyncSession, since: datetime) -> tuple[int, float, int]:
    runs_stmt, calls_stmt = period_stmts(since)
    runs = (await session.execute(runs_stmt)).one()
    calls = (await session.execute(calls_stmt)).one()
    # 回数の上限はチャットの実行だけを数える。金額は埋め込み等も含める
    return int(runs[0]), float(runs[1]) + float(calls[0]), int(runs[2]) + int(calls[1])


def _limit(v: float) -> float | None:
    return v if v > 0 else None


async def usage_summary(session: AsyncSession, settings: Settings) -> UsageSummary:
    day, month = period_starts(settings.usage_timezone)
    d_runs, d_cost, _ = await _period(session, day)
    m_runs, m_cost, m_unknown = await _period(session, month)
    return UsageSummary(
        timezone=settings.usage_timezone,
        today=UsagePeriod(
            run_count=d_runs,
            run_limit=settings.daily_run_limit or None,
            cost_usd=d_cost,
            cost_limit_usd=_limit(settings.daily_cost_limit_usd),
        ),
        month=UsagePeriod(
            run_count=m_runs,
            run_limit=None,
            cost_usd=m_cost,
            cost_limit_usd=_limit(settings.monthly_cost_limit_usd),
        ),
        unknown_cost_run_count=m_unknown,
    )


def exceeded_reason(u: UsageSummary) -> str | None:
    """上限に達していれば理由を返す。実行前に判定するため、最後の 1 回で上限をわずかに超えることはある。"""
    t, m = u.today, u.month
    if t.run_limit is not None and t.run_count >= t.run_limit:
        return f"本日の実行回数が上限（{t.run_limit}回）に達しました"
    if t.cost_limit_usd is not None and t.cost_usd >= t.cost_limit_usd:
        return f"本日の推定利用額が上限（${t.cost_limit_usd:.2f}）に達しました（${t.cost_usd:.2f}）"
    if m.cost_limit_usd is not None and m.cost_usd >= m.cost_limit_usd:
        return f"今月の推定利用額が上限（${m.cost_limit_usd:.2f}）に達しました（${m.cost_usd:.2f}）"
    return None
