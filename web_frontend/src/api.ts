// Thin client for the read-only research API (api/main.py).
// Base URL from VITE_API_BASE_URL, defaulting to the local backend.

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export type ResearchState =
  | 'complete'
  | 'pending'
  | 'invalid_ticker'
  | 'insufficient_data'
  | 'error'

export interface Financials {
  pe?: unknown
  pb?: unknown
  market_cap?: unknown
  quality_score?: unknown
  growth_score?: unknown
  balance_sheet_score?: unknown
  valuation_score?: unknown
}

export interface ResearchResult {
  ok: boolean
  state: ResearchState
  ticker: string
  canonical?: string | null
  company_name?: string
  market?: string | null
  total_score?: number | null
  final_score_preview?: number | null
  research_priority?: string
  research_priority_note?: string
  ai_rating?: string | null
  strengths?: string[]
  risks?: string[]
  missing_fields?: string[]
  financials?: Financials
  message?: string
  raw?: Record<string, unknown>
}

export async function fetchResearch(ticker: string): Promise<ResearchResult> {
  const url = `${API_BASE}/api/research?ticker=${encodeURIComponent(ticker)}`
  try {
    const res = await fetch(url)
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`HTTP_${res.status}:${text.slice(0, 200)}`)
    }
    const data = (await res.json()) as ResearchResult
    return data
  } catch (err) {
    console.error('[API] fetchResearch failed:', err)
    throw err
  }
}

export async function checkHealth(): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/health`)
  return (await res.json()) as { ok: boolean }
}
