import type { ResearchResult } from '../api'
import ExampleChips from './ExampleChips'

interface Props {
  status: 'idle' | 'loading' | 'done' | 'error'
  result: ResearchResult | null
  errorMsg: string
  ticker: string
  onPick: (t: string) => void
}

const ERROR_CHIPS = ['AAPL', 'MSFT', '600519.SH']

// Productionized basic preview (Phase 3): NOT the full 1:1 result page. Reads the API
// `state` flag so error / insufficient / pending / complete all render safely & calmly.
export default function ResultPreview({ status, result, errorMsg, ticker, onPick }: Props) {
  if (status === 'idle') return null

  if (status === 'loading') {
    return (
      <div className="loading">
        <p className="loading-title">小猫正在分析 {ticker}…</p>
        <p className="loading-sub">正在读取财务数据、规则评分和 AI 初判</p>
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
    // insufficient_data — calm/neutral, never alarming ("不能像公司差")
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
  const score = result.total_score != null ? Math.round(Number(result.total_score)) : null
  const strengths = (result.strengths ?? []).slice(0, 5)
  const risks = (result.risks ?? []).slice(0, 3)

  return (
    <div className="result-card">
      <div className="result-head">
        <span className="result-ticker">{result.canonical ?? result.ticker}</span>
        {result.company_name && <span className="result-name">{result.company_name}</span>}
      </div>

      <div className="score-block">
        <div className="score-label">规则总分</div>
        {!pending && score != null ? (
          <>
            <div className="score">
              <span className="score-num">{score}</span>
              <span className="score-max">/ 100</span>
            </div>
            <div className="score-cap">基于财务质量、估值、安全边际与成长性规则评分</div>
          </>
        ) : (
          <div className="score-cap">数据不足（待补录），暂不显示评分</div>
        )}
      </div>

      {result.research_priority && (
        <section className="result-section">
          <div className="section-label">研究优先级</div>
          <div className="priority-value">{result.research_priority}</div>
          {result.research_priority_note && (
            <p className="priority-note">{result.research_priority_note}</p>
          )}
        </section>
      )}

      {strengths.length > 0 && (
        <section className="result-section">
          <div className="section-label">关键优势</div>
          <ul className="dot-list strengths">
            {strengths.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </section>
      )}

      {risks.length > 0 && (
        <section className="result-section">
          <div className="section-label">主要风险</div>
          <ul className="dot-list risks">
            {risks.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </section>
      )}

      <p className="result-disclaimer">
        基础预览（架构闭环用，非完整结果页）。研究优先级仅表示「值得进一步研究的程度」，不构成投资建议。
      </p>
    </div>
  )
}
