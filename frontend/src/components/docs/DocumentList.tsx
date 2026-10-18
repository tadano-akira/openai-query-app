import { useQuery } from '@tanstack/react-query'
import { Upload } from 'lucide-react'

import { ApprovalBadge, ExtractionBadge, Link, OfficialBadge } from '@/components/docs/common'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  DOCUMENT_TYPE_LABELS,
  SCOPE_LABELS,
  docsApi,
  latestExtraction,
  latestVersion,
  textCoverage,
} from '@/lib/docs'
import { formatDate } from '@/lib/format'
import { navigate } from '@/lib/router'

/** 資料一覧: 企業・資料・対象期・連結区分・公表日・版・公式確認・抽出・承認・文字抽出率 */
export function DocumentList() {
  const { data, isLoading } = useQuery({
    queryKey: ['docs'],
    queryFn: docsApi.documents,
    // 解析中の資料があれば状態を追う
    refetchInterval: (q) =>
      q.state.data?.some((d) => ['queued', 'running'].includes(latestExtraction(latestVersion(d))?.status ?? ''))
        ? 2000
        : false,
  })
  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">資料</h1>
          <p className="text-muted-foreground text-sm">
            登録した公式PDFを解析し、抽出結果を確認・承認します。承認済みの資料だけが、今後の質問の根拠になります。
          </p>
        </div>
        <Button onClick={() => navigate('/docs/upload')}>
          <Upload /> PDFを登録
        </Button>
      </div>
      {isLoading ? (
        <p className="text-muted-foreground text-sm">読み込み中…</p>
      ) : !data?.length ? (
        <p className="text-muted-foreground text-sm">まだ資料がありません。「PDFを登録」から追加してください。</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>企業</TableHead>
              <TableHead>資料</TableHead>
              <TableHead>対象期</TableHead>
              <TableHead>区分</TableHead>
              <TableHead>公表日</TableHead>
              <TableHead>版</TableHead>
              <TableHead>状態</TableHead>
              <TableHead className="text-right">文字抽出率</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((d) => {
              const v = latestVersion(d)
              const e = latestExtraction(v)
              return (
                <TableRow key={d.id}>
                  <TableCell className="whitespace-nowrap">
                    {d.issuer.name}
                    {d.issuer.code && <span className="text-muted-foreground ml-1 text-xs">{d.issuer.code}</span>}
                  </TableCell>
                  <TableCell>
                    {v ? <Link to={`/docs/versions/${v.id}`}>{d.title}</Link> : d.title}
                    <div className="text-muted-foreground text-xs">{DOCUMENT_TYPE_LABELS[d.document_type]}</div>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{v?.period_label ?? v?.period_end ?? '—'}</TableCell>
                  <TableCell>{v ? SCOPE_LABELS[v.scope] : '—'}</TableCell>
                  <TableCell className="whitespace-nowrap">{v?.published_on ?? '—'}</TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    {d.versions.length}版<div className="text-muted-foreground">{v && formatDate(v.created_at).slice(0, 10)}</div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {v && <OfficialBadge status={v.official_status} />}
                      <ExtractionBadge e={e} />
                      <ApprovalBadge e={v?.extractions.find((x) => x.active) ?? e} />
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{textCoverage(e)}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
      <p className="text-muted-foreground text-xs">
        文字抽出率は、文字を取り出せたページの割合です（正しさの指標ではありません）。画像だけのページはOCRで読み取りません。
      </p>
    </div>
  )
}
