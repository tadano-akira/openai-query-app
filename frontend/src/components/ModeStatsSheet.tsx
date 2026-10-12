import { useQuery } from '@tanstack/react-query'

import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api } from '@/lib/api'
import { formatMs, formatUsd } from '@/lib/format'

const num = (v: number | null) => (v == null ? '—' : Math.round(v).toLocaleString('ja-JP'))

/** モード×モデル×検索有無ごとの実績（成功した実行の平均） */
export function ModeStatsSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const stats = useQuery({ queryKey: ['mode-stats'], queryFn: api.getModeStats, enabled: open })
  const modes = useQuery({ queryKey: ['modes'], queryFn: api.listModes })
  const label = (id: string) => modes.data?.modes.find((m) => m.id === id)?.label ?? id

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto data-[side=right]:sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>モード別の実績</SheetTitle>
          <SheetDescription>
            成功した実行の平均値。同じモデル・同じ検索設定の行どうしで比べてください（質問の内容でも大きく変わります）。
          </SheetDescription>
        </SheetHeader>
        <div className="px-4 pb-6">
          {stats.isLoading ? (
            <p className="text-muted-foreground text-sm">読み込み中…</p>
          ) : !stats.data?.length ? (
            <p className="text-muted-foreground text-sm">まだ実行がありません</p>
          ) : (
            <Table className="text-xs">
              <TableHeader>
                <TableRow>
                  <TableHead>モデル</TableHead>
                  <TableHead>検索</TableHead>
                  <TableHead>モード</TableHead>
                  <TableHead className="text-right">回数</TableHead>
                  <TableHead className="text-right">出力tok</TableHead>
                  <TableHead className="text-right">うち推論</TableHead>
                  <TableHead className="text-right">回答文字数</TableHead>
                  <TableHead className="text-right">推定料金</TableHead>
                  <TableHead className="text-right">API時間</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...stats.data]
                  .sort(
                    (a, b) =>
                      a.model.localeCompare(b.model) ||
                      Number(b.web_search_enabled) - Number(a.web_search_enabled) ||
                      a.mode.localeCompare(b.mode),
                  )
                  .map((s) => (
                    <TableRow key={`${s.model}-${s.web_search_enabled}-${s.mode}`}>
                      <TableCell className="font-mono">{s.model}</TableCell>
                      <TableCell>{s.web_search_enabled ? 'ON' : 'OFF'}</TableCell>
                      <TableCell>{label(s.mode)}</TableCell>
                      <TableCell className="text-right tabular-nums">{s.run_count}</TableCell>
                      <TableCell className="text-right tabular-nums">{num(s.avg_output_tokens)}</TableCell>
                      <TableCell className="text-right tabular-nums">{num(s.avg_reasoning_tokens)}</TableCell>
                      <TableCell className="text-right tabular-nums">{num(s.avg_answer_chars)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatUsd(s.avg_cost_usd)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatMs(s.avg_openai_duration_ms)}</TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
