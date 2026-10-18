import { useQuery } from '@tanstack/react-query'
import { ZoomIn, ZoomOut } from 'lucide-react'
import { useState } from 'react'

import { Link } from '@/components/docs/common'
import { HtmlPageView } from '@/components/docs/HtmlPageView'
import { PdfPageView, type Highlight } from '@/components/docs/PdfPageView'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { docsApi, pdfUrl } from '@/lib/docs'
import { navigate } from '@/lib/router'
import { cn } from '@/lib/utils'

/**
 * 根拠 ID から原本の該当ページを開き、値（黄色）と項目・期など（薄い青）を強調する。
 * 根拠は固定された原本版・抽出版を指すため、資料が差し替え・再解析されても同じ位置を表示する。
 */
export function EvidenceViewer({ elementId, mode }: { elementId: string; mode: 'pdf' | 'html' }) {
  const ev = useQuery({ queryKey: ['evidence', elementId], queryFn: () => docsApi.evidence(elementId) })
  const e = ev.data
  const page = useQuery({
    queryKey: ['page', e?.extraction_id, e?.page_number],
    queryFn: () => docsApi.page(e!.extraction_id, e!.page_number),
    enabled: !!e,
  })
  const [scale, setScale] = useState(1.3)

  if (ev.isError) return <p className="text-destructive p-6 text-sm">根拠が見つかりません</p>
  if (!e) return <p className="text-muted-foreground p-6 text-sm">読み込み中…</p>

  const highlights: Highlight[] = [
    ...e.related.map((r) => ({ id: r.id, quads: r.quads, tone: 'context' as const })),
    { id: e.element_id, quads: e.quads, tone: 'value' as const },
  ]
  const setMode = (m: 'pdf' | 'html') => navigate(`/viewer/evidence/${elementId}${m === 'html' ? '?mode=html' : ''}`, { replace: true })
  const location =
    e.kind === 'cell'
      ? `表のセル（${(e.row ?? 0) + 1}行目・${(e.col ?? 0) + 1}列目）`
      : e.line_seq != null
        ? `抽出行 ${e.line_seq}`
        : e.kind

  return (
    <div className="flex h-full min-h-0">
      <aside className="w-80 shrink-0 space-y-4 overflow-y-auto border-r p-4 text-sm">
        <p className="text-muted-foreground text-xs">
          <Link to="/docs">資料</Link> / <Link to={`/docs/versions/${e.version_id}`}>{e.document_title}</Link>
        </p>
        {!e.extraction_approved && (
          <Alert>
            <AlertTitle>未承認の抽出版です</AlertTitle>
            <AlertDescription>この根拠は、まだ承認されていない抽出結果に基づいています。</AlertDescription>
          </Alert>
        )}
        <dl className="space-y-2">
          <div>
            <dt className="text-muted-foreground text-xs">資料</dt>
            <dd>
              {e.issuer_name}・{e.document_title}
              {e.period_label && `（${e.period_label}）`}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">位置</dt>
            <dd>
              PDF {e.page_number}ページ目
              {e.printed_page_label ? `（紙面${e.printed_page_label}）` : e.pdf_page_label ? `（PDFラベル ${e.pdf_page_label}）` : ''}／{location}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">原文</dt>
            <dd className="bg-yellow-100 dark:bg-yellow-900/40 rounded px-2 py-1 whitespace-pre-wrap">{e.text || '（空）'}</dd>
          </div>
          {e.related.length > 0 && (
            <div>
              <dt className="text-muted-foreground text-xs">見出し（項目・期。自動推定）</dt>
              <dd className="space-y-1">
                {e.related.map((r) => (
                  <div key={r.id} className="bg-sky-50 dark:bg-sky-900/40 rounded px-2 py-1">
                    {r.text}
                  </div>
                ))}
              </dd>
            </div>
          )}
          <div>
            <dt className="text-muted-foreground text-xs">原本・抽出版</dt>
            <dd className="font-mono text-[11px] break-all">
              SHA-256 {e.sha256.slice(0, 16)}…<br />
              抽出版 {e.extraction_id.slice(0, 8)}… / 根拠 {e.element_id.slice(0, 8)}…
            </dd>
          </div>
        </dl>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs">
          <div className="bg-muted flex gap-1 rounded-md p-0.5">
            {(['pdf', 'html'] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={cn('rounded px-2 py-0.5', mode === m ? 'bg-background shadow-sm' : 'text-muted-foreground')}
              >
                {m === 'pdf' ? 'PDF' : 'HTML'}
              </button>
            ))}
          </div>
          {mode === 'pdf' && (
            <>
              <Button size="icon-xs" variant="ghost" onClick={() => setScale((s) => Math.max(0.5, s - 0.2))} aria-label="縮小">
                <ZoomOut />
              </Button>
              <span className="tabular-nums">{Math.round(scale * 100)}%</span>
              <Button size="icon-xs" variant="ghost" onClick={() => setScale((s) => Math.min(3, s + 0.2))} aria-label="拡大">
                <ZoomIn />
              </Button>
            </>
          )}
          <span className="text-muted-foreground ml-auto">黄色: 根拠の値／青: 項目・期などの見出し</span>
        </div>
        {mode === 'pdf' ? (
          <PdfPageView url={pdfUrl(e.version_id)} pageNumber={e.page_number} highlights={highlights} scale={scale} focusId={e.element_id} className="min-h-0 flex-1" />
        ) : page.data ? (
          <HtmlPageView
            page={page.data}
            values={[e.element_id]}
            contexts={e.related.map((r) => r.id)}
            className="min-h-0 flex-1"
          />
        ) : (
          <p className="text-muted-foreground p-4 text-sm">読み込み中…</p>
        )}
      </div>
    </div>
  )
}
