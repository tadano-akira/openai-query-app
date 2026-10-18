/** 資料Q&A（PDF 登録・抽出・レビュー・根拠表示）の API とラベル */
import { request } from '@/lib/api'

export type Issuer = { id: string; name: string; code: string | null }

export type Extraction = {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  quality: 'ok' | 'needs_review' | 'partial' | 'unsupported_no_text' | null
  stats: {
    pages: number
    chars: number
    lines: number
    tables: number
    cells: number
    page_quality: Record<string, number>
  } | null
  error_code: string | null
  error_message: string | null
  parser_version: string
  approved_at: string | null
  approved_by: string | null
  approval_memo: string | null
  active: boolean
  created_at: string
  completed_at: string | null
  index_status: 'none' | 'queued' | 'running' | 'ready' | 'failed'
  index_error: string | null
  index_model: string | null
  indexed_at: string | null
}

export type DocVersion = {
  id: string
  document_id: string
  sha256: string
  file_size: number
  original_filename: string | null
  page_count: number | null
  source_url: string | null
  fetched_at: string
  published_on: string | null
  period_start: string | null
  period_end: string | null
  period_label: string | null
  scope: string
  accounting_standard: string | null
  currency: string | null
  official_status: 'unconfirmed' | 'confirmed' | 'rejected'
  official_confirmed_by: string | null
  official_confirmed_at: string | null
  official_memo: string | null
  supersedes_id: string | null
  correction_reason: string | null
  created_at: string
  extractions: Extraction[]
}

export type Doc = {
  id: string
  issuer: Issuer
  title: string
  document_type: string
  created_at: string
  versions: DocVersion[]
}

export type Quad = [number, number][]

export type DocElement = {
  id: string
  kind: 'line' | 'table' | 'cell'
  block_seq: number | null
  line_seq: number | null
  text: string
  quads: Quad[]
}

export type DocCell = DocElement & { row: number; col: number; header_ids: string[] }
export type DocTable = DocElement & { cells: DocCell[] }

export type PageSummary = {
  page_number: number
  pdf_page_label: string | null
  printed_page_label: string | null
  quality: 'ok' | 'blank' | 'non_text' | 'needs_review'
  char_count: number
  image_count: number
  line_count: number
  table_count: number
}

export type PageDetail = {
  extraction_id: string
  version_id: string
  page_number: number
  page_count: number
  pdf_page_label: string | null
  printed_page_label: string | null
  quality: PageSummary['quality']
  geometry: { rotation: number; view: number[]; mediabox: number[]; cropbox: number[] }
  lines: DocElement[]
  tables: DocTable[]
}

export type Evidence = {
  element_id: string
  kind: DocElement['kind']
  text: string
  page_number: number
  pdf_page_label: string | null
  printed_page_label: string | null
  line_seq: number | null
  quads: Quad[]
  related: DocElement[]
  table_id: string | null
  row: number | null
  col: number | null
  extraction_id: string
  extraction_approved: boolean
  version_id: string
  sha256: string
  document_id: string
  document_title: string
  issuer_name: string
  period_label: string | null
  viewer_url: string
}

export type SearchHit = {
  rank: number
  chunk_id: string
  kind: 'text' | 'table'
  heading: string | null
  text: string
  page_number: number
  pdf_page_label: string | null
  extraction_id: string
  version_id: string
  document_title: string
  issuer_name: string
  period_label: string | null
  lexical_rank: number | null
  lexical_score: number | null
  vector_rank: number | null
  vector_score: number | null
  rrf_score: number
  evidence: { element_id: string; role: string; text: string }[]
}

export type SearchScope = {
  extraction_id: string
  version_id: string
  title: string
  issuer: string
  period_label: string | null
  issuer_id?: string
}

export type AnswerStatus =
  | 'running'
  | 'completed'
  | 'insufficient_evidence'
  | 'needs_clarification'
  | 'needs_review'
  | 'conflict'
  | 'failed'
  | 'cancelled'

