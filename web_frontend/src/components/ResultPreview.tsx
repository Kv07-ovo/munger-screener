import { useState, useEffect } from 'react'
import type { ResearchResult } from '../api'
import ExampleChips from './ExampleChips'
import MobileResultView from './MobileResultView'
import AiEvidence from './AiEvidence'

// 仅移动端把「完整分析」折叠为 <details>；桌面端原生 open 直接展开（避免 Chrome
// ::details-content 在“强制展开关闭态”时的隐藏问题）。
function useIsMobile(query = '(max-width: 680px)') {
  const [m, setM] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
  )
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = () => setM(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return m
}

interface Props {
  status: 'idle' | 'loading' | 'done' | 'error'
  result: ResearchResult | null
  errorMsg: string
  ticker: string
  onPick: (t: string) => void
}

const ERROR_CHIPS = ['AAPL', 'MSFT', '600519.SH']

// Core financial metrics shown in the sidebar grid (api Financials shape). Missing
// values render as a dashed「待补录」cell rather than disappearing.
const METRICS: { key: keyof NonNullable<ResearchResult['financials']>; label: string }[] = [
  { key: 'pe', label: 'PE' },
  { key: 'pb', label: 'PB' },
  { key: 'market_cap', label: '市值' },
  { key: 'quality_score', label: '质量分' },
  { key: 'growth_score', label: '成长分' },
  { key: 'balance_sheet_score', label: '资产负债' },
  { key: 'valuation_score', label: '估值分' },
]

// Format a financial cell; null means「待补录」(missing / empty / non-finite).
function fmtMetric(v: unknown): string | null {
  if (v == null) return null
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return null
    return Number.isInteger(v) ? String(v) : v.toFixed(1)
  }
  const s = String(v).trim()
  return s === '' || s === '—' ? null : s
}

