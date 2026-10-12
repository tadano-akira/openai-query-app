import { ArrowUp } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'

export type ComposerValue = { prompt: string; web_search_enabled: boolean; search_url: string | null }

function ModelSelect({
  models,
  value,
  onChange,
  disabled,
}: {
  models: string[]
  value: string | null
  onChange: (model: string) => void
  disabled: boolean
}) {
  return (
    <select
      aria-label="モデル"
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled || models.length === 0}
      className="border-input bg-background focus-visible:ring-ring/50 h-8 rounded-lg border px-2 text-sm outline-none focus-visible:ring-3 disabled:opacity-50"
    >
      {models.length === 0 && <option value="">モデル取得中…</option>}
      {models.map((m) => (
        <option key={m} value={m}>
          {m}
        </option>
      ))}
    </select>
  )
}

/** 画面表示用の目安。正式な正規化・検証はバックエンドで行う */
function previewDomain(url: string): string | null {
  const raw = url.trim()
  if (!raw) return null
  try {
    return new URL(raw.includes('://') ? raw : `https://${raw}`).hostname.toLowerCase()
  } catch {
    return null
  }
}

export function Composer({
  disabled,
  onSubmit,
  models,
  model,
  onModelChange,
}: {
  disabled: boolean
  onSubmit: (v: ComposerValue) => Promise<boolean>
  models: string[]
  model: string | null
  onModelChange: (model: string) => void
}) {
  const [prompt, setPrompt] = useState('')
  const [webSearch, setWebSearch] = useState(true)
  const [url, setUrl] = useState('')
  const domain = previewDomain(url)
  const canSend = !disabled && prompt.trim().length > 0

  async function submit() {
    if (!canSend) return
    const ok = await onSubmit({
      prompt,
      web_search_enabled: webSearch,
      search_url: webSearch && url.trim() ? url.trim() : null,
    })
    if (ok) setPrompt('')
  }

  return (
    <div className="bg-background space-y-2 rounded-2xl border p-3 shadow-sm">
      <Textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={(e) => {
          // IME 変換中の Enter は送信しない
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            void submit()
          }
        }}
        placeholder="メッセージを入力（Enterで送信 / Shift+Enterで改行）"
        className="max-h-60 min-h-16 resize-none border-0 shadow-none focus-visible:ring-0"
        disabled={disabled}
      />
      <div className="flex flex-wrap items-center gap-3">
        <ModelSelect models={models} value={model} onChange={onModelChange} disabled={disabled} />
        <div className="flex items-center gap-2">
          <Switch id="web-search" checked={webSearch} onCheckedChange={setWebSearch} />
          <Label htmlFor="web-search" className="text-sm">
            Web検索
          </Label>
        </div>
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={!webSearch}
          placeholder="検索先URL（任意）例: https://finance.yahoo.co.jp/"
          className="h-8 min-w-0 flex-1 text-sm"
        />
        <Button size="icon" onClick={() => void submit()} disabled={!canSend} aria-label="送信">
          <ArrowUp />
        </Button>
      </div>
      {webSearch && (
        <p className="text-muted-foreground text-xs">
          検索対象: {domain ? `${domain}（サブドメイン含む）` : 'ドメイン制限なし'}
          {domain && ' ・ URLはサイト指定であり、そのページの取得を保証しません'}
        </p>
      )}
    </div>
  )
}
