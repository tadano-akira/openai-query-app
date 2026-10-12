import type { ConversationStats } from '@/lib/api'
import { formatMs, formatUsd } from '@/lib/format'

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground text-[11px]">{label}</div>
      <div className="truncate font-mono text-sm tabular-nums">{value}</div>
      {note && <div className="text-muted-foreground truncate text-[10px]">{note}</div>}
    </div>
  )
}

export function StatsBar({ stats }: { stats: ConversationStats }) {
  const tokenNote =
    stats.unknown_usage_call_count > 0
      ? `取得済み分の合計・不明 ${stats.unknown_usage_call_count}件`
      : `入力 ${stats.input_tokens.toLocaleString()} / 出力 ${stats.output_tokens.toLocaleString()}`
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-2 border-b px-4 py-2 sm:grid-cols-3 lg:grid-cols-7">
      <Stat
        label="実行回数"
        value={`${stats.run_count}`}
        note={`成功 ${stats.succeeded_count} / 失敗 ${stats.failed_count}`}
      />
      <Stat label="累計トークン" value={stats.total_tokens.toLocaleString()} note={tokenNote} />
      <Stat
        label="推定料金"
        value={formatUsd(stats.estimated_cost_usd)}
        note={stats.unknown_cost_run_count > 0 ? `単価不明 ${stats.unknown_cost_run_count}件を除く` : '単価からの目安'}
      />
      <Stat
        label="平均応答時間（画面）"
        value={formatMs(stats.avg_client_duration_ms)}
        note={`対象 ${stats.client_duration_sample_count}件`}
      />
      <Stat
        label="平均 API 所要時間"
        value={formatMs(stats.avg_openai_duration_ms)}
        note={`対象 ${stats.openai_duration_sample_count}件`}
      />
      <Stat label="処理時間累計" value={formatMs(stats.total_backend_duration_ms)} note="バックエンド" />
      <Stat label="会話の経過時間" value={formatMs(stats.elapsed_ms)} note="思考時間を含む" />
    </div>
  )
}
