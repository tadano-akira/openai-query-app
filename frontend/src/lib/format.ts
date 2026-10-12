const dateFmt = new Intl.DateTimeFormat('ja-JP', {
  timeZone: 'Asia/Tokyo',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

export function formatDate(iso: string | null | undefined): string {
  return iso ? dateFmt.format(new Date(iso)) : '—'
}

/** null は「未取得」として扱い、0 と区別する */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return '未取得'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${m}分${s}秒`
}

export function formatTokens(n: number | null | undefined): string {
  return n == null ? '不明' : n.toLocaleString('ja-JP')
}

export const ERROR_LABELS: Record<string, string> = {
  timeout: 'タイムアウト',
  network_error: '通信エラー',
  rate_limited: 'API利用制限',
  quota_exceeded: '利用枠不足',
  auth_error: '認証・権限エラー',
  config_error: '設定不備',
  bad_request: 'リクエスト不正',
  upstream_error: 'OpenAI側障害',
  context_limit_exceeded: '文脈上限超過',
  db_error: 'DB保存失敗',
  interrupted: '中断',
  internal_error: '内部エラー',
  busy: '処理中',
}

export function errorLabel(code: string): string {
  return ERROR_LABELS[code] ?? code
}

export function outcomeLabel(outcome: string | null): string | null {
  if (!outcome || outcome === 'answered') return null
  if (outcome === 'search_not_executed') return '検索未実行'
  if (outcome.startsWith('incomplete')) return `回答が途中で終了 (${outcome.split(':')[1] ?? '不明'})`
  return outcome
}
