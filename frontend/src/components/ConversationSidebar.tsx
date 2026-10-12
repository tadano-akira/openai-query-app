import { useQuery, useQueryClient } from '@tanstack/react-query'
import { MessageSquarePlus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { UsagePanel } from '@/components/UsagePanel'
import { ApiError, api, type ConversationSummary } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 30

export function ConversationSidebar({
  selectedId,
  onSelect,
  onNew,
  onHidden,
  creating,
}: {
  selectedId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onHidden: (id: string) => void
  creating: boolean
}) {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(0)
  const { data, isError } = useQuery({
    queryKey: ['conversations', page],
    queryFn: () => api.listConversations(page * PAGE_SIZE, PAGE_SIZE),
  })
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  /** 一覧から非表示にする（DB のデータは残る） */
  async function hide(c: ConversationSummary) {
    if (!window.confirm(`「${c.title}」を一覧から削除しますか？
（データはDBに残ります）`)) return
    try {
      await api.hideConversation(c.id)
    } catch (e) {
      // 既に非表示なら一覧の更新だけ行う
      if (!(e instanceof ApiError && e.status === 404)) {
        window.alert(e instanceof Error ? e.message : String(e))
        return
      }
    }
    onHidden(c.id)
    if (data?.items.length === 1 && page > 0) setPage(page - 1)
    await queryClient.invalidateQueries({ queryKey: ['conversations'] })
  }

  return (
    <aside className="bg-muted/30 flex h-full w-72 shrink-0 flex-col border-r">
      <div className="p-3">
        <Button className="w-full justify-start" variant="outline" onClick={onNew} disabled={creating}>
          <MessageSquarePlus /> 新規チャット
        </Button>
      </div>
      {/* Radix の viewport 内側は display:table で幅が中身に広がり truncate が効かないため block にする */}
      <ScrollArea className="min-h-0 flex-1 [&_[data-slot=scroll-area-viewport]>div]:block!">
        <nav className="space-y-0.5 px-2 pb-2">
          {isError && <p className="text-destructive px-2 text-xs">会話一覧を取得できません</p>}
          {data?.items.map((c) => (
            <div
              key={c.id}
              className={cn('group hover:bg-muted relative rounded-md', c.id === selectedId && 'bg-muted')}
            >
              <button onClick={() => onSelect(c.id)} className="w-full px-2 py-1.5 text-left">
                <div className="truncate pr-6 text-sm">{c.title}</div>
                <div className="text-muted-foreground flex justify-between gap-2 text-[11px]">
                  <span>{formatDate(c.updated_at).slice(5, 16)}</span>
                  <span>
                    {c.message_count}件 · {c.total_tokens.toLocaleString()} tok
                    {c.unknown_usage_call_count > 0 && '+?'}
                  </span>
                </div>
                {c.models.length > 0 && (
                  <div className="text-muted-foreground truncate font-mono text-[11px]" title={c.models.join(', ')}>
                    {c.models.join(', ')}
                  </div>
                )}
              </button>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="会話を削除"
                title="一覧から削除（データは残ります）"
                disabled={creating}
                onClick={() => void hide(c)}
                className="text-muted-foreground hover:text-destructive absolute top-1 right-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
              >
                <Trash2 />
              </Button>
            </div>
          ))}
        </nav>
      </ScrollArea>
      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t p-2 text-xs">
          <Button variant="ghost" size="xs" disabled={page === 0} onClick={() => setPage(page - 1)}>
            前へ
          </Button>
          <span>
            {page + 1} / {totalPages}
          </span>
          <Button
            variant="ghost"
            size="xs"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage(page + 1)}
          >
            次へ
          </Button>
        </div>
      )}
      <UsagePanel />
    </aside>
  )
}
