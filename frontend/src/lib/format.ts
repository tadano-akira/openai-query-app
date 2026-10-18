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

/** 推定料金。少額でも 0 に丸めないよう桁数を変える */
export function formatUsd(usd: number | null | undefined): string {
  if (usd == null) return '不明'
  if (usd === 0) return '$0'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  if (usd < 1) return `$${usd.toFixed(3)}`
  return `$${usd.toFixed(2)}`
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
  usage_limit_exceeded: '利用上限',
  secret_detected: '機密情報を検知したため送信を中止しました',
  secret_scan_unavailable: '機密情報の検査ができません',
  invalid_model: '選択できないモデル',
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
