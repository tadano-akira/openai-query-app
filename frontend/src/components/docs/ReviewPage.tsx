import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Link2, RotateCw, ZoomIn, ZoomOut } from 'lucide-react'
import { useMemo, useState } from 'react'

import { ExtractionBadge, ApprovalBadge, Link, inputClass } from '@/components/docs/common'
import { HtmlPageView } from '@/components/docs/HtmlPageView'
import { PdfPageView, type Highlight } from '@/components/docs/PdfPageView'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import { QUALITY_LABELS, docsApi, pdfUrl, type PageDetail } from '@/lib/docs'
import { navigate } from '@/lib/router'
import { cn } from '@/lib/utils'

const QUALITY_TONE: Record<string, string> = {
  ok: 'bg-emerald-500',
  needs_review: 'bg-amber-500',
  non_text: 'bg-red-500',
  blank: 'bg-muted-foreground/40',
}

/** 選択した要素と、セルなら見出しセル（項目・期）を返す */
function selection(page: PageDetail | undefined, selectedId: string | null) {
  if (!page || !selectedId) return { value: [] as string[], context: [] as string[] }
  for (const t of page.tables) {
    const cell = t.cells.find((c) => c.id === selectedId)
    if (cell) return { value: [cell.id], context: cell.header_ids }
  }
  return { value: [selectedId], context: [] }
}