export type AnswerItem = {
  n: number
  kind: 'value' | 'text'
  value: string
  unit: string | null
  row: string | null
  column: string | null
  title: string | null
  page_number: number
  note: string
  evidence_id: string
  context_ids: string[]
  viewer_url: string
  document_title?: string
  issuer?: string
  period_label?: string | null
  version_id?: string
  context_before?: string
  context_after?: string
  fact: { id: string; status: 'verified' | 'unverified'; unit: string | null; issues: string[] } | null
}

export type MetricPolicyInfo = {
  metric_key: string
  name: string
  basis: string | null
  basis_label: string | null
  policy_version: string | null
  description: string | null
  items: string[]
  approved_on: string | null
}

export type CalculationView = {
  operation: 'sum' | 'subtract'
  claim_numbers: number[]
  expression: string
  status: 'computed' | 'unverified'
  inputs: {
    n: number
    fact_id: string | null
    raw_value: string
    unit: string | null
    normalized_value: string | null
    period_label: string | null
    label: string | null
    status: string
  }[]
  result_display?: string
  unit?: string | null
  result_base?: string
  currency?: string | null
  rounding?: { mode: string; places: number; applied: boolean }
  notes?: string[]
  policy: MetricPolicyInfo | null
}

export type AnswerResult = {
  answer_kind: string | null
  items: AnswerItem[]
  calculation: CalculationView | null
  review_reasons?: string[]
  policy?: MetricPolicyInfo | null
  clarification: string | null
  missing: string[]
  scope: SearchScope[]
  mentioned_issuers?: string[]
}

export type AnswerRunSummary = {
  id: string
  question: string
  status: AnswerStatus
  answer_kind: string | null
  model: string
  created_at: string
}

export type AnswerRun = AnswerRunSummary & {
  stage: string
  prompt_version: string
  search_run_id: string | null
  result: AnswerResult | null
  candidates: { ref: string; element_id: string; kind: string; page_number: number; text: string; row: string | null }[]
  llm_outputs: { attempt: number; output: unknown }[]
  validation_errors: { attempt: number; errors: string[] }[]
  error_code: string | null
  error_message: string | null
  timings: Record<string, number>
  completed_at: string | null
  langfuse_url: string | null
  estimated_cost_usd: number | null
}

export type SearchRun = {
  id: string
  question: string
  status: 'succeeded' | 'no_scope' | 'running'
  scope: SearchScope[]
  expanded_terms: string[]
  embedding_model: string | null
  timings: { total_ms?: number; lexical_ms?: number; embedding_ms?: number; vector_ms?: number; terms?: string[] }
  created_at: string
  hits: SearchHit[]
}

export type ChunkDetail = {
  chunk_id: string
  kind: 'text' | 'table'
  heading: string | null
  text: string
  page_number: number
  extraction_id: string
  version_id: string
  elements: DocElement[]
  roles: Record<string, string>
}

export const INDEX_LABELS: Record<string, string> = {
  none: '索引なし',
  queued: '索引作成待ち',
  running: '索引作成中',
  ready: '索引作成済み',
  failed: '索引作成失敗',
}

export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  tanshin_q1: '第1四半期決算短信',
  tanshin_q2: '第2四半期（中間期）決算短信',
  tanshin_q3: '第3四半期決算短信',
  tanshin_fy: '通期決算短信',
  securities_report: '有価証券報告書',
  semiannual_report: '半期報告書',
  quarterly_report: '四半期報告書（制度廃止前）',
  other: 'その他',
}

export const SCOPE_LABELS: Record<string, string> = {
  consolidated: '連結',
  standalone: '単体',
  mixed: '混在',
  unknown: '不明',
}

export const OFFICIAL_LABELS: Record<string, string> = {
  unconfirmed: '未確認',
  confirmed: '確認済み',
  rejected: '却下',
}

export const EXTRACTION_STATUS_LABELS: Record<string, string> = {
  queued: '解析待ち',
  running: '解析中',
  succeeded: '解析完了',
  failed: '解析失敗',
}

