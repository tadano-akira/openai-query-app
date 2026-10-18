import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, ExternalLink, Loader2, MessageSquareQuote } from 'lucide-react'
import { useState } from 'react'

import { Link, inputClass } from '@/components/docs/common'
import { Stat } from '@/components/StatsBar'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ApiError, api } from '@/lib/api'
import {
  docsApi,
  type AnswerItem,
  type AnswerRun,
  type AnswerStats,
  type AnswerStatus,
  type CalculationView,
  type MetricPolicyInfo,
} from '@/lib/docs'
import { errorLabel, formatDate, formatMs, formatTokens, formatUsd } from '@/lib/format'
import { usePersistentFlag } from '@/lib/prefs'
import { navigate } from '@/lib/router'
import { cn } from '@/lib/utils'

const STATUS: Record<AnswerStatus, { label: string; className: string }> = {
  running: { label: '処理中', className: 'bg-muted text-foreground' },
  completed: { label: '回答', className: 'bg-emerald-600 text-white' },
  needs_review: { label: '要確認', className: 'bg-amber-500 text-white' },
  insufficient_evidence: { label: '根拠不足', className: 'bg-slate-500 text-white' },
  needs_clarification: { label: '確認が必要', className: 'bg-sky-600 text-white' },
  conflict: { label: '資料間で不一致', className: 'bg-orange-600 text-white' },
  failed: { label: '失敗', className: 'bg-destructive text-white' },
  cancelled: { label: '取消', className: 'bg-muted text-foreground' },
}

const KIND_LABELS: Record<string, string> = {
  direct: '直接記載',
  calculated: '資料から計算',
  calculation_unverified: '要確認（計算できない値を含む）',
}

export function StatusBadge({ status }: { status: AnswerStatus }) {
  const s = STATUS[status] ?? { label: status, className: 'bg-muted' }
  return <span className={cn('rounded px-1.5 py-0.5 text-xs font-medium', s.className)}>{s.label}</span>
}

function ItemCard({ item }: { item: AnswerItem }) {
  return (
    <div className="space-y-1.5 rounded-lg border p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="bg-primary text-primary-foreground rounded px-1.5 font-mono text-xs">[{item.n}]</span>
        {item.kind === 'value' ? (
          <>
            <span className="text-lg font-semibold tabular-nums">{item.value}</span>
            {item.unit && <span className="text-muted-foreground text-sm">{item.unit}</span>}
          </>
        ) : (
          <span className="text-sm">
            {item.context_before && <span className="text-muted-foreground">{item.context_before} </span>}
            <mark className="bg-yellow-200/70 px-0.5 dark:bg-yellow-500/30">{item.value}</mark>
            {item.context_after && <span className="text-muted-foreground"> {item.context_after}</span>}
          </span>
        )}
        {item.fact && (
          <span
            className={cn(
              'rounded px-1 text-[10px]',
              item.fact.status === 'verified' ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200' : 'bg-amber-100 text-amber-800',
            )}
            title={item.fact.issues.join('、') || '承認済み抽出版の値で、数値・単位・期を根拠から特定できました'}
          >
            {item.fact.status === 'verified' ? '数値検証済み' : '未検証'}
          </span>
        )}
      </div>
      {item.kind === 'value' && (
        <div className="text-sm">
          {item.row && <span>{item.row}</span>}
          {item.column && <span className="text-muted-foreground">｜{item.column}</span>}
        </div>
      )}
      {item.note && <div className="text-muted-foreground text-xs">説明（AI）: {item.note}</div>}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground">
          {[item.issuer, item.document_title, item.period_label].filter(Boolean).join('・')}
          {item.title && ` ／ ${item.title}`} ／ PDF {item.page_number}ページ目
        </span>
        <Button size="xs" variant="outline" className="ml-auto" onClick={() => navigate(item.viewer_url)}>
          エビデンスを確認する（原本参照）
        </Button>
      </div>
    </div>
  )
}

function PolicyNote({ policy }: { policy: MetricPolicyInfo }) {
  return (
    <div className="bg-muted/40 space-y-0.5 rounded p-2 text-xs">
      <p>
        <span className="font-medium">「{policy.name}」の定義</span>（{policy.basis_label}・版 {policy.policy_version}
        {policy.approved_on && `・${policy.approved_on} 承認`}）
      </p>
      <p>{policy.description}</p>
      {policy.items.length > 0 && <p className="text-muted-foreground">算入項目: {policy.items.join('、')}</p>}
      {policy.basis === 'user_defined' && (
        <p className="text-muted-foreground">これは利用者が決めた計算条件であり、資料に記載された定義ではありません。</p>
      )}
    </div>
  )
}

