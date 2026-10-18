import { useQuery } from '@tanstack/react-query'
import { FileSearch, Loader2, Search } from 'lucide-react'
import { useState } from 'react'

import { Link, inputClass } from '@/components/docs/common'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import { docsApi, type SearchHit, type SearchRun } from '@/lib/docs'
import { errorLabel, formatMs } from '@/lib/format'
import { navigate } from '@/lib/router'

function HitCard({ hit }: { hit: SearchHit }) {
  const [open, setOpen] = useState(false)
  const lines = hit.text.split('\n')
  const preview = open ? lines : lines.slice(0, 8)
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="bg-primary text-primary-foreground rounded px-1.5 font-mono">#{hit.rank}</span>
        <Badge variant="outline">{hit.kind === 'table' ? '表' : '本文'}</Badge>
        <span className="font-medium">
          {hit.issuer_name}・{hit.document_title}
        </span>
        <span className="text-muted-foreground">
          PDF {hit.page_number}ページ目{hit.pdf_page_label && `（PDFラベル ${hit.pdf_page_label}）`}
        </span>
        <span className="text-muted-foreground ml-auto font-mono" title="字句検索・ベクトル検索それぞれの順位と、統合スコア（RRF）">
          字句 {hit.lexical_rank ?? '—'} / ベクトル {hit.vector_rank ?? '—'} / RRF {hit.rrf_score.toFixed(4)}
        </span>
      </div>
      {hit.heading && <div className="text-muted-foreground text-xs">見出し: {hit.heading}</div>}
      <pre className="bg-muted/40 overflow-x-auto rounded p-2 text-xs leading-5 whitespace-pre-wrap">{preview.join('\n')}</pre>
      <div className="flex flex-wrap gap-2">
        {lines.length > 8 && (
          <Button size="xs" variant="ghost" onClick={() => setOpen((o) => !o)}>
            {open ? '折りたたむ' : `すべて表示（${lines.length}行）`}
          </Button>
        )}
        <Button size="xs" variant="outline" onClick={() => navigate(`/viewer/chunk/${hit.chunk_id}`)}>
          原本で見る
        </Button>
        <Button
          size="xs"
          variant="ghost"
          onClick={() => navigate(`/docs/extractions/${hit.extraction_id}/review?page=${hit.page_number}`)}
        >
          <FileSearch /> レビュー画面で開く
        </Button>
      </div>
    </div>
  )
}

/** 段階 4: 承認済み資料の検索（引用つきの回答生成は段階 5 で追加する） */
export function SearchPage({ runId }: { runId: string | null }) {
  const issuers = useQuery({ queryKey: ['issuers'], queryFn: docsApi.issuers })
  const existing = useQuery({ queryKey: ['search-run', runId], queryFn: () => docsApi.searchRun(runId!), enabled: !!runId })
  const [question, setQuestion] = useState('')
  const [issuerIds, setIssuerIds] = useState<string[]>([])
  const [result, setResult] = useState<SearchRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ title: string; message: string } | null>(null)
  const run = result ?? existing.data ?? null

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!question.trim()) return
    setBusy(true)
    setError(null)
    try {
      const r = await docsApi.search(question, issuerIds)
      setResult(r)
      navigate(`/docs/search?run=${r.id}`, { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? { title: errorLabel(err.code), message: err.message } : { title: 'エラー', message: String(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-5xl space-y-4 p-6">
      <div>
        <h1 className="text-lg font-semibold">資料を検索</h1>
        <p className="text-muted-foreground text-sm">
          承認済み・既定・公式性確認済みの資料だけを対象に、字句検索とベクトル検索を組み合わせて根拠の候補を探します（上位12件）。
          回答の生成はまだ行いません。
        </p>
      </div>
      <form onSubmit={submit} className="space-y-2">
        <div className="flex gap-2">
          <input
            className={`${inputClass} h-9 flex-1`}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="例: トヨタの直近の有利子負債はいくらですか"
            maxLength={1000}
          />
          <Button type="submit" disabled={busy || !question.trim()}>
            {busy ? <Loader2 className="animate-spin" /> : <Search />} 検索
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="text-muted-foreground">企業で絞り込む:</span>
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
          {issuerIds.length === 0 && <span className="text-muted-foreground">（指定なし＝すべて）</span>}
        </div>
      </form>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>{error.title}</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}
      {run && (
        <div className="space-y-3">
          <div className="text-muted-foreground space-y-1 text-xs">
            <p>
              質問: <span className="text-foreground">{run.question}</span>
            </p>
            <p>
              対象: {run.scope.length}件（{run.scope.map((s) => `${s.issuer} ${s.title}`).join('、') || 'なし'}）
              {run.timings.total_ms != null && `・所要 ${formatMs(run.timings.total_ms)}`}
            </p>
            {run.expanded_terms.length > 0 && <p>同義語で追加した語: {run.expanded_terms.join('、')}</p>}
          </div>
          {run.status === 'no_scope' ? (
            <Alert>
              <AlertTitle>検索できる資料がありません</AlertTitle>
              <AlertDescription>
                資料の公式性を「確認済み」にし、抽出結果を承認して「既定にする」と、索引が作られて検索対象になります。
                <Link to="/docs">資料一覧へ</Link>
              </AlertDescription>
            </Alert>
          ) : run.hits.length === 0 ? (
            <p className="text-muted-foreground text-sm">該当する候補はありませんでした。</p>
          ) : (
            run.hits.map((h) => <HitCard key={h.chunk_id} hit={h} />)
          )}
        </div>
      )}
    </div>
  )
}
