// Thin client for the read-only research API (api/main.py).
// Base URL from VITE_API_BASE_URL, defaulting to the local backend.

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export type ResearchState =
  | 'complete'
  | 'pending'
  | 'invalid_ticker'
  | 'insufficient_data'
  | 'error'

// 前端请求生命周期状态（UI 层状态机，区别于上面的 API ResearchState）。
// 统一在此 export，App.tsx 与 ResultPreview.tsx 共用，避免两处内联重复定义而漂移。
export type Status = 'idle' | 'loading' | 'done' | 'error'

export interface Financials {
  pe?: unknown
  pb?: unknown
  market_cap?: unknown
  quality_score?: unknown
  growth_score?: unknown
  balance_sheet_score?: unknown
  valuation_score?: unknown
}

// ai_evidence_v1 评分方法（前端据此区分真实 AI / 开发 mock / 不可用）。
export type ScoringMethod = 'ai_llm' | 'ai_mock' | 'unavailable'

// 7 维 rubric 分项（缺失维度可能为 null）。
export interface AiBreakdown {
  business_quality?: number | null
  growth?: number | null
  balance_sheet?: number | null
  valuation?: number | null
  moat?: number | null
  management_governance?: number | null
  data_quality_adjustment?: number | null
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

  // ── ai_evidence_v1：AI 证据评分（主分链路）──
  rating?: string | null
  confidence?: number | null
  summary?: string
  ai_generated?: boolean
  scoring_method?: ScoringMethod
  scoring_rubric_version?: string | null
  validator_status?: string | null
  generated_at?: string | null
  evidence_packet_id?: string | null
  ai_breakdown?: AiBreakdown | null
  score_drivers?: string[]
  missing_data_impact?: string
  source_dates?: Record<string, string | null> | null
  stale_fields?: string[]
  warnings?: string[]
  disclaimer?: string
  data_confidence?: number | null
}

export async function fetchResearch(
  ticker: string,
  opts?: { signal?: AbortSignal; timeoutMs?: number },
): Promise<ResearchResult> {
  const url = `${API_BASE}/api/research?ticker=${encodeURIComponent(ticker)}`
  const timeoutMs = opts?.timeoutMs ?? 20000

  // 内部 controller：超时与外部 signal 都汇聚到它，统一中止 fetch。
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  // 外部 signal（请求被取代 / 组件卸载）→ 转发到内部 controller。
  const external = opts?.signal
  const onExternalAbort = () => controller.abort()
  if (external) {
    if (external.aborted) controller.abort()
    else external.addEventListener('abort', onExternalAbort)
  }

  try {
    const res = await fetch(url, { signal: controller.signal })
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`HTTP_${res.status}:${text.slice(0, 200)}`)
    }
    const data = (await res.json()) as ResearchResult
    return data
  } catch (err) {
    // 超时：抛出可识别错误，供 App 显示超时文案。
    if (timedOut) throw new Error('TIMEOUT')
    // 外部主动中止：保留 AbortError 语义，让 App 静默忽略（不打印噪音）。
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      console.error('[API] fetchResearch failed:', err)
    }
    throw err
  } finally {
    clearTimeout(timer)
    if (external) external.removeEventListener('abort', onExternalAbort)
  }
}

export async function checkHealth(): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/health`)
  return (await res.json()) as { ok: boolean }
}