export function ReviewPage({ extractionId, pageNumber }: { extractionId: string; pageNumber: number }) {
  const queryClient = useQueryClient()
  const review = useQuery({ queryKey: ['review', extractionId], queryFn: () => docsApi.review(extractionId) })
  const page = useQuery({
    queryKey: ['page', extractionId, pageNumber],
    queryFn: () => docsApi.page(extractionId, pageNumber),
  })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [scale, setScale] = useState(1.2)
  const [rotation, setRotation] = useState(0)
  const [memo, setMemo] = useState('')
  const [error, setError] = useState<string | null>(null)

  const p = page.data
  const sel = selection(p, selectedId)
  const highlights = useMemo<Highlight[]>(() => {
    if (!p) return []
    const all: Highlight[] = showAll
      ? [
          ...p.lines.map((l) => ({ id: l.id, quads: l.quads, tone: 'context' as const })),
          ...p.tables.flatMap((t) => t.cells.map((c) => ({ id: c.id, quads: c.quads, tone: 'context' as const }))),
        ]
      : []
    const byId = new Map([...p.lines, ...p.tables.flatMap((t) => t.cells)].map((e) => [e.id, e]))
    const chosen: Highlight[] = [
      ...sel.context.flatMap((id) => (byId.get(id) ? [{ id, quads: byId.get(id)!.quads, tone: 'context' as const }] : [])),
      ...sel.value.flatMap((id) => (byId.get(id) ? [{ id, quads: byId.get(id)!.quads, tone: 'select' as const }] : [])),
    ]
    return [...all.filter((h) => !chosen.some((c) => c.id === h.id)), ...chosen]
    // sel は selectedId と p から決まる
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p, selectedId, showAll])

  const r = review.data
  const ext = r?.extraction
  const pages = r?.pages ?? []
  const go = (n: number) => {
    setSelectedId(null)
    navigate(`/docs/extractions/${extractionId}/review?page=${n}`, { replace: true })
  }

  async function approve() {
    setError(null)
    try {
      await docsApi.approve(extractionId, memo || null)
      await queryClient.invalidateQueries({ queryKey: ['review', extractionId] })
      void queryClient.invalidateQueries({ queryKey: ['version'] })
      void queryClient.invalidateQueries({ queryKey: ['docs'] })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b px-4 py-2">
        <div className="min-w-0">
          <p className="text-muted-foreground text-xs">
            <Link to="/docs">資料</Link>
            {r && (
              <>
                {' / '}
                <Link to={`/docs/versions/${r.version_id}`}>版の詳細</Link>
              </>
            )}
            {' / '}抽出結果のレビュー
          </p>
          <div className="flex flex-wrap items-center gap-1">
            <ExtractionBadge e={ext} />
            <ApprovalBadge e={ext} />
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {ext && !ext.approved_at && (
            <>
              <input className={`${inputClass} w-64`} placeholder="承認メモ（確認した範囲など）" value={memo} onChange={(e) => setMemo(e.target.value)} />
              <Button size="sm" onClick={() => void approve()}>
                抽出結果を承認
              </Button>
            </>
          )}
        </div>
      </div>
      {error && (
        <Alert variant="destructive" className="m-2">
          <AlertTitle>承認できません</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="flex min-h-0 flex-1">
        {/* ページ一覧と品質 */}
        <nav className="w-36 shrink-0 overflow-y-auto border-r p-2 text-xs">
          {pages.map((pg) => (
            <button
              key={pg.page_number}
              onClick={() => go(pg.page_number)}
              className={cn(
                'hover:bg-muted flex w-full items-center gap-2 rounded px-2 py-1 text-left',
                pg.page_number === pageNumber && 'bg-muted font-medium',
              )}
              title={`${QUALITY_LABELS[pg.quality]}・${pg.char_count}文字・表${pg.table_count}`}
            >
              <span className={cn('size-2 shrink-0 rounded-full', QUALITY_TONE[pg.quality])} />
              <span>{pg.page_number}</span>
              {pg.pdf_page_label && <span className="text-muted-foreground">({pg.pdf_page_label})</span>}
              {pg.table_count > 0 && <span className="text-muted-foreground ml-auto">表{pg.table_count}</span>}
            </button>
          ))}
        </nav>
        {/* 原本 */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex flex-wrap items-center gap-1 border-b px-2 py-1 text-xs">
            <Button size="icon-xs" variant="ghost" disabled={pageNumber <= 1} onClick={() => go(pageNumber - 1)} aria-label="前のページ">
              <ChevronLeft />
            </Button>
            <span className="tabular-nums">
              PDF {pageNumber} / {pages.length || '—'} ページ目
              {p?.pdf_page_label && `（PDFラベル ${p.pdf_page_label}）`}
            </span>
            <Button size="icon-xs" variant="ghost" disabled={pageNumber >= pages.length} onClick={() => go(pageNumber + 1)} aria-label="次のページ">
              <ChevronRight />
            </Button>
            <Button size="icon-xs" variant="ghost" onClick={() => setScale((s) => Math.max(0.5, s - 0.2))} aria-label="縮小">
              <ZoomOut />
            </Button>
            <span className="tabular-nums">{Math.round(scale * 100)}%</span>
            <Button size="icon-xs" variant="ghost" onClick={() => setScale((s) => Math.min(3, s + 0.2))} aria-label="拡大">
              <ZoomIn />
            </Button>
            <Button size="icon-xs" variant="ghost" onClick={() => setRotation((x) => (x + 90) % 360)} aria-label="回転">
              <RotateCw />
            </Button>
            <label className="ml-2 flex items-center gap-1">
              <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
              すべての抽出範囲を表示
            </label>
            {p && <span className="text-muted-foreground ml-auto">品質: {QUALITY_LABELS[p.quality]}</span>}
          </div>
          {r && (
            <PdfPageView
              url={pdfUrl(r.version_id)}
              pageNumber={pageNumber}
              highlights={highlights}
              scale={scale}
              extraRotation={rotation}
              focusId={selectedId}
              onSelect={setSelectedId}
              className="min-h-0 flex-1"
            />
          )}
        </div>
        {/* 抽出結果 */}
        <div className="flex w-[30rem] shrink-0 flex-col border-l">
          <div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs">
            <span className="font-medium">抽出結果</span>
            {selectedId && (
              <Link to={`/viewer/evidence/${selectedId}`} className="ml-auto inline-flex items-center gap-1">
                <Link2 className="size-3" /> 選択した根拠の固定リンク
              </Link>
            )}
          </div>
          {p ? (
            <HtmlPageView
              page={p}
              values={sel.value}
              contexts={sel.context}
              focusId={selectedId}
              onSelect={setSelectedId}
              className="min-h-0 flex-1"
            />
          ) : (
            <p className="text-muted-foreground p-4 text-sm">読み込み中…</p>
          )}
          <p className="text-muted-foreground border-t px-3 py-2 text-[11px]">
            行番号はシステムが付けた抽出行番号です（原本の公式な行番号ではありません）。表の見出し（項目・期）は自動推定のため、セルを選んで原本と一致するか確認してください。
          </p>
        </div>
      </div>
    </div>
  )
}
