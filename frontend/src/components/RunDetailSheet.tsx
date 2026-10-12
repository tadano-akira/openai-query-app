import { useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api } from '@/lib/api'
import { errorLabel, formatDate, formatMs, formatTokens } from '@/lib/format'

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1 text-sm">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="text-right font-mono break-all">{value}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

export function RunDetailSheet({
  runId,
  onOpenChange,
}: {
  runId: string | null
  onOpenChange: (open: boolean) => void
}) {
  const { data: run, isLoading } = useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.getRun(runId!),
    enabled: !!runId,
  })
  const langfuse = useQuery({
    queryKey: ['run-langfuse', runId],
    queryFn: () => api.getLangfuseStatus(runId!),
    enabled: !!runId && !!run?.langfuse_trace_id,
    // 反映は非同期のため、未反映の間だけ再確認する
    refetchInterval: (q) => (q.state.data?.reflected === false ? 3000 : false),
  })

  return (
    <Sheet open={!!runId} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>計測詳細</SheetTitle>
          <SheetDescription className="font-mono text-xs break-all">{runId}</SheetDescription>
        </SheetHeader>
        {isLoading || !run ? (
          <p className="text-muted-foreground px-4 text-sm">読み込み中…</p>
        ) : (
          <div className="space-y-5 px-4 pb-6">
            <Section title="処理時間">
              <Row label="画面上の待ち時間" value={formatMs(run.timings.client_duration_ms)} />
              <Row label="バックエンド処理時間" value={formatMs(run.timings.backend_duration_ms)} />
              <Row label="OpenAI API 所要時間（合計）" value={formatMs(run.timings.openai_duration_ms)} />
              <Row label="DB 保存時間（合計）" value={formatMs(run.timings.db_duration_ms)} />
              <p className="text-muted-foreground text-xs">
                各時間は重なりを含むため、合計は画面上の待ち時間と一致しません。API所要時間はネットワーク等の待ちを含みます。
              </p>
            </Section>
            <Separator />
            <Section title="使用量（API返却値）">
              <Row label="入力トークン" value={formatTokens(run.usage.input_tokens)} />
              <Row label="　うちキャッシュ済み" value={formatTokens(run.usage.cached_input_tokens)} />
              <Row label="出力トークン" value={formatTokens(run.usage.output_tokens)} />
              <Row label="　うち推論" value={formatTokens(run.usage.reasoning_tokens)} />
              <Row label="合計トークン" value={formatTokens(run.usage.total_tokens)} />
              <p className="text-muted-foreground text-xs">
                入力トークンにはシステム指示と過去の文脈を含みます。
              </p>
            </Section>
            <Separator />
            <Section title="実行条件・文脈">
              <Row label="状態" value={run.status} />
              <Row label="モデル" value={run.model} />
              <Row label="プロンプト設定" value={run.prompt_version} />
              <Row label="Web検索" value={run.web_search_enabled ? 'ON' : 'OFF'} />
              <Row label="検索先ドメイン" value={run.searched_domain ?? '制限なし'} />
              <Row
                label="検索ツール実行"
                value={
                  run.search_info
                    ? `${run.search_info.executed ? 'あり' : 'なし'}（${run.search_info.call_count}回）`
                    : '—'
                }
              />
              <Row label="文脈メッセージ数" value={run.context_message_ids.length} />
              <Row label="再実行元" value={run.retry_of_run_id ?? '—'} />
              <Row label="作成" value={formatDate(run.created_at)} />
              <Row label="回答生成" value={formatDate(run.generated_at)} />
              <Row label="OpenAI 応答ID" value={run.openai_response_id ?? '—'} />
            </Section>
            {run.error && (
              <>
                <Separator />
                <Section title="エラー">
                  <Row label="区分" value={`${errorLabel(run.error.code)} (${run.error.code})`} />
                  <p className="text-destructive text-sm">{run.error.message}</p>
                </Section>
              </>
            )}
            <Separator />
            <Section title={`API 呼び出し（${run.api_calls.length}件）`}>
              {run.api_calls.length === 0 ? (
                <p className="text-muted-foreground text-sm">呼び出しなし</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>#</TableHead>
                      <TableHead>状態</TableHead>
                      <TableHead className="text-right">所要</TableHead>
                      <TableHead className="text-right">入力</TableHead>
                      <TableHead className="text-right">出力</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {run.api_calls.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell>{c.attempt}</TableCell>
                        <TableCell>
                          {c.status}
                          {c.error_code && (
                            <span className="text-destructive ml-1 text-xs">{errorLabel(c.error_code)}</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">{formatMs(c.duration_ms)}</TableCell>
                        <TableCell className="text-right">{formatTokens(c.input_tokens)}</TableCell>
                        <TableCell className="text-right">{formatTokens(c.output_tokens)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Section>
            <Separator />
            <Section title="Langfuse">
              {!run.langfuse_trace_id ? (
                <p className="text-muted-foreground text-sm">
                  トレース未送信（{run.langfuse_status === 'disabled' ? '未設定' : '送信失敗'}）
                </p>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  {langfuse.data?.reflected === true && <Badge>反映済み</Badge>}
                  {langfuse.data?.reflected === false && <Badge variant="secondary">送信済み・未反映</Badge>}
                  {langfuse.data && langfuse.data.reflected === null && (
                    <Badge variant="outline">反映状況を確認できません</Badge>
                  )}
                  {run.langfuse_url && (
                    <Button asChild variant="outline" size="sm">
                      <a href={run.langfuse_url} target="_blank" rel="noopener noreferrer">
                        トレースを開く <ExternalLink />
                      </a>
                    </Button>
                  )}
                </div>
              )}
            </Section>
            <Separator />
            <Section title="送信内容のスナップショット">
              <pre className="bg-muted max-h-80 overflow-auto rounded-md p-2 text-xs whitespace-pre-wrap">
                {JSON.stringify(run.request_snapshot, null, 2)}
              </pre>
            </Section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
