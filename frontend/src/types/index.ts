// ── Auth ───────────────────────────────────────────────────────────────────────

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  tenant_id: string
  name: string
  email: string
  password: string
  plan: 'free' | 'standard' | 'enterprise'
}

export interface TokenResponse {
  access_token: string
  token_type: string
  tenant_id: string
  name: string
  plan: string
}

export interface AuthState {
  token: string
  tenant_id: string
  name: string
  plan: string
}

// ── Documents ─────────────────────────────────────────────────────────────────

export type ExtractionStatus = 'queued' | 'extracting' | 'indexed' | 'failed'

export interface DocumentUploadResponse {
  doc_id: string
  status: ExtractionStatus
  file_name: string
  file_size_bytes: number
}

export interface DocumentDetail {
  id: string
  tenant_id: string
  file_name: string | null
  mime_type: string | null
  s3_key: string | null
  file_size_bytes: number
  extraction_status: ExtractionStatus
  extraction_error: string | null
  page_count: number | null
  word_count: number | null
  title: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
  download_url: string | null
}

// ── Search ────────────────────────────────────────────────────────────────────

export interface SearchResultItem {
  doc_id: string
  file_name: string
  snippet: string
  page_hint: number
  score: float
  download_url: string
  extraction_status: ExtractionStatus
}

export interface SearchResponse {
  results: SearchResultItem[]
  total: number
  query_time_ms: number
  query: string
  tenant_id: string
  cached: boolean
}

// ── Local state ───────────────────────────────────────────────────────────────

export interface RecentUpload {
  doc_id: string
  file_name: string
  status: ExtractionStatus
  title?: string
  file_size_bytes: number
  uploaded_at: string
}

// TypeScript hack — "float" is just an alias for number
type float = number