function CalculationPanel({ c }: { c: CalculationView }) {
  // 段階 5 の回答（計算未対応の時点の記録）には入力の一覧がない
  if (!Array.isArray(c.inputs))
    return <p className="text-muted-foreground text-xs">この回答は計算機能の追加前に作られたため、計算結果はありません。</p>
  return (
    <div className={cn('space-y-2 rounded-lg border-2 p-3', c.status === 'computed' ? 'border-emerald-500/60' : 'border-amber-400')}>
      {c.status === 'computed' ? (
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-muted-foreground text-xs">計算結果</span>
          <span className="text-2xl font-semibold tabular-nums">{c.result_display}</span>
          <span className="text-muted-foreground text-sm">{c.unit}</span>
          <span className="text-muted-foreground font-mono text-xs">= {c.expression}</span>
        </div>
      ) : (
        <p className="text-sm font-medium">検証できない値を含むため、計算結果は表示しません（{c.expression}）</p>
      )}
      <table className="w-full text-xs">
        <thead className="text-muted-foreground">
          <tr className="text-left">
            <th className="font-normal">入力</th>
            <th className="font-normal">項目</th>
            <th className="font-normal">期</th>
            <th className="text-right font-normal">原文の値</th>
            <th className="text-right font-normal">基準単位の値</th>
          </tr>
        </thead>
        <tbody>
          {c.inputs.map((i) => (
            <tr key={i.n} className="border-t">
              <td className="font-mono">[{i.n}]</td>
              <td>{i.label}</td>
              <td className="text-muted-foreground">{i.period_label}</td>
              <td className="text-right tabular-nums">
                {i.raw_value} {i.unit}
              </td>
              <td className="text-right tabular-nums">
                {i.normalized_value ?? '—'}
                {i.status !== 'verified' && <span className="ml-1 text-amber-600">未検証</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {c.status === 'computed' && (
        <p className="text-muted-foreground text-[11px]">
          Decimal で計算（基準単位の結果 {c.result_base}
          {c.currency && ` ${c.currency}`}）。{c.rounding?.applied ? `小数${c.rounding.places}桁に四捨五入（ROUND_HALF_UP）。` : '丸めなし。'}
          {c.notes?.join(' ')}
        </p>
      )}
      {c.policy && <PolicyNote policy={c.policy} />}
    </div>
  )
}

/** 回答ごとの計測値（チャットの各回答の下の行と同じ形式） */
function UsageLine({ run }: { run: AnswerRun }) {
  const u = run.usage
  return (
    <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-2 text-xs">
      <span>応答 {formatMs(run.timings.total_ms)}</span>
      <span title="回答の LLM 呼び出しの所要時間（作り直しを含む）">API {formatMs(u.api_ms)}</span>
      <span title={`埋め込み（検索）${u.embedding_tokens.toLocaleString()} を含む・呼び出し ${u.llm_call_count}回`}>
        入力 {formatTokens(u.input_tokens)} / 出力 {formatTokens(u.output_tokens)}
        {u.unknown_usage_call_count > 0 && `（不明 ${u.unknown_usage_call_count}件）`}
      </span>
      <span title="モデル別単価による推定料金（埋め込みを含む）">推定 {formatUsd(run.estimated_cost_usd)}</span>
      <span>{run.model}</span>
      <span>{formatDate(run.created_at)}</span>
    </div>
  )
}

const STATUS_ORDER: AnswerStatus[] = ['completed', 'needs_review', 'insufficient_evidence', 'needs_clarification', 'failed']

/** 資料Q&A 全体の計測値（チャットの上部の計測値と同じ形式） */
function AnswerStatsBar({ stats }: { stats: AnswerStats }) {
  const tokenNote =
    stats.unknown_usage_call_count > 0
      ? `取得済み分の合計・不明 ${stats.unknown_usage_call_count}件`
      : `入力 ${stats.input_tokens.toLocaleString()} / 出力 ${stats.output_tokens.toLocaleString()}`
  return (
    <div id="answer-stats-bar" className="grid grid-cols-2 gap-x-6 gap-y-2 border-t px-4 py-2 sm:grid-cols-3 lg:grid-cols-6">
      <Stat label="実行回数" value={`${stats.run_count}`} note={`成功 ${stats.succeeded_count} / 失敗 ${stats.failed_count}`} />
      <Stat label="累計トークン" value={stats.total_tokens.toLocaleString()} note={tokenNote} />
      <Stat
        label="推定料金"
        value={formatUsd(stats.estimated_cost_usd)}
        note={stats.unknown_cost_call_count > 0 ? `単価不明 ${stats.unknown_cost_call_count}件を除く` : '単価からの目安'}
      />
      <Stat label="平均応答時間" value={formatMs(stats.avg_duration_ms)} note={`対象 ${stats.duration_sample_count}件`} />
      <Stat label="平均 API 所要時間" value={formatMs(stats.avg_api_ms)} note={`対象 ${stats.api_sample_count}件`} />
      <Stat
        label="回答の状態"
        value={`${stats.status_counts.completed ?? 0} 回答`}
        note={STATUS_ORDER.slice(1)
          .filter((s) => stats.status_counts[s])
          .map((s) => `${STATUS[s].label} ${stats.status_counts[s]}`)
          .join(' / ')}
      />
    </div>
  )
}

function AnswerView({ run }: { run: AnswerRun }) {
  const r = run.result
  const [showDetail, setShowDetail] = useState(false)
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={run.status} />
        {run.answer_kind && KIND_LABELS[run.answer_kind] && (
          <Badge variant="outline" className="text-xs">
            {KIND_LABELS[run.answer_kind]}
          </Badge>
        )}
        <span className="font-medium">{run.question}</span>
      </div>
      {r && r.scope.length > 0 && (
        <div className="text-muted-foreground text-xs">対象: {r.scope.map((s) => `${s.issuer} ${s.title}`).join('、')}</div>
      )}

      {run.status === 'failed' && (
        <Alert variant="destructive">
          <AlertTitle>{run.error_code ? errorLabel(run.error_code) : '失敗'}</AlertTitle>
          <AlertDescription>{run.error_message}</AlertDescription>
        </Alert>
      )}
      {run.status === 'insufficient_evidence' && (
        <Alert>
          <AlertTitle>登録済みの資料からは答えられません</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {(r?.missing.length ? r.missing : ['質問に答える根拠が見つかりませんでした']).map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      {run.status === 'needs_clarification' && r?.clarification && (
        <Alert>
          <AlertTitle>質問の対象を確認させてください</AlertTitle>
          <AlertDescription>
            <p>{r.clarification}</p>
            {r.policy && !r.calculation && (
              <p className="text-muted-foreground mt-1 text-xs">
                指標の定義は backend/metrics/ のファイルで企業ごとに設定します。
              </p>
            )}
          </AlertDescription>
        </Alert>
      )}
      {r?.review_reasons && r.review_reasons.length > 0 && (
        <Alert className="border-amber-400">
          <AlertTitle>確認が必要な値があります</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {r.review_reasons.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      {r?.calculation && <CalculationPanel c={r.calculation} />}
      {r && r.items.length > 0 && (
        <div className="space-y-2">
          {r.items.map((it) => (
            <ItemCard key={it.n} item={it} />
          ))}
          <p className="text-muted-foreground text-[11px]">
            値・ページ・見出しは承認済み抽出版の原文をそのまま表示しています（AIが書いた数値ではありません）。「最新」は登録した資料の中での最新です。
          </p>
        </div>
      )}

      <UsageLine run={run} />
      <div>
        <Button size="xs" variant="ghost" onClick={() => setShowDetail((v) => !v)}>
          {showDetail ? '処理の詳細を閉じる' : '処理の詳細'}
        </Button>
        {run.langfuse_url && (
          <a href={run.langfuse_url} target="_blank" rel="noreferrer" className="text-muted-foreground ml-2 text-xs underline">
            Langfuse <ExternalLink className="inline size-3" />
          </a>
        )}
        {run.search_run_id && (
          <Link to={`/docs/search?run=${run.search_run_id}`} className="ml-3 text-xs">
            検索結果を見る
          </Link>
        )}
      </div>
      {showDetail && (
        <div className="bg-muted/40 space-y-2 rounded p-3 text-xs">
          <p>
            段階: {run.stage}・プロンプト版: {run.prompt_version}・候補: {run.candidates.length}件・LLM試行:{' '}
            {run.llm_outputs.length}回
          </p>
          {run.validation_errors.length > 0 && (
            <div>
              <p className="font-medium">検証で不採用にした出力</p>
              {run.validation_errors.map((v) => (
                <ul key={v.attempt} className="list-disc pl-4">
                  {v.errors.map((e) => (
                    <li key={e}>
                      試行{v.attempt}: {e}
                    </li>
                  ))}
                </ul>
              ))}
            </div>
          )}
          <pre className="overflow-x-auto whitespace-pre-wrap">{JSON.stringify(run.timings, null, 1)}</pre>
        </div>
      )}
    </div>
  )
}

/** 段階 5: 資料Q&A。根拠の ID を AI に選ばせ、検証に合格した根拠だけで回答を組み立てる */
export function AnswerPage({ runId }: { runId: string | null }) {
  const qc = useQueryClient()
  const issuers = useQuery({ queryKey: ['issuers'], queryFn: docsApi.issuers })
  const models = useQuery({ queryKey: ['models'], queryFn: api.listModels, staleTime: Infinity })
  const recent = useQuery({ queryKey: ['answer-runs'], queryFn: docsApi.answerRuns })
  const stats = useQuery({ queryKey: ['answer-stats'], queryFn: docsApi.answerStats })
  const [statsOpen, setStatsOpen] = usePersistentFlag('oqa.statsOpen')
  const existing = useQuery({ queryKey: ['answer-run', runId], queryFn: () => docsApi.answerRun(runId!), enabled: !!runId })
  const [question, setQuestion] = useState('')
  const [issuerIds, setIssuerIds] = useState<string[]>([])
  const [model, setModel] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ title: string; message: string } | null>(null)
  const run = runId ? existing.data : null

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!question.trim()) return
    setBusy(true)
    setError(null)
    try {
      const r = await docsApi.answer(question, issuerIds, model)
      qc.setQueryData(['answer-run', r.id], r)
      qc.invalidateQueries({ queryKey: ['answer-runs'] })
      qc.invalidateQueries({ queryKey: ['answer-stats'] })
      qc.invalidateQueries({ queryKey: ['usage'] })
      navigate(`/docs/answer?run=${r.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? { title: errorLabel(err.code), message: err.message } : { title: 'エラー', message: String(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <header className="bg-header border-b">
        <div className="flex items-center justify-between gap-3 px-4 py-2">
          <h1 className="text-sm font-semibold">資料に質問する</h1>
          <Button
            variant="ghost"
            size="xs"
            className="text-muted-foreground"
            onClick={() => setStatsOpen((o) => !o)}
            aria-expanded={statsOpen}
            aria-controls="answer-stats-bar"
            title={statsOpen ? '計測値を隠す' : '計測値を表示'}
          >
            計測値 {statsOpen ? <ChevronUp /> : <ChevronDown />}
          </Button>
        </div>
        {stats.data && statsOpen && <AnswerStatsBar stats={stats.data} />}
      </header>
      <div className="flex max-w-6xl gap-6 p-6">
        <div className="min-w-0 flex-1 space-y-4">
          <p className="text-muted-foreground text-sm">
            承認済みの資料から根拠を探し、AIが根拠を選びます。値・ページは原文から表示し、根拠のない回答はしません。
          </p>
          <form onSubmit={submit} className="space-y-2">
            <div className="flex gap-2">
              <input
                className={`${inputClass} h-9 flex-1`}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="例: トヨタの直近の有利子負債はいくら？"
                maxLength={1000}
              />
              <Button type="submit" disabled={busy || !question.trim()}>
                {busy ? <Loader2 className="animate-spin" /> : <MessageSquareQuote />} 質問
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs">
              <span className="text-muted-foreground">企業:</span>
              {(issuers.data ?? []).map((i) => (
                <label key={i.id} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={issuerIds.includes(i.id)}
                    onChange={(e) => setIssuerIds((ids) => (e.target.checked ? [...ids, i.id] : ids.filter((x) => x !== i.id)))}
                  />
                  {i.name}
                </label>
              ))}
              {issuerIds.length === 0 && <span className="text-muted-foreground">（指定なし＝質問の企業名から判断）</span>}
              <span className="text-muted-foreground ml-auto">モデル:</span>
              <select
                className={`${inputClass} h-7 w-auto py-0`}
                value={model ?? models.data?.default ?? ''}
                onChange={(e) => setModel(e.target.value)}
              >
                {(models.data?.models ?? []).map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
          </form>
          {busy && (
            <p className="text-muted-foreground flex items-center gap-2 text-sm">
              <Loader2 className="size-4 animate-spin" /> 根拠を検索・選択しています（10秒ほどかかります）
            </p>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertTitle>{error.title}</AlertTitle>
              <AlertDescription>{error.message}</AlertDescription>
            </Alert>
          )}
          {runId && existing.isLoading && <Loader2 className="animate-spin" />}
          {run && <AnswerView run={run} />}
        </div>
        <aside className="hidden w-64 shrink-0 space-y-2 lg:block">
          <h2 className="text-muted-foreground text-xs font-medium">最近の質問</h2>
          {(recent.data ?? []).map((r) => (
            <button
              key={r.id}
              onClick={() => navigate(`/docs/answer?run=${r.id}`)}
              className={cn(
                'hover:bg-muted block w-full space-y-1 rounded border p-2 text-left text-xs',
                r.id === runId && 'border-primary',
              )}
            >
              <div className="line-clamp-2">{r.question}</div>
              <div className="flex items-center gap-1">
                <StatusBadge status={r.status} />
                <Badge variant="outline" className="text-[10px]">
                  {r.model}
                </Badge>
              </div>
            </button>
          ))}
        </aside>
      </div>
    </div>
  )
}
