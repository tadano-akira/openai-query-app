import { FileText, MessagesSquare } from 'lucide-react'

import { navigate } from '@/lib/router'
import { cn } from '@/lib/utils'

/** チャットと資料Q&A の切り替え */
export function AppNav({ current }: { current: 'chat' | 'docs' }) {
  const item = (key: 'chat' | 'docs', label: string, Icon: typeof FileText, to: string) => (
    <button
      onClick={() => navigate(to)}
      aria-current={current === key ? 'page' : undefined}
      className={cn(
        'flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors',
        current === key ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground',
      )}
    >
      <Icon className="size-3.5" /> {label}
    </button>
  )
  return (
    <div className="bg-muted flex gap-1 rounded-lg p-1">
      {item('chat', 'チャット', MessagesSquare, '/')}
      {item('docs', '資料Q&A', FileText, '/docs')}
    </div>
  )
}
