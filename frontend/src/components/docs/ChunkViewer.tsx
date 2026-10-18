import { useQuery } from '@tanstack/react-query'
import { ZoomIn, ZoomOut } from 'lucide-react'
import { useState } from 'react'

import { Link } from '@/components/docs/common'
import { PdfPageView, type Highlight } from '@/components/docs/PdfPageView'
import { Button } from '@/components/ui/button'
import { docsApi, pdfUrl } from '@/lib/docs'

const ROLE_LABELS: Record<string, string> = { content: '本文・値', header: '見出し', unit: '単位', title: '表題' }

/** 検索結果のチャンクを原本上で強調する（値・本文は黄色、表題・単位・見出しは薄い青） */
export function ChunkViewer({ chunkId }: { chunkId: string }) {
  const chunk = useQuery({ queryKey: ['chunk', chunkId], queryFn: () => docsApi.chunk(chunkId) })
  const [scale, setScale] = useState(1.3)
  const c = chunk.data
  if (chunk.isError) return <p className="text-destructive p-6 text-sm">チャンクが見つかりません</p>
  if (!c) return <p className="text-muted-foreground p-6 text-sm">読み込み中…</p>

  const highlights: Highlight[] = c.elements
    .filter((e) => e.kind !== 'table')
    .map((e) => ({ id: e.id, quads: e.quads, tone: c.roles[e.id] === 'content' ? ('value' as const) : ('context' as const) }))
  const counts = Object.values(c.roles).reduce<Record<string, number>>((acc, r) => ({ ...acc, [r]: (acc[r] ?? 0) + 1 }), {})

  return (
    <div className="flex h-full min-h-0">
      <aside className="w-96 shrink-0 space-y-3 overflow-y-auto border-r p-4 text-sm">
        <p className="text-muted-foreground text-xs">
          <Link to="/docs/search">検索</Link> / 検索結果のチャンク
        </p>
        <div>
          <div className="text-muted-foreground text-xs">位置</div>
          <div>
            PDF {c.page_number}ページ目・{c.kind === 'table' ? '表' : '本文'}
          </div>
        </div>
        {c.heading && (
          <div>
            <div className="text-muted-foreground text-xs">見出し</div>
            <div>{c.heading}</div>
          </div>
        )}
        <div className="text-muted-foreground text-xs">
          根拠要素: {Object.entries(counts).map(([r, n]) => `${ROLE_LABELS[r] ?? r} ${n}`).join('・')}
        </div>
        <pre className="bg-muted/40 rounded p-2 text-xs leading-5 whitespace-pre-wrap">{c.text}</pre>
        <p className="text-muted-foreground text-xs">
          チャンクは検索用のまとまりで、引用の最小単位ではありません。個々の値の根拠は、レビュー画面でセルや行を選んで確認できます。
          <Link to={`/docs/extractions/${c.extraction_id}/review?page=${c.page_number}`}>レビュー画面で開く</Link>
        </p>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs">
          <Button size="icon-xs" variant="ghost" onClick={() => setScale((s) => Math.max(0.5, s - 0.2))} aria-label="縮小">
            <ZoomOut />
          </Button>
          <span className="tabular-nums">{Math.round(scale * 100)}%</span>
          <Button size="icon-xs" variant="ghost" onClick={() => setScale((s) => Math.min(3, s + 0.2))} aria-label="拡大">
            <ZoomIn />
          </Button>
          <span className="text-muted-foreground ml-auto">黄色: 本文・値／青: 表題・単位・見出し</span>
        </div>
        <PdfPageView url={pdfUrl(c.version_id)} pageNumber={c.page_number} highlights={highlights} scale={scale} className="min-h-0 flex-1" />
      </div>
    </div>
  )
}