export const QUALITY_LABELS: Record<string, string> = {
  ok: '良好',
  needs_review: '要確認',
  partial: '一部抽出不可',
  unsupported_no_text: '文字抽出不可（OCR非対応）',
  blank: '空白',
  non_text: '文字なし',
}

/** 文字抽出率 = 検索に使えるページ数 / 総ページ数（正答率ではない） */
export function textCoverage(e: Extraction | undefined): string {
  const s = e?.stats
  if (!s || !s.pages) return '—'
  const unusable = (s.page_quality.non_text ?? 0) + (s.page_quality.blank ?? 0)
  return `${Math.round(((s.pages - unusable) / s.pages) * 100)}%`
}

export function latestVersion(d: Doc): DocVersion | undefined {
  return d.versions[d.versions.length - 1]
}

export function latestExtraction(v: DocVersion | undefined): Extraction | undefined {
  return v?.extractions[v.extractions.length - 1]
}

export const pdfUrl = (versionId: string) => `/api/v1/document-versions/${versionId}/pdf`

export const docsApi = {
  issuers: () => request<Issuer[]>('/api/v1/issuers'),
  createIssuer: (name: string, code: string | null) =>
    request<Issuer>('/api/v1/issuers', { method: 'POST', body: JSON.stringify({ name, code }) }),
  documents: () => request<Doc[]>('/api/v1/documents'),
  document: (id: string) => request<Doc>(`/api/v1/documents/${id}`),
  upload: (form: FormData) =>
    request<{ document_id: string; version_id: string; extraction_id: string; job_id: string }>(
      '/api/v1/documents',
      { method: 'POST', body: form },
    ),
  version: (id: string) => request<DocVersion>(`/api/v1/document-versions/${id}`),
  patchVersion: (id: string, body: Record<string, string | null>) =>
    request<DocVersion>(`/api/v1/document-versions/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  setOfficial: (id: string, status: DocVersion['official_status'], memo: string | null) =>
    request<DocVersion>(`/api/v1/document-versions/${id}/official`, {
      method: 'POST',
      body: JSON.stringify({ status, memo }),
    }),
  reextract: (id: string) =>
    request<{ extraction_id: string }>(`/api/v1/document-versions/${id}/extract`, { method: 'POST' }),
  deleteDocument: (id: string) => request<void>(`/api/v1/documents/${id}`, { method: 'DELETE' }),
  review: (extractionId: string) =>
    request<{ extraction: Extraction; version_id: string; pages: PageSummary[] }>(
      `/api/v1/extractions/${extractionId}/review`,
    ),
  page: (extractionId: string, page: number) =>
    request<PageDetail>(`/api/v1/extractions/${extractionId}/pages/${page}`),
  approve: (extractionId: string, memo: string | null) =>
    request<Extraction>(`/api/v1/extractions/${extractionId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ memo }),
    }),
  activate: (extractionId: string) =>
    request<Extraction>(`/api/v1/extractions/${extractionId}/activate`, { method: 'POST' }),
  evidence: (elementId: string) => request<Evidence>(`/api/v1/evidence/${elementId}`),
  index: (extractionId: string) =>
    request<Extraction>(`/api/v1/extractions/${extractionId}/index`, { method: 'POST' }),
  search: (question: string, issuerIds: string[]) =>
    request<SearchRun>('/api/v1/search', {
      method: 'POST',
      body: JSON.stringify({ question, issuer_ids: issuerIds }),
    }),
  searchRun: (id: string) => request<SearchRun>(`/api/v1/search-runs/${id}`),
  chunk: (id: string) => request<ChunkDetail>(`/api/v1/chunks/${id}`),
  answer: (question: string, issuerIds: string[], model: string | null) =>
    request<AnswerRun>('/api/v1/answers', {
      method: 'POST',
      body: JSON.stringify({ question, issuer_ids: issuerIds, model }),
    }),
  answerRuns: () => request<AnswerRunSummary[]>('/api/v1/answer-runs'),
  answerRun: (id: string) => request<AnswerRun>(`/api/v1/answer-runs/${id}`),
}
