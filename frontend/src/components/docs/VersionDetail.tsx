import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, FileSearch, RefreshCw, Trash2, Upload } from 'lucide-react'
import { useState } from 'react'

import { ApprovalBadge, ExtractionBadge, Field, Link, OfficialBadge, inputClass } from '@/components/docs/common'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ApiError } from '@/lib/api'
import {
  DOCUMENT_TYPE_LABELS,
  QUALITY_LABELS,
  SCOPE_LABELS,
  docsApi,
  pdfUrl,
  textCoverage,
  type DocVersion,
} from '@/lib/docs'
import { formatDate } from '@/lib/format'
import { navigate } from '@/lib/router'

const META_FIELDS: { key: keyof DocVersion; label: string; type?: string }[] = [
  { key: 'period_label', label: '対象期の表示名' },
  { key: 'period_start', label: '対象期間の開始日', type: 'date' },
  { key: 'period_end', label: '対象期末日', type: 'date' },
  { key: 'published_on', label: '公表日', type: 'date' },
  { key: 'accounting_standard', label: '会計基準' },
  { key: 'currency', label: '通貨' },
  { key: 'source_url', label: '公式の掲載元URL', type: 'url' },
]

function MetadataForm({ v, onSaved }: { v: DocVersion; onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries([...META_FIELDS.map((f) => [f.key, (v[f.key] as string | null) ?? '']), ['scope', v.scope]]),
  )
  const [error, setError] = useState<string | null>(null)
  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await docsApi.patchVersion(v.id, Object.fromEntries(Object.entries(values).map(([k, x]) => [k, x || null])))
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
  }
  return (
    <form onSubmit={save} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        {META_FIELDS.map((f) => (
          <Field key={f.key} label={f.label}>
            <input
              type={f.type ?? 'text'}
              className={inputClass}
              value={values[f.key]}
              onChange={(e) => setValues((x) => ({ ...x, [f.key]: e.target.value }))}
            />
          </Field>
        ))}
        <Field label="連結区分">
          <select className={inputClass} value={values.scope} onChange={(e) => setValues((x) => ({ ...x, scope: e.target.value }))}>
            {Object.entries(SCOPE_LABELS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {error && <p className="text-destructive text-sm">{error}</p>}
      <Button type="submit" size="sm" variant="outline">
        メタデータを保存
      </Button>
      <p className="text-muted-foreground text-xs">変更は監査記録に残ります。公式性を確認済みの版は、変更すると「未確認」に戻ります。</p>
    </form>
  )
}

export function VersionDetail({ versionId }: { versionId: string }) {
  const queryClient = useQueryClient()
  const version = useQuery({
    queryKey: ['version', versionId],
    queryFn: () => docsApi.version(versionId),
    refetchInterval: (q) => (q.state.data?.extractions.some((e) => ['queued', 'running'].includes(e.status)) ? 1500 : false),
  })
  const v = version.data
  const doc = useQuery({
    queryKey: ['doc', v?.document_id],
    queryFn: () => docsApi.document(v!.document_id),
    enabled: !!v,
  })
  const [memo, setMemo] = useState('')
  const [error, setError] = useState<string | null>(null)

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['version', versionId] })
    void queryClient.invalidateQueries({ queryKey: ['docs'] })
    void queryClient.invalidateQueries({ queryKey: ['doc'] })
  }
  async function run(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
  }

  if (version.isLoading || !v) return <p className="text-muted-foreground p-6 text-sm">読み込み中…</p>
  const d = doc.data
  return (
    <div className="max-w-5xl space-y-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-muted-foreground text-xs">
            <Link to="/docs">資料</Link> / {d?.issuer.name}
          </p>
          <h1 className="text-lg font-semibold">{d?.title}</h1>
          <p className="text-muted-foreground text-sm">
            {d && DOCUMENT_TYPE_LABELS[d.document_type]}・{v.period_label ?? '対象期未設定'}・{SCOPE_LABELS[v.scope]}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => window.open(pdfUrl(v.id), '_blank', 'noopener')}>
            <ExternalLink /> 原本PDF
          </Button>
          {d && (
            <Button variant="outline" size="sm" onClick={() => navigate(`/docs/upload?document=${d.id}`)}>
              <Upload /> 新しい版を登録
            </Button>
          )}
          {d && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                window.confirm(`「${d.title}」を削除しますか？（検索対象から外します。原本・抽出結果・記録は残ります）`) &&
                void run(async () => {
                  await docsApi.deleteDocument(d.id)
                  navigate('/docs')
                })
              }
            >
              <Trash2 /> 削除
            </Button>
          )}
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>操作できません</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold">原本</h2>
        <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">ファイル名</dt>
          <dd>{v.original_filename ?? '—'}</dd>
          <dt className="text-muted-foreground">SHA-256</dt>
          <dd className="font-mono text-xs break-all">{v.sha256}</dd>
          <dt className="text-muted-foreground">サイズ・ページ数</dt>
          <dd>
            {(v.file_size / 1024 / 1024).toFixed(2)} MB・{v.page_count ?? '—'} ページ
          </dd>
          <dt className="text-muted-foreground">登録日時</dt>
          <dd>{formatDate(v.fetched_at)}</dd>
          {v.correction_reason && (
            <>
              <dt className="text-muted-foreground">訂正理由</dt>
              <dd>{v.correction_reason}</dd>
            </>
          )}
        </dl>
        {d && d.versions.length > 1 && (
          <p className="text-muted-foreground text-xs">
            この資料には {d.versions.length} 版あります:{' '}
            {d.versions.map((x, i) => (
              <span key={x.id} className="mr-2">
                {x.id === v.id ? <b>第{i + 1}版（表示中）</b> : <Link to={`/docs/versions/${x.id}`}>第{i + 1}版</Link>}
              </span>
            ))}
          </p>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold">メタデータ</h2>
        <MetadataForm key={`${v.id}-${v.official_status}`} v={v} onSaved={refresh} />
      </section>

      <section className="space-y-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          公式性の確認 <OfficialBadge status={v.official_status} />
        </h2>
        <p className="text-muted-foreground text-xs">
          掲載元URLと原本の内容（企業・資料・対象期）を確認してください。確認済みでない版は検索対象にしません。
          {v.official_confirmed_by && ` 最終確認: ${v.official_confirmed_by}（${formatDate(v.official_confirmed_at)}）`}
          {v.official_memo && ` メモ: ${v.official_memo}`}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input className={`${inputClass} max-w-sm`} placeholder="確認メモ（任意）" value={memo} onChange={(e) => setMemo(e.target.value)} />
          <Button size="sm" onClick={() => void run(() => docsApi.setOfficial(v.id, 'confirmed', memo || null))}>
            確認済みにする
          </Button>
          <Button size="sm" variant="outline" onClick={() => void run(() => docsApi.setOfficial(v.id, 'rejected', memo || null))}>
            却下
          </Button>
          {v.official_status !== 'unconfirmed' && (
            <Button size="sm" variant="ghost" onClick={() => void run(() => docsApi.setOfficial(v.id, 'unconfirmed', memo || null))}>
              未確認に戻す
            </Button>
          )}
        </div>
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">抽出版</h2>
          <Button size="sm" variant="outline" onClick={() => void run(() => docsApi.reextract(v.id))}>
            <RefreshCw /> 再解析（新しい抽出版を作成）
          </Button>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>作成</TableHead>
              <TableHead>状態</TableHead>
              <TableHead>内容</TableHead>
              <TableHead>承認</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {[...v.extractions].reverse().map((e) => (
              <TableRow key={e.id}>
                <TableCell className="text-xs whitespace-nowrap">
                  {formatDate(e.created_at)}
                  <div className="text-muted-foreground font-mono">{e.parser_version}</div>
                </TableCell>
                <TableCell>
                  <ExtractionBadge e={e} />
                  {e.error_message && <div className="text-destructive mt-1 text-xs">{e.error_message}</div>}
                </TableCell>
                <TableCell className="text-xs">
                  {e.stats ? (
                    <>
                      {e.stats.pages}ページ・{e.stats.lines}行・表{e.stats.tables}（セル{e.stats.cells}）
                      <div className="text-muted-foreground">
                        文字抽出率 {textCoverage(e)}・
                        {Object.entries(e.stats.page_quality)
                          .map(([q, n]) => `${QUALITY_LABELS[q] ?? q} ${n}`)
                          .join('・')}
                      </div>
                    </>
                  ) : (
                    '—'
                  )}
                </TableCell>
                <TableCell className="text-xs">
                  <ApprovalBadge e={e} />
                  {e.approved_by && (
                    <div className="text-muted-foreground mt-1">
                      {e.approved_by}（{formatDate(e.approved_at).slice(0, 16)}）{e.approval_memo && `: ${e.approval_memo}`}
                    </div>
                  )}
                </TableCell>
                <TableCell className="text-right whitespace-nowrap">
                  {e.status === 'succeeded' && e.quality !== 'unsupported_no_text' && (
                    <div className="flex justify-end gap-1">
                      <Button size="xs" variant="outline" onClick={() => navigate(`/docs/extractions/${e.id}/review`)}>
                        <FileSearch /> レビュー
                      </Button>
                      {e.approved_at && !e.active && (
                        <Button size="xs" onClick={() => void run(() => docsApi.activate(e.id))}>
                          既定にする
                        </Button>
                      )}
                    </div>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <p className="text-muted-foreground text-xs">
          承認はレビュー画面で行います。再解析しても既存の抽出版と、それを参照する根拠は変わりません。
        </p>
      </section>
    </div>
  )
}
