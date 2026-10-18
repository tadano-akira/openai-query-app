import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy, type PageViewport } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { useEffect, useRef, useState } from 'react'

import type { Quad } from '@/lib/docs'
import { cn } from '@/lib/utils'

GlobalWorkerOptions.workerSrc = workerUrl

/** value: 根拠の値（黄色）、context: 項目・期・単位など（薄い青）、select: レビューで選択中 */
export type Highlight = { id: string; quads: Quad[]; tone: 'value' | 'context' | 'select' }

const TONE: Record<Highlight['tone'], string> = {
  value: 'fill-yellow-300/45 stroke-yellow-500',
  context: 'fill-sky-300/30 stroke-sky-500',
  select: 'fill-orange-300/40 stroke-orange-500',
}

/**
 * 原本 PDF の 1 ページを描画し、根拠の四辺形を SVG で重ねる。
 * 四辺形は PDF user space（バックエンドで CropBox ∩ MediaBox を基準に変換済み）なので、
 * viewport.convertToViewportPoint で回転・CropBox・拡大率を反映した CSS 座標にする。
 * canvas は devicePixelRatio 倍で描き、SVG は CSS ピクセルで重ねる（二重に倍率をかけない）。
 */
export function PdfPageView({
  url,
  pageNumber,
  highlights,
  scale = 1.3,
  extraRotation = 0,
  focusId,
  onPageCount,
  onSelect,
  className,
}: {
  url: string
  pageNumber: number
  highlights: Highlight[]
  scale?: number
  extraRotation?: number
  focusId?: string | null
  onPageCount?: (n: number) => void
  onSelect?: (id: string) => void
  className?: string
}) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  const [viewport, setViewport] = useState<PageViewport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    setDoc(null)
    setError(null)
    // 同一オリジンなので Cookie（ログイン）が送られる
    const task = getDocument({
      url,
      withCredentials: true,
      // 埋め込みのない日本語フォント等の描画に必要（vite.config.ts の pdfjsAssets で配信）
      cMapUrl: '/pdfjs/cmaps/',
      cMapPacked: true,
      standardFontDataUrl: '/pdfjs/standard_fonts/',
      wasmUrl: '/pdfjs/wasm/',
      iccUrl: '/pdfjs/iccs/',
    })
    task.promise.then(
      (d) => {
        if (cancelled) return
        setDoc(d)
        onPageCount?.(d.numPages)
      },
      (e: unknown) => !cancelled && setError(e instanceof Error ? e.message : String(e)),
    )
    return () => {
      cancelled = true
      void task.destroy()
    }
    // onPageCount は親の再描画で変わりうるが、文書の読み込み直しは url の変更時だけ行う
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url])

  useEffect(() => {
    if (!doc || pageNumber < 1 || pageNumber > doc.numPages) return
    let cancelled = false
    // 前のページの座標でハイライトを重ねないよう、描画が終わるまで消しておく
    setViewport(null)
    let renderTask: { cancel: () => void; promise: Promise<void> } | null = null
    void doc.getPage(pageNumber).then((page) => {
      if (cancelled || !canvasRef.current) return
      const vp = page.getViewport({ scale, rotation: (page.rotate + extraRotation) % 360 })
      const canvas = canvasRef.current
      const dpr = window.devicePixelRatio || 1
      canvas.width = Math.floor(vp.width * dpr)
      canvas.height = Math.floor(vp.height * dpr)
      canvas.style.width = `${vp.width}px`
      canvas.style.height = `${vp.height}px`
      renderTask = page.render({
        canvas,
        viewport: vp,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
      })
      renderTask.promise.then(
        () => !cancelled && setViewport(vp),
        () => undefined, // 再描画で取り消された場合
      )
    })
    return () => {
      cancelled = true
      renderTask?.cancel()
    }
  }, [doc, pageNumber, scale, extraRotation])

  // 描画が終わったら、注目する根拠（なければ最初の根拠）までスクロールする
  useEffect(() => {
    if (!viewport || !scrollRef.current) return
    const target = highlights.find((h) => h.id === focusId) ?? highlights.find((h) => h.tone !== 'context')
    if (!target?.quads.length) return
    const ys = target.quads.flatMap((q) => q.map(([x, y]) => viewport.convertToViewportPoint(x, y)[1]))
    const xs = target.quads.flatMap((q) => q.map(([x, y]) => viewport.convertToViewportPoint(x, y)[0]))
    const box = scrollRef.current
    box.scrollTo({
      top: Math.max(0, Math.min(...ys) - box.clientHeight / 3),
      left: Math.max(0, Math.min(...xs) - box.clientWidth / 4),
      behavior: 'smooth',
    })
  }, [viewport, highlights, focusId])

  const points = (q: Quad) => {
    if (!viewport) return ''
    // 四辺形は 左上・右上・左下・右下 の順。多角形は 左上→右上→右下→左下 で結ぶ
    const [ul, ur, ll, lr] = q.map(([x, y]) => viewport.convertToViewportPoint(x, y))
    return [ul, ur, lr, ll].map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  }

  if (error) return <p className="text-destructive p-4 text-sm">PDF を表示できません: {error}</p>
  return (
    <div ref={scrollRef} className={cn('bg-muted/40 overflow-auto', className)}>
      <div className="relative m-4 inline-block shadow-md">
        <canvas ref={canvasRef} className="block bg-white" />
        {viewport && (
          <svg
            className="absolute top-0 left-0"
            width={viewport.width}
            height={viewport.height}
            viewBox={`0 0 ${viewport.width} ${viewport.height}`}
          >
            {highlights.map((h) =>
              h.quads.map((q, i) => (
                <polygon
                  key={`${h.id}-${i}`}
                  points={points(q)}
                  className={cn(TONE[h.tone], onSelect && 'cursor-pointer', h.id === focusId && 'stroke-2')}
                  strokeWidth={h.id === focusId ? 2 : 1}
                  onClick={onSelect ? () => onSelect(h.id) : undefined}
                />
              )),
            )}
          </svg>
        )}
        {!viewport && <div className="text-muted-foreground absolute inset-0 grid place-items-center text-sm">読み込み中…</div>}
      </div>
    </div>
  )
}