function Chevron() {
  return (
    <svg className="rp-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  )
}

// MVP two-column result page (桌面端6/kv_5·kv_6, 移动端7/kv_5·kv_6). Reads the API
// `state` flag so error / insufficient / pending / complete all render safely & calmly.
export default function ResultPreview({ status, result, errorMsg, ticker, onPick }: Props) {
  const isMobile = useIsMobile()
  if (status === 'idle') return null

  if (status === 'loading') {
    return (
      <div className="loading" role="status" aria-live="polite" aria-busy="true">
        <p className="loading-title">小猫正在分析 {ticker}…</p>
        <p className="loading-sub">正在读取财务数据、规则评分和 AI 初判</p>
        <div className="sk-card" aria-hidden="true">
          <div className="sk-bar sk-name" />
          <div className="sk-bar sk-score" />
          <div className="sk-bar sk-line-1" />
          <div className="sk-bar sk-line-2" />
          <div className="sk-bar sk-line-3" />
        </div>
      </div>
    )
  }

  if (status === 'error') {
    // transport failure (backend down) — distinct from a structured API error
    return (
      <div className="result-card is-warn">
        <p className="state-title">连接出错</p>
        <p className="state-sub">{errorMsg}</p>
      </div>
    )
  }

  if (!result) return null

  // ---- structured API errors ----
  if (!result.ok) {
    if (result.state === 'invalid_ticker') {
      return (
        <div className="state-block">
          <div className="result-card is-warn">
            <p className="state-title">小猫没找到这个股票代码</p>
            <p className="state-sub">请检查代码格式，或试试下面的示例</p>
          </div>
          <ExampleChips tickers={ERROR_CHIPS} onPick={onPick} />
        </div>
      )
    }
    // insufficient_data
    return (
      <div className="state-block">
        <div className="result-card is-muted nodata">
          <div className="nodata-icon" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <ellipse cx="12" cy="6" rx="8" ry="3" />
              <path d="M4 6v6c0 1.66 3.58 3 8 3s8-1.34 8-3V6" />
              <path d="M4 12v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
              <line x1="4" y1="4" x2="20" y2="20" />
            </svg>
          </div>
          <p className="state-title">暂时没有足够数据</p>
          <p className="state-sub">自动抓取未能获得完整财务数据，当前无法生成可靠评分。换个代码或稍后再试。</p>
        </div>
        <ExampleChips tickers={ERROR_CHIPS} onPick={onPick} />
      </div>
    )
  }

  // ---- ok: complete or pending ----
  const pending = result.state === 'pending'
  const canonical = result.canonical ?? result.ticker
  const missing = result.missing_fields ?? []
  const fin = result.financials ?? {}
  const legacyRating = result.ai_rating || ''
  const sub = [result.company_name, result.market]
    .filter((s): s is string => Boolean(s) && s !== canonical)
    .join(' · ')

  // ===== 移动端：阅读型研究摘要（独立组件，不再复用旧 rp-m 报表结构）=====
  if (isMobile) return <MobileResultView result={result} />

  return (
    <div className="result-page">
      {/* ===== Result header：ticker + 公司名 + 市场 + 状态徽章 ===== */}
      <div className="rp-header">
        <div className="rp-header-left">
          <h2 className="rp-ticker">{canonical}</h2>
          {sub && <p className="rp-sub">{sub}</p>}
        </div>
        <span className={`rp-badge ${pending ? 'is-pending' : 'is-ok'}`}>
          {pending ? '部分字段缺失' : '数据完整'}
        </span>
      </div>

      {/* ===== 主列：研究优先级 + AI 证据评分（ai_evidence_v1 主叙事）===== */}
      <div className="result-main">
        {result.research_priority && (
          <section className="rp-card">
            <div className="rp-card-title">研究优先级</div>
            <div className="rp-priority">{result.research_priority}</div>
            {result.research_priority_note && (
              <p className="rp-note">{result.research_priority_note}</p>
            )}
          </section>
        )}

        <AiEvidence result={result} />
      </div>

      {/* ===== 右侧：参考数据（数据状态 + 规则分/原始财务，仅参考，非主分）=====
          移动端折叠为「参考数据」；桌面端由 CSS 强制展开为右侧栏，结构不变。 */}
      <aside className="result-side">
       <details className="rp-full" open={!isMobile}>
        <summary className="rp-full-sum"><span>参考数据</span><Chevron /></summary>
        <div className="rp-full-body">
        <section className="rp-card">
          <div className="rp-card-label">数据状态</div>
          <div className="rp-status-main">{pending ? '部分待补录' : '完整'}</div>
          <p className="rp-status-sub">
            {pending
              ? (missing.length > 0 ? `缺失 ${missing.length} 项关键字段` : '部分字段待补录')
              : '关键字段齐全'}
          </p>
          <p className="rp-status-sub" style={{marginTop: 2}}>自动抓取 + 本地补录</p>
        </section>

        {legacyRating && (
          <section className="rp-card">
            <div className="rp-card-label">规则参考评级</div>
            <div className="rp-kv-list">
              <div className="rp-kv">
                <span className="rp-kv-k">规则初判</span>
                <span className="rp-kv-v">{legacyRating}</span>
              </div>
            </div>
          </section>
        )}

        <section className="rp-card">
          <div className="rp-card-label">核心财务 / 规则分（参考）</div>
          <div className="rp-fin-grid">
            {METRICS.map((m) => {
              const val = fmtMetric(fin[m.key])
              return (
                <div key={m.key} className={`rp-fin ${val == null ? 'is-missing' : ''}`}>
                  <div className="rp-fin-label">{m.label}</div>
                  <div className="rp-fin-val">{val ?? '待补录'}</div>
                </div>
              )
            })}
          </div>
        </section>
        </div>
       </details>
      </aside>

      <p className="rp-disclaimer">
        右侧规则分与原始财务仅作数据参考，非评分主依据。
      </p>
    </div>
  )
}
