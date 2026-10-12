import { ChevronLeft, ChevronRight, Gauge, Globe, RotateCcw } from 'lucide-react'
import { useState } from 'react'

import { Markdown } from '@/components/Markdown'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { Run, Turn } from '@/lib/api'
import { errorLabel, formatDate, formatMs, formatTokens, outcomeLabel } from '@/lib/format'

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="bg-muted max-w-[85%] rounded-2xl px-4 py-2 text-sm whitespace-pre-wrap">{text}</div>
    </div>
  )
}

function uniqueSources(run: Run): { url: string; title: string | null }[] {
  const seen = new Map<string, string | null>()
  for (const c of run.citations) if (!seen.has(c.url)) seen.set(c.url, c.title)
  return [...seen].map(([url, title]) => ({ url, title }))
}

function SearchBadge({ run }: { run: Run }) {
  if (!run.web_search_enabled) return <Badge variant="outline">検索OFF</Badge>
  if (run.status !== 'succeeded') return null
  const scope = run.searched_domain ?? '制限なし'
  return run.search_info?.executed ? (
    <Badge variant="secondary">
      <Globe /> 検索済み: {scope}
    </Badge>
  ) : (
    <Badge variant="destructive">検索未実行（対象: {scope}）</Badge>
  )
}

function RunBody({ run }: { run: Run }) {
  if (run.status === 'succeeded') {
    const sources = uniqueSources(run)
    const note = outcomeLabel(run.outcome)
    return (
      <div className="space-y-3">
        {note && run.outcome !== 'search_not_executed' && (
          <Alert>
            <AlertTitle>{note}</AlertTitle>
          </Alert>
        )}
        <Markdown>{run.answer ?? ''}</Markdown>
        {sources.length > 0 && (
          <div className="space-y-1">
            <div className="text-muted-foreground text-xs font-medium">参照元</div>
            <ol className="list-decimal space-y-0.5 pl-5 text-xs">
              {sources.map((s) => (
                <li key={s.url}>
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer nofollow"
                    className="text-primary break-all underline underline-offset-2"
                  >
                    {s.title || s.url}
                  </a>
                </li>
              ))}
            </ol>
          </div>
        )}
        {!run.persisted && (
          <Alert variant="destructive">
            <AlertTitle>履歴未保存</AlertTitle>
            <AlertDescription>DBへの保存に失敗したため、この回答は履歴に残りません。</AlertDescription>
          </Alert>
        )}
      </div>
    )
  }
  if (run.status === 'running') {
    return <p className="text-muted-foreground text-sm">処理中（別タブまたは中断の可能性）</p>
  }
  return (
    <Alert variant="destructive">
      <AlertTitle>{run.error ? errorLabel(run.error.code) : 'エラー'}</AlertTitle>
      <AlertDescription>{run.error?.message ?? '不明なエラーが発生しました'}</AlertDescription>
    </Alert>
  )
}

export function TurnView({
  turn,
  busy,
  onRetry,
  onShowDetail,
}: {
  turn: Turn
  busy: boolean
  onRetry: (turnId: string) => void
  onShowDetail: (runId: string) => void
}) {
  const [index, setIndex] = useState<number | null>(null)
  const runs = turn.runs
  const current = index ?? runs.length - 1
  const run = runs[current]

  return (
    <div className="space-y-3">
      {turn.user_message && <UserBubble text={turn.user_message.content} />}
      {run && (
        <div className="space-y-2">
          <RunBody run={run} />
          <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <SearchBadge run={run} />
            <span>応答 {formatMs(run.timings.client_duration_ms ?? run.timings.backend_duration_ms)}</span>
            <span>API {formatMs(run.timings.openai_duration_ms)}</span>
            <span>
              入力 {formatTokens(run.usage.input_tokens)} / 出力 {formatTokens(run.usage.output_tokens)}
            </span>
            <span>{run.model}</span>
            <span>{formatDate(run.generated_at ?? run.created_at)}</span>
            <Button variant="ghost" size="xs" onClick={() => onShowDetail(run.request_id)}>
              <Gauge /> 計測詳細
            </Button>
            {turn.is_latest && (
              <Button
                variant="ghost"
                size="xs"
                disabled={busy}
                onClick={() => {
                  setIndex(null)
                  onRetry(turn.id)
                }}
              >
                <RotateCcw /> 再実行
              </Button>
            )}
            {runs.length > 1 && (
              <span className="flex items-center">
                <Button
                  variant="ghost"
                  size="icon-xs"
                  disabled={current === 0}
                  onClick={() => setIndex(current - 1)}
                  aria-label="前の試行"
                >
                  <ChevronLeft />
                </Button>
                試行 {current + 1}/{runs.length}
                <Button
                  variant="ghost"
                  size="icon-xs"
                  disabled={current === runs.length - 1}
                  onClick={() => setIndex(current + 1)}
                  aria-label="次の試行"
                >
                  <ChevronRight />
                </Button>
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
