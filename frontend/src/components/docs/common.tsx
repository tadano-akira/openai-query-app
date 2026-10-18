import { Badge } from '@/components/ui/badge'
import { navigate } from '@/lib/router'
import { cn } from '@/lib/utils'
import { EXTRACTION_STATUS_LABELS, OFFICIAL_LABELS, QUALITY_LABELS, type Extraction } from '@/lib/docs'

/** 画面内リンク（History API で遷移する） */
export function Link({ to, className, children }: { to: string; className?: string; children: React.ReactNode }) {
  return (
    <a
      href={to}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
        e.preventDefault()
        navigate(to)
      }}
      className={cn('text-primary underline-offset-2 hover:underline', className)}
    >
      {children}
    </a>
  )
}

export function OfficialBadge({ status }: { status: string }) {
  const variant = status === 'confirmed' ? 'secondary' : status === 'rejected' ? 'destructive' : 'outline'
  return <Badge variant={variant}>公式性: {OFFICIAL_LABELS[status] ?? status}</Badge>
}

export function ExtractionBadge({ e }: { e: Extraction | undefined }) {
  if (!e) return <Badge variant="outline">未解析</Badge>
  if (e.status !== 'succeeded') {
    return (
      <Badge variant={e.status === 'failed' ? 'destructive' : 'outline'} title={e.error_message ?? undefined}>
        {EXTRACTION_STATUS_LABELS[e.status]}
        {e.error_code && `（${e.error_code}）`}
      </Badge>
    )
  }
  const q = e.quality ?? 'ok'
  return (
    <Badge variant={q === 'ok' ? 'secondary' : q === 'unsupported_no_text' ? 'destructive' : 'outline'}>
      {QUALITY_LABELS[q]}
    </Badge>
  )
}

export function ApprovalBadge({ e }: { e: Extraction | undefined }) {
  if (!e?.approved_at) return <Badge variant="outline">未承認</Badge>
  return <Badge variant="secondary">{e.active ? '承認済み・既定' : '承認済み'}</Badge>
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-muted-foreground text-xs">{label}</span>
      {children}
    </label>
  )
}

export const inputClass =
  'border-input bg-background focus-visible:ring-ring/50 h-8 w-full rounded-lg border px-2 text-sm outline-none focus-visible:ring-3'
