import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { ModeStatsSheet } from '@/components/ModeStatsSheet'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { formatUsd } from '@/lib/format'
import { cn } from '@/lib/utils'

function Meter({ label, value, limit, text }: { label: string; value: number; limit: number | null; text: string }) {
  const ratio = limit ? Math.min(1, value / limit) : 0
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between gap-2 text-[11px]">
        <span className="text-muted-foreground">{label}</span>
        <span className={cn('font-mono tabular-nums', ratio >= 1 && 'text-destructive')}>{text}</span>
      </div>
      {limit != null && (
        <div className="bg-muted h-1 overflow-hidden rounded-full">
          <div
            className={cn('h-full rounded-full', ratio >= 1 ? 'bg-destructive' : ratio >= 0.8 ? 'bg-amber-500' : 'bg-primary')}
            style={{ width: `${ratio * 100}%` }}
          />
        </div>
      )}
    </div>
  )
}

const limitText = (v: string, limit: string | null) => (limit ? `${v} / ${limit}` : `${v}（上限なし）`)

/** 当日・当月の実行回数と推定利用額（上限つき） */
export function UsagePanel() {
  const { data } = useQuery({ queryKey: ['usage'], queryFn: api.getUsage })
  const [statsOpen, setStatsOpen] = useState(false)
  if (!data) return null
  const { today, month } = data
  return (
    <div className="space-y-1.5 border-t p-3" title="モデル別単価による推定。請求額とは一致しない場合があります">
      <div className="text-muted-foreground text-[11px] font-medium">利用状況（推定）</div>
      <Meter
        label="本日の回数"
        value={today.run_count}
        limit={today.run_limit}
        text={limitText(`${today.run_count}回`, today.run_limit != null ? `${today.run_limit}回` : null)}
      />
      <Meter
        label="本日の利用額"
        value={today.cost_usd}
        limit={today.cost_limit_usd}
        text={limitText(formatUsd(today.cost_usd), today.cost_limit_usd != null ? formatUsd(today.cost_limit_usd) : null)}
      />
      <Meter
        label="今月の利用額"
        value={month.cost_usd}
        limit={month.cost_limit_usd}
        text={limitText(formatUsd(month.cost_usd), month.cost_limit_usd != null ? formatUsd(month.cost_limit_usd) : null)}
      />
      <Button variant="ghost" size="xs" className="-ml-2" onClick={() => setStatsOpen(true)}>
        モード別の実績
      </Button>
      <ModeStatsSheet open={statsOpen} onOpenChange={setStatsOpen} />
      {data.unknown_cost_run_count > 0 && (
        <p className="text-muted-foreground text-[10px]">単価不明の実行 {data.unknown_cost_run_count}件は含みません</p>
      )}
    </div>
  )
}
