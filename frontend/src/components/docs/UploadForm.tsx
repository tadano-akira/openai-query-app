import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Plus, Upload } from 'lucide-react'
import { useState } from 'react'

import { Field, inputClass } from '@/components/docs/common'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import { DOCUMENT_TYPE_LABELS, SCOPE_LABELS, docsApi } from '@/lib/docs'
import { navigate } from '@/lib/router'

function IssuerPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const queryClient = useQueryClient()
  const { data: issuers = [] } = useQuery({ queryKey: ['issuers'], queryFn: docsApi.issuers })
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function add() {
    setError(null)
    try {
      const issuer = await docsApi.createIssuer(name.trim(), code.trim() || null)
      await queryClient.invalidateQueries({ queryKey: ['issuers'] })
      onChange(issuer.id)
      setAdding(false)
      setName('')
      setCode('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <select className={inputClass} value={value} onChange={(e) => onChange(e.target.value)} required>
          <option value="">企業を選択</option>
          {issuers.map((i) => (
            <option key={i.id} value={i.id}>
              {i.name}
              {i.code ? `（${i.code}）` : ''}
            </option>
          ))}
        </select>
        <Button type="button" variant="outline" size="sm" onClick={() => setAdding((a) => !a)}>
          <Plus /> 企業を追加
        </Button>
      </div>
      {adding && (
        <div className="bg-muted/40 flex flex-wrap items-end gap-2 rounded-lg p-2">
          <Field label="企業名">
            <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="例: トヨタ自動車" />
          </Field>
          <Field label="証券コード（任意）">
            <input className={inputClass} value={code} onChange={(e) => setCode(e.target.value)} placeholder="例: 7203" />
          </Field>
          <Button type="button" size="sm" disabled={!name.trim()} onClick={() => void add()}>
            追加
          </Button>
          {error && <p className="text-destructive w-full text-xs">{error}</p>}
        </div>
      )}
    </div>
  )
}

/** PDF の登録。原本はそのまま保存し、解析ジョブを登録する。暗号化 PDF は解析時に拒否される */
export function UploadForm({ documentId }: { documentId: string | null }) {
  const queryClient = useQueryClient()
  const existing = useQuery({
    queryKey: ['doc', documentId],
    queryFn: () => docsApi.document(documentId!),
    enabled: !!documentId,
  })
  const [issuerId, setIssuerId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [fields, setFields] = useState({
    title: '',
    document_type: 'tanshin_fy',
    period_label: '',
    period_start: '',
    period_end: '',
    published_on: '',
    scope: 'consolidated',
    accounting_standard: '',
    currency: 'JPY',
    source_url: '',
    correction_reason: '',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof typeof fields) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setFields((f) => ({ ...f, [k]: e.target.value }))

  const doc = existing.data
  const effectiveIssuer = doc?.issuer.id ?? issuerId

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!file || !effectiveIssuer) return
    setSubmitting(true)
    setError(null)
    const form = new FormData()
    form.append('file', file)
    form.append('issuer_id', effectiveIssuer)
    form.append('title', doc?.title ?? fields.title)
    form.append('document_type', doc?.document_type ?? fields.document_type)
    for (const [k, v] of Object.entries(fields)) {
      if (k !== 'title' && k !== 'document_type' && v) form.append(k, v)
    }
    if (doc) {
      form.append('document_id', doc.id)
      const prev = doc.versions[doc.versions.length - 1]
      if (prev && fields.correction_reason) form.append('supersedes_id', prev.id)
    }
    try {
      const res = await docsApi.upload(form)
      await queryClient.invalidateQueries({ queryKey: ['docs'] })
      navigate(`/docs/versions/${res.version_id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit} className="max-w-3xl space-y-4 p-6">
      <div>
        <h1 className="text-lg font-semibold">{doc ? `新しい版を登録: ${doc.title}` : 'PDFを登録'}</h1>
        <p className="text-muted-foreground text-sm">
          公式の決算資料（文字情報を持つPDF）を登録します。原本は変更せずに保存し、登録後に自動で解析します。
          スキャン画像だけのPDFはOCRしないため、検索対象にできません。
        </p>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>登録できません</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Field label="PDFファイル（50MB・500ページまで）">
        <input
          type="file"
          accept="application/pdf,.pdf"
          required
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-sm"
        />
      </Field>
      {!doc && (
        <>
          <Field label="企業">
            <IssuerPicker value={issuerId} onChange={setIssuerId} />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="資料名">
              <input className={inputClass} required value={fields.title} onChange={set('title')} placeholder="例: 2026年3月期 決算短信" />
            </Field>
            <Field label="資料種別">
              <select className={inputClass} value={fields.document_type} onChange={set('document_type')}>
                {Object.entries(DOCUMENT_TYPE_LABELS).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        </>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="対象期の表示名">
          <input className={inputClass} value={fields.period_label} onChange={set('period_label')} placeholder="例: 2026年3月期" />
        </Field>
        <Field label="対象期間の開始日">
          <input type="date" className={inputClass} value={fields.period_start} onChange={set('period_start')} />
        </Field>
        <Field label="対象期末日">
          <input type="date" className={inputClass} value={fields.period_end} onChange={set('period_end')} />
        </Field>
        <Field label="公表日">
          <input type="date" className={inputClass} value={fields.published_on} onChange={set('published_on')} />
        </Field>
        <Field label="連結区分">
          <select className={inputClass} value={fields.scope} onChange={set('scope')}>
            {Object.entries(SCOPE_LABELS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="会計基準">
          <select className={inputClass} value={fields.accounting_standard} onChange={set('accounting_standard')}>
            <option value="">未選択</option>
            <option value="IFRS">IFRS</option>
            <option value="JGAAP">日本基準</option>
            <option value="USGAAP">米国基準</option>
            <option value="other">その他</option>
          </select>
        </Field>
      </div>
      <Field label="公式の掲載元URL（公式性の確認に必要）">
        <input
          type="url"
          className={inputClass}
          value={fields.source_url}
          onChange={set('source_url')}
          placeholder="https://global.toyota/jp/ir/... など、企業IRサイトやEDINET・TDnetのURL"
        />
      </Field>
      {doc && (
        <Field label="訂正・差し替えの理由（訂正資料の場合）">
          <input className={inputClass} value={fields.correction_reason} onChange={set('correction_reason')} />
        </Field>
      )}
      <Button type="submit" disabled={submitting || !file || !effectiveIssuer}>
        {submitting ? <Loader2 className="animate-spin" /> : <Upload />} 登録して解析
      </Button>
    </form>
  )
}
