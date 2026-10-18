import { useEffect, useRef } from 'react'

import type { DocTable, PageDetail } from '@/lib/docs'
import { cn } from '@/lib/utils'

/** 根拠 ID からアンカー名（固定リンク #ev-{uuid}） */
const anchorId = (id: string) => `ev-${id}`

function TableView({
  table,
  tone,
  onSelect,
}: {
  table: DocTable
  tone: (id: string) => string | undefined
  onSelect?: (id: string) => void
}) {
  const rows = Math.max(0, ...table.cells.map((c) => c.row)) + 1
  const cols = Math.max(0, ...table.cells.map((c) => c.col)) + 1
  const at = new Map(table.cells.map((c) => [`${c.row}:${c.col}`, c]))
  return (
    <table id={anchorId(table.id)} className="my-2 w-full border-collapse text-xs">
      <tbody>
        {Array.from({ length: rows }, (_, r) => (
          <tr key={r}>
            {Array.from({ length: cols }, (_, c) => {
              const cell = at.get(`${r}:${c}`)
              if (!cell) return <td key={c} className="border border-dashed px-2 py-1" title="結合セル（抽出なし）" />
              return (
                <td
                  key={c}
                  id={anchorId(cell.id)}
                  onClick={onSelect ? () => onSelect(cell.id) : undefined}
                  className={cn(
                    'border px-2 py-1 whitespace-pre-wrap',
                    c > 0 && 'text-right tabular-nums',
                    onSelect && 'hover:bg-muted cursor-pointer',
                    tone(cell.id),
                  )}
                >
                  {cell.text}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * 抽出結果から作る閲覧用の HTML（元 PDF の見た目の再現は目的にしない）。
 * 各行・セルに id="ev-{uuid}" を付け、同じ根拠 ID でハイライトする。
 * 原文は React のテキストとして描画し、HTML としては解釈しない。
 */
export function HtmlPageView({
  page,
  values,
  contexts = [],
  focusId,
  onSelect,
  className,
}: {
  page: PageDetail
  values: string[]
  contexts?: string[]
  focusId?: string | null
  onSelect?: (id: string) => void
  className?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const tone = (id: string) =>
    values.includes(id) ? 'bg-yellow-200 dark:bg-yellow-700/60' : contexts.includes(id) ? 'bg-sky-100 dark:bg-sky-800/50' : undefined

  useEffect(() => {
    const id = focusId ?? values[0]
    if (!id) return
    // 再描画の後でもスクロールできるよう、描画後に対象を探す
    const el = ref.current?.querySelector(`#${CSS.escape(anchorId(id))}`)
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [page, focusId, values])

  return (
    <div ref={ref} className={cn('overflow-auto p-4', className)}>
      <section className="space-y-0.5">
        <h3 className="text-muted-foreground mb-1 text-xs font-medium">本文（抽出行）</h3>
        {page.lines.map((line) => (
          <p
            key={line.id}
            id={anchorId(line.id)}
            onClick={onSelect ? () => onSelect(line.id) : undefined}
            className={cn('flex gap-3 rounded px-1 text-sm', onSelect && 'hover:bg-muted cursor-pointer', tone(line.id))}
          >
            <span className="text-muted-foreground w-8 shrink-0 text-right font-mono text-[11px] leading-5">
              {line.line_seq}
            </span>
            <span className="whitespace-pre-wrap">{line.text}</span>
          </p>
        ))}
        {page.lines.length === 0 && <p className="text-muted-foreground text-sm">文字はありません</p>}
      </section>
      {page.tables.map((t, i) => (
        <section key={t.id} className="mt-4">
          <h3 className="text-muted-foreground text-xs font-medium">表 {i + 1}</h3>
          <TableView table={t} tone={tone} onSelect={onSelect} />
        </section>
      ))}
    </div>
  )
}
