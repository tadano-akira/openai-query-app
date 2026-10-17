export type Citation = { url: string; title: string | null; start_index: number; end_index: number }

export type SearchCall = {
  id: string
  status: string
  action: string | null
  query: string | null
  queries: string[] | null
  url: string | null
  sources: { url: string }[]
}

export type SearchInfo = { executed: boolean; call_count: number; calls: SearchCall[] }

export type Usage = {
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cached_input_tokens: number | null
  reasoning_tokens: number | null
}

export type Timings = {
  backend_duration_ms: number | null
  openai_duration_ms: number | null
  db_duration_ms: number | null
  client_duration_ms: number | null
}

export type Run = {
  conversation_id: string
  turn_id: string
  request_id: string
  message_id: string | null
  retry_of_run_id: string | null
  status: 'running' | 'succeeded' | 'failed' | 'interrupted'
  outcome: string | null
  persisted: boolean
  prompt: string
  answer: string | null
  citations: Citation[]
  web_search_enabled: boolean
  search_url: string | null
  searched_domain: string | null
  search_info: SearchInfo | null
  model: string
  /** 回答スタイルのモード ID（prompts/modes/<id>.md） */
  mode: string
  prompt_version: string
  context_message_count: number
  created_at: string
  generated_at: string | null
  usage: Usage
  /** モデル別単価による推定料金（USD）。単価未定義のモデルは null */
  estimated_cost_usd: number | null
  timings: Timings
  error: { code: string; message: string | null } | null
  openai_response_id: string | null
  langfuse_trace_id: string | null
  langfuse_status: string | null
  langfuse_url: string | null
}

export type ApiCall = {
  id: string
  attempt: number
  model: string
  status: string
  started_at: string
  duration_ms: number | null
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cached_input_tokens: number | null
  reasoning_tokens: number | null
  openai_response_id: string | null
  error_code: string | null
  error_message: string | null
}

export type RunDetail = Run & {
  request_snapshot: Record<string, unknown> | null
  context_message_ids: string[]
  api_calls: ApiCall[]
}

export type Turn = {
  id: string
  seq: number
  user_message: { id: string; content: string; created_at: string } | null
  adopted_message_id: string | null
  runs: Run[]
  is_latest: boolean
}

export type ConversationStats = {
  run_count: number
  succeeded_count: number
  failed_count: number
  api_call_count: number
  unknown_usage_call_count: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  estimated_cost_usd: number
  unknown_cost_run_count: number
  avg_client_duration_ms: number | null
  client_duration_sample_count: number
  avg_openai_duration_ms: number | null
  openai_duration_sample_count: number
  total_backend_duration_ms: number
  elapsed_ms: number | null
}

export type ConversationSummary = {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  total_tokens: number
  unknown_usage_call_count: number
  models: string[]
}

export type ConversationDetail = {
  id: string
  title: string
  created_at: string
  updated_at: string
  models: string[]
  turns: Turn[]
  stats: ConversationStats
}

export type UsagePeriod = {
  run_count: number
  run_limit: number | null
  cost_usd: number
  cost_limit_usd: number | null
}

export type UsageSummary = {
  timezone: string
  today: UsagePeriod
  month: UsagePeriod
  unknown_cost_run_count: number
}

export type Mode = {
  id: string
  label: string
  description: string
  verbosity: string | null
  reasoning_effort: string | null
}

export type ModeStat = {
  mode: string
  model: string
  web_search_enabled: boolean
  run_count: number
  avg_input_tokens: number | null
  avg_output_tokens: number | null
  avg_reasoning_tokens: number | null
  avg_answer_chars: number | null
  avg_cost_usd: number | null
  avg_openai_duration_ms: number | null
}

export type LangfuseStatus = {
  trace_id: string | null
  status: string | null
  reflected: boolean | null
  url: string | null
}

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError(0, 'network_error', 'バックエンドに接続できません')
  }
  if (res.status === 204) return undefined as T
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = body?.detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      throw new ApiError(res.status, detail.code ?? 'error', detail.message ?? '不明なエラー')
    }
    if (Array.isArray(detail)) {
      const msg = detail.map((d: { msg: string }) => d.msg.replace(/^Value error, /, '')).join(' / ')
      throw new ApiError(res.status, 'validation_error', msg)
    }
    throw new ApiError(res.status, 'http_error', `HTTP ${res.status}`)
  }
  return body as T
}

export const api = {
  getUsage: () => request<UsageSummary>('/api/v1/usage'),
  getCharacter: () => request<{ name: string; avatar_url: string | null; portrait_url: string | null }>('/api/v1/character'),
  listModes: () => request<{ modes: Mode[]; default: string }>('/api/v1/modes'),
  getModeStats: () => request<ModeStat[]>('/api/v1/stats/modes'),
  listModels: () => request<{ models: string[]; default: string }>('/api/v1/models'),
  listConversations: (offset = 0, limit = 30) =>
    request<{ items: ConversationSummary[]; total: number; limit: number; offset: number }>(
      `/api/v1/conversations?limit=${limit}&offset=${offset}`,
    ),
  createConversation: () =>
    request<ConversationSummary>('/api/v1/conversations', { method: 'POST', body: '{}' }),
  getConversation: (id: string) => request<ConversationDetail>(`/api/v1/conversations/${id}`),
  hideConversation: (id: string) => request<void>(`/api/v1/conversations/${id}`, { method: 'DELETE' }),
  sendMessage: (
    id: string,
    body: {
      prompt: string
      web_search_enabled: boolean
      search_url: string | null
      model: string | null
      mode: string | null
    },
  ) =>
    request<Run>(`/api/v1/conversations/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  retryTurn: (turnId: string, model: string | null, mode: string | null) =>
    request<Run>(`/api/v1/turns/${turnId}/retry`, { method: 'POST', body: JSON.stringify({ model, mode }) }),
  getRun: (id: string) => request<RunDetail>(`/api/v1/runs/${id}`),
  putClientMetrics: (id: string, ms: number) =>
    request<void>(`/api/v1/runs/${id}/client-metrics`, {
      method: 'PATCH',
      body: JSON.stringify({ client_duration_ms: Math.round(ms) }),
    }),
  getLangfuseStatus: (id: string) => request<LangfuseStatus>(`/api/v1/runs/${id}/langfuse`),
}
