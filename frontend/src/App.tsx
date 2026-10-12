import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Composer, type ComposerValue } from '@/components/Composer'
import { ConversationSidebar } from '@/components/ConversationSidebar'
import { RunDetailSheet } from '@/components/RunDetailSheet'
import { StatsBar } from '@/components/StatsBar'
import { TurnView, UserBubble } from '@/components/TurnView'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { ApiError, api, type Run, type Turn } from '@/lib/api'
import { errorLabel, formatMs } from '@/lib/format'

function initialConversationId(): string | null {
  return new URLSearchParams(window.location.search).get('c')
}

/** 描画が画面に反映されるまで待つ（2回の rAF でペイント後を近似） */
function nextPaint(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
}

/** 会話で最後に作成した実行 */
function lastRun(turns: Turn[]): Run | null {
  const runs = turns.flatMap((t) => t.runs)
  return runs.length ? runs.reduce((a, b) => (a.created_at > b.created_at ? a : b)) : null
}

/**
 * 会話ごとの選択値（モデル・モード）。
 * 優先順: この会話で選んだもの > 会話の最終使用値 > 直前に選んだもの > 既定
 */
function useConversationChoice(
  conversationId: string | null,
  available: string[],
  lastUsed: string | null | undefined,
  fallback: string | null | undefined,
): [string | null, (value: string) => void] {
  const [choice, setChoice] = useState<{ conversationId: string; value: string } | null>(null)
  const key = conversationId ?? ''
  const valid = (v: string | null | undefined) => (v && available.includes(v) ? v : null)
  const value =
    valid(choice?.conversationId === key ? choice.value : null) ??
    valid(lastUsed) ??
    valid(choice?.value) ??
    fallback ??
    null
  return [value, (v) => setChoice({ conversationId: key, value: v })]
}

/**
 * 送信中は自分の実行が running で取得されうるため（新規会話の選択直後の取得など）、
 * 生成中表示と重ならないよう running の実行を除き、実行が残らないターンは隠す
 */
function hideRunning(turns: Turn[]): Turn[] {
  return turns
    .map((t) => ({ ...t, runs: t.runs.filter((r) => r.status !== 'running') }))
    .filter((t) => t.runs.length > 0)
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    const t = setInterval(() => setNow(performance.now()), 100)
    return () => clearInterval(t)
  }, [])
  return <span className="font-mono tabular-nums">{formatMs(now - since)}</span>
}

