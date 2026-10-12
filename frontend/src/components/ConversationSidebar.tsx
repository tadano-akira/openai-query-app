import { useQuery } from '@tanstack/react-query'
import { MessageSquarePlus } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 30

export function ConversationSidebar({
  selectedId,
  onSelect,
  onNew,
  creating,
}: {
  selectedId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  creating: boolean
}) {
  const [page, setPage] = useState(0)
  const { data, isError } = useQuery({
    queryKey: ['conversations', page],
    queryFn: () => api.listConversations(page * PAGE_SIZE, PAGE_SIZE),
  })
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  return (
    <aside className="bg-muted/30 flex h-full w-72 shrink-0 flex-col border-r">
      <div className="p-3">
        <Button className="w-full justify-start" variant="outline" onClick={onNew} disabled={creating}>
          <MessageSquarePlus /> 新規チャット
        </Button>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <nav className="space-y-0.5 px-2 pb-2">
          {isError && <p className="text-destructive px-2 text-xs">会話一覧を取得できません</p>}
          {data?.items.map((c) => (
            <button
              key={c.id}
              onClick={() => onSelect(c.id)}
              className={cn(
                'hover:bg-muted w-full rounded-md px-2 py-1.5 text-left',
                c.id === selectedId && 'bg-muted',
              )}
            >
              <div className="truncate text-sm">{c.title}</div>
              <div className="text-muted-foreground flex justify-between gap-2 text-[11px]">
                <span>{formatDate(c.updated_at).slice(5, 16)}</span>
                <span>
                  {c.message_count}件 · {c.total_tokens.toLocaleString()} tok
                  {c.unknown_usage_call_count > 0 && '+?'}
                </span>
              </div>
            </button>
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
    </aside>
  )
}