export default function App() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(initialConversationId)
  const [pending, setPending] = useState<{ prompt: string | null; startedAt: number } | null>(null)
  const [error, setError] = useState<{ title: string; message: string } | null>(null)
  const [creating, setCreating] = useState(false)
  const [detailRunId, setDetailRunId] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const models = useQuery({ queryKey: ['models'], queryFn: api.listModels, staleTime: Infinity })
  const modes = useQuery({ queryKey: ['modes'], queryFn: api.listModes })

  const conversation = useQuery({
    queryKey: ['conversation', selectedId],
    queryFn: () => api.getConversation(selectedId!),
    enabled: !!selectedId,
  })

  useEffect(() => {
    const url = new URL(window.location.href)
    if (selectedId) url.searchParams.set('c', selectedId)
    else url.searchParams.delete('c')
    window.history.replaceState(null, '', url)
  }, [selectedId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [conversation.data, pending])

  function select(id: string | null) {
    if (pending) return
    setError(null)
    setSelectedId(id)
  }

  function showError(e: unknown) {
    if (e instanceof ApiError) setError({ title: errorLabel(e.code), message: e.message })
    else setError({ title: 'エラー', message: String(e) })
  }

  async function createConversation(): Promise<string> {
    setCreating(true)
    try {
      const c = await api.createConversation()
      await queryClient.invalidateQueries({ queryKey: ['conversations'] })
      return c.id
    } finally {
      setCreating(false)
    }
  }

  /** 実行し、画面描画までの待ち時間を計測して履歴へ追記する */
  async function execute(prompt: string | null, call: (convId: string) => Promise<Run>): Promise<boolean> {
    const startedAt = performance.now()
    setError(null)
    setPending({ prompt, startedAt })
    let convId = selectedId
    let run: Run
    try {
      if (!convId) {
        convId = await createConversation()
        setSelectedId(convId)
      }
      run = await call(convId)
      await queryClient.refetchQueries({ queryKey: ['conversation', convId] })
    } catch (e) {
      setPending(null)
      showError(e)
      void queryClient.invalidateQueries({ queryKey: ['usage'] })
      return false
    }
    setPending(null)
    await nextPaint()
    const clientMs = performance.now() - startedAt
    if (run.persisted) {
      await api.putClientMetrics(run.request_id, clientMs).catch(() => undefined)
    }
    void queryClient.invalidateQueries({ queryKey: ['conversation', convId] })
    void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    void queryClient.invalidateQueries({ queryKey: ['usage'] })
    void queryClient.invalidateQueries({ queryKey: ['mode-stats'] })
    return true
  }

  const data = selectedId ? conversation.data : undefined
  const busy = pending !== null

  const latest = data ? lastRun(data.turns) : null
  const [model, setModel] = useConversationChoice(
    selectedId,
    models.data?.models ?? [],
    latest?.model,
    models.data?.default,
  )
  const modeList = modes.data?.modes ?? []
  const [mode, setMode] = useConversationChoice(
    selectedId,
    modeList.map((m) => m.id),
    latest?.mode,
    modes.data?.default,
  )
  const modeLabels = Object.fromEntries(modeList.map((m) => [m.id, m.label]))

  const send = (v: ComposerValue) => execute(v.prompt, (id) => api.sendMessage(id, { ...v, model, mode }))
  const retry = (turnId: string) => void execute(null, () => api.retryTurn(turnId, model, mode))

  function onHidden(id: string) {
    if (id === selectedId) select(null)
  }

  const turns = data ? (busy ? hideRunning(data.turns) : data.turns) : []

  return (
    <div className="flex h-dvh">
      <ConversationSidebar
        selectedId={selectedId}
        onSelect={select}
        onNew={() => select(null)}
        onHidden={onHidden}
        creating={creating || busy}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="border-b">
          <div className="flex items-center justify-between gap-3 px-4 py-2">
            <h1 className="truncate text-sm font-semibold">{data?.title ?? '新規チャット'}</h1>
            {data && data.models.length > 0 && (
              <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                <span className="text-muted-foreground text-xs">使用モデル</span>
                {data.models.map((m) => (
                  <Badge key={m} variant="outline" className="font-mono">
                    {m}
                  </Badge>
                ))}
              </div>
            )}
          </div>
          {data && <StatsBar stats={data.stats} />}
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-8 px-4 py-6">
            {!selectedId && !pending && (
              <p className="text-muted-foreground pt-20 text-center text-sm">
                質問を入力して新しい会話を始めます。
              </p>
            )}
            {selectedId && conversation.isError && (
              <Alert variant="destructive">
                <AlertTitle>会話を読み込めません</AlertTitle>
                <AlertDescription>{String(conversation.error)}</AlertDescription>
              </Alert>
            )}
            {turns.map((t) => (
              <TurnView
                key={t.id}
                turn={t}
                busy={busy}
                modeLabels={modeLabels}
                onRetry={retry}
                onShowDetail={setDetailRunId}
              />
            ))}
            {pending && (
              <div className="space-y-3">
                {pending.prompt && <UserBubble text={pending.prompt} />}
                <div className="text-muted-foreground flex items-center gap-2 text-sm">
                  <Loader2 className="size-4 animate-spin" />
                  回答を生成中… <Elapsed since={pending.startedAt} />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pb-4">
          {error && (
            <Alert variant="destructive">
              <AlertTitle>{error.title}</AlertTitle>
              <AlertDescription>{error.message}</AlertDescription>
            </Alert>
          )}
          <Composer
            disabled={busy}
            onSubmit={send}
            models={models.data?.models ?? []}
            model={model}
            onModelChange={setModel}
            modes={modeList.map((m) => ({ value: m.id, label: m.label, title: m.description }))}
            mode={mode}
            onModeChange={setMode}
          />
        </div>
      </main>
      <RunDetailSheet runId={detailRunId} onOpenChange={(o) => !o && setDetailRunId(null)} />
    </div>
  )
}
