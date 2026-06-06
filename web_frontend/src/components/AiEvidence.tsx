import type { ResearchResult, AiBreakdown } from '../api'

// ai_evidence_v1 可信展示组件（桌面 / 移动共享，响应式由 CSS 控制）。
// 纯展示层：只消费后端 adapter 已整理好的字段，绝不展示原始 AI JSON，绝不输出投资建议。
// 三态显式区分：real（真实 LLM）/ mock（开发确定性模拟，非真实 AI）/ unavailable（未生成评分）。
// 所有字段缺失时不崩：每处取值都做空值兜底，缺失维度/字段降级为「—」或省略。

type AiState = 'real' | 'mock' | 'unavailable'

// 判态：仅当方法明确且确有分数时才认定 real/mock，否则一律 unavailable（绝不把 mock 伪装成真实 AI）。
function aiState(r: ResearchResult): AiState {
  const hasScore = r.total_score != null
  if (r.scoring_method === 'ai_llm' && hasScore) return 'real'
  if (r.scoring_method === 'ai_mock' && hasScore) return 'mock'
  return 'unavailable'
}

// 展示分取整（与 rp/mrv 同口径）。
function asScore(v: unknown): number | null {
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? Math.round(n) : null
}

// 比例 [0,1] → 百分比整数；缺失 / 非有限 → null。
function asPct(v: unknown): number | null {
  if (v == null || v === '') return null
  const n = Number(v)
  if (!Number.isFinite(n)) return null
  return Math.round(n * 100)
}

// 单维分数格式（整数直出，小数保留 1 位）。
function fmt1(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}

// generated_at ISO → 「YYYY-MM-DD HH:MM」；非 ISO 文本原样返回；空 → null。
function fmtTime(v: unknown): string | null {
  if (typeof v !== 'string' || !v.trim()) return null
  const s = v.trim()
  const m = s.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return m ? `${m[1]} ${m[2]}` : s
}

// source_dates 单值格式（空值 → 空串；数组取 join；其余转字符串）。
function fmtSrc(v: unknown): string {
  if (v == null) return ''
  if (Array.isArray(v)) return v.map((x) => String(x)).join('/')
  return String(v).trim()
}

// 列表清洗：接受 string[] 或 [{point}] 形态，去空裁剪到 n 条（防编造 / 防对象直出）。
function cleanList(arr: unknown, n: number): string[] {
  if (!Array.isArray(arr)) return []
  const out: string[] = []
  for (const x of arr) {
    let s = ''
    if (typeof x === 'string') s = x.trim()
    else if (x && typeof x === 'object' && 'point' in (x as Record<string, unknown>)) {
      s = String((x as Record<string, unknown>).point ?? '').trim()
    }
    if (s) out.push(s)
    if (out.length >= n) break
  }
  return out
}

// 取一维分项数值（缺失 / 非有限 → null）。
function dimVal(b: AiBreakdown | null | undefined, key: keyof AiBreakdown): number | null {
  if (!b) return null
  const v = b[key]
  if (v == null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// 数据质量调整（±5，可负，不画进度条）：显式带符号。
function adjText(b: AiBreakdown | null | undefined): string {
  const v = dimVal(b, 'data_quality_adjustment')
  if (v == null) return '—'
  const r = Math.round(v * 10) / 10
  return r > 0 ? `+${fmt1(r)}` : fmt1(r)
}

// 评分方法 → 用户可读徽章（必须让 mock / unavailable 一眼可辨，不得伪装真实 AI）。
const METHOD_BADGE: Record<AiState, { label: string; tone: 'ok' | 'warn' | 'muted' }> = {
  real: { label: 'AI 评分', tone: 'ok' },
  mock: { label: '演示评分 · 非真实 AI', tone: 'warn' },
  unavailable: { label: 'AI 不可用', tone: 'muted' },
}

// 七维 rubric 的正向 6 维（含满分上界，用于进度条比例）。data_quality_adjustment 单列处理。
const DIMS: { key: keyof AiBreakdown; label: string; max: number }[] = [
  { key: 'business_quality', label: '生意质量', max: 30 },
  { key: 'growth', label: '成长性', max: 15 },
  { key: 'balance_sheet', label: '资产负债', max: 15 },
  { key: 'valuation', label: '估值', max: 15 },
  { key: 'moat', label: '护城河', max: 15 },
  { key: 'management_governance', label: '管理层与治理', max: 5 },
]

const SRC_LABELS: Record<string, string> = {
  valuation_as_of: '估值',
  fundamentals_as_of: '财务',
  financial_years: '财报年度',
}

const VALIDATOR_LABEL: Record<string, string> = {
  passed: '已校验通过',
  repaired: '已校验（自动修正）',
  failed: '未通过校验',
}

// 内部字段名 / 列名 → 用户可读中文（绝不把工程字段名直接暴露给用户）。
const FIELD_LABELS: Record<string, string> = {
  // 七维
  business_quality: '生意质量', growth: '成长性', balance_sheet: '资产负债',
  valuation: '估值', moat: '护城河', management_governance: '管理层与治理',
  data_quality_adjustment: '数据质量调整',
  // 量化指标
  roe_5y_avg: 'ROE(5年均值)', roic_5y_avg: 'ROIC(5年均值)',
  gross_margin_5y_avg: '毛利率(5年均值)', net_margin_5y_avg: '净利率(5年均值)',
  revenue_growth_5y_cagr: '营收5年复合增速', eps_growth_5y_cagr: 'EPS5年复合增速',
  fcf_positive_years: '自由现金流为正年数', debt_to_equity: '负债权益比',
  pe: '市盈率', fcf_yield: '自由现金流收益率', pe_percentile_5y: '5年估值分位',
  pb: '市净率', market_cap: '市值',
  roe_trend: 'ROE趋势', roic_trend: 'ROIC趋势', margin_trend: '利润率趋势', revenue_trend: '营收趋势',
}

const RUBRIC_LABELS: Record<string, string> = { ai_evidence_v1: 'AI 证据评分 v1' }

// 字段名 → 中文（未知则原样兜底，避免误吞 legacy 已是中文的字段）。
function fieldLabel(f: string): string {
  return FIELD_LABELS[f] || f
}

// 维度读数式 driver（如 "business_quality 25/30（强）"）：与「分项评分」重复且夹带英文 key，过滤掉。
// 字段名 token 允许含数字（roic_5y_avg / pe_percentile_5y 等），故用 [a-z][a-z0-9_]+ 而非 [a-z_]{2,}。
function isDimReadout(s: string): boolean {
  return /^[a-z][a-z0-9_]+\s+-?\d/.test(s.trim())
}

// 把文本中出现的内部字段名整词替换为中文（兜底：处理真实 AI 偶发夹带的英文 key）。
// token 含数字时（如 roic_5y_avg）必须整体匹配，否则会被拆成 roic_/y_avg 而漏译并泄露。
function labelize(s: string): string {
  return s.replace(/[a-z][a-z0-9_]+/g, (m) => FIELD_LABELS[m] || m)
}

const FIXED_DISCLAIMER = 'AI 研究评分仅供研究参考，不构成任何投资建议。'

export default function AiEvidence({ result }: { result: ResearchResult }) {
  const state = aiState(result)
  const unavailable = state === 'unavailable'
  const badge = METHOD_BADGE[state]

  const score = asScore(result.total_score)
  const rating = (result.rating || '').trim()
  const conf = asPct(result.confidence)
  const time = fmtTime(result.generated_at)
  const rubric = (result.scoring_rubric_version || '').trim()

  const summary = (result.summary || '').trim()
  // score_drivers：丢弃「英文维度名+分数」式开发态读数（与分项评分重复），其余中文化后保留
  const drivers = cleanList(result.score_drivers, 6).filter((s) => !isDimReadout(s)).map(labelize)
  const strengths = cleanList(result.strengths, 4).map(labelize)
  const risks = cleanList(result.risks, 4).map(labelize)
  const impact = (result.missing_data_impact || '').trim()

  const dataConf = asPct(result.data_confidence)
  const validator = result.validator_status
    ? VALIDATOR_LABEL[result.validator_status] || result.validator_status
    : null
  const sources =
    result.source_dates && typeof result.source_dates === 'object'
      ? Object.entries(result.source_dates).filter(([, v]) => v != null && fmtSrc(v))
      : []
  const missing = (result.missing_fields ?? []).map((s) => String(s).trim()).filter(Boolean)
  const stale = (result.stale_fields ?? []).map((s) => String(s).trim()).filter(Boolean)

  const hasExplain = !unavailable && (summary || drivers.length || strengths.length || risks.length || impact)

  return (
    <section className="ae" aria-label="AI 证据评分">
      {/* ===== 1) 主评分区 ===== */}
      <div className="ae-hero">
        <div className="ae-hero-main">
          <div className="ae-score">
            {score != null ? (
              <>
                <span className="ae-score-num">{score}</span>
                <span className="ae-score-max">/ 100</span>
              </>
            ) : (
              <span className="ae-score-na">未生成评分</span>
            )}
          </div>
          {score != null && rating && <span className="ae-rating">{rating}</span>}
        </div>
        <div className="ae-hero-meta">
          <span className={`ae-method is-${badge.tone}`}>{badge.label}</span>
          {!unavailable && conf != null && <span className="ae-meta-item">置信度 {conf}%</span>}
          {!unavailable && time && <span className="ae-meta-item">{time}</span>}
          {!unavailable && rubric && <span className="ae-meta-item ae-rubric">{RUBRIC_LABELS[rubric] || rubric}</span>}
        </div>
      </div>

      {/* mock / unavailable 显式提示，禁止伪装真实 AI */}
      {state === 'mock' && (
        <p className="ae-banner is-warn">
          当前为开发环境演示结果，仅用于验证评分流程，不构成研究结论。
        </p>
      )}
      {unavailable && (
        <p className="ae-banner is-muted">{impact || '本次未生成 AI 评分。'}</p>
      )}

      {/* ===== 2) 七维分项评分 ===== */}
      {!unavailable && (
        <div className="ae-block">
          <div className="ae-block-t">分项评分</div>
          <div className="ae-dims">
            {DIMS.map((d) => {
              const v = dimVal(result.ai_breakdown, d.key)
              const pct = v != null ? Math.max(0, Math.min(100, (v / d.max) * 100)) : 0
              return (
                <div key={d.key} className="ae-dim">
                  <div className="ae-dim-head">
                    <span className="ae-dim-k">{d.label}</span>
                    <span className="ae-dim-v">{v != null ? `${fmt1(v)} / ${d.max}` : '—'}</span>
                  </div>
                  <div className="ae-bar">
                    <div className="ae-bar-fill" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
          {/* 数据质量调整（±5，可负）：单列、带分隔，不画进度条 */}
          <div className="ae-dim-adj">
            <div className="ae-dim-head">
              <span className="ae-dim-k">数据质量调整</span>
              <span className="ae-dim-v">{adjText(result.ai_breakdown)}</span>
            </div>
          </div>
        </div>
      )}

      {/* ===== 3) 评分解释 ===== */}
      {hasExplain && (
        <div className="ae-block">
          <div className="ae-block-t">评分解释</div>
          {summary && <p className="ae-summary">{summary}</p>}
          {drivers.length > 0 && (
            <ul className="ae-list ae-drivers">
              {drivers.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          )}
          {(strengths.length > 0 || risks.length > 0) && (
            <div className="ae-sr">
              <div className="ae-sr-col">
                <div className="ae-sr-t">关键优势</div>
                {strengths.length > 0 ? (
                  <ul className="ae-list is-up">
                    {strengths.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="ae-muted">—</p>
                )}
              </div>
              <div className="ae-sr-col">
                <div className="ae-sr-t">主要风险</div>
                {risks.length > 0 ? (
                  <ul className="ae-list is-down">
                    {risks.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="ae-muted">暂无显著风险</p>
                )}
              </div>
            </div>
          )}
          {impact && <p className="ae-impact">{impact}</p>}
        </div>
      )}

      {/* ===== 4) 数据透明度（完整度 / 新鲜度分列，避免「100% 完整」与「偏旧」并置冲突）===== */}
      <div className="ae-block">
        <div className="ae-block-t">数据透明度</div>
        <div className="ae-trans">
          <div className="ae-trans-row">
            <span className="ae-trans-k">字段完整度</span>
            <span className="ae-trans-v">
              {dataConf != null
                ? `${dataConf}%`
                : missing.length
                  ? `缺失 ${missing.length} 项`
                  : '关键字段较完整'}
            </span>
          </div>
          <div className="ae-trans-row">
            <span className="ae-trans-k">数据新鲜度</span>
            <span className="ae-trans-v">{stale.length ? `${stale.length} 项偏旧` : '数据较新'}</span>
          </div>
          {validator && (
            <div className="ae-trans-row">
              <span className="ae-trans-k">校验状态</span>
              <span className="ae-trans-v">{validator}</span>
            </div>
          )}
        </div>
        {(missing.length > 0 || stale.length > 0) && (
          <div className="ae-tags">
            {missing.slice(0, 8).map((f, i) => (
              <span key={`m-${i}-${f}`} className="ae-tag is-missing">
                {fieldLabel(f)}
              </span>
            ))}
            {stale.slice(0, 8).map((f, i) => (
              <span key={`s-${i}-${f}`} className="ae-tag is-stale">
                {fieldLabel(f)} · 偏旧
              </span>
            ))}
          </div>
        )}
        {sources.length > 0 && (
          <p className="ae-srcline">
            数据时点：{sources.map(([k, v]) => `${SRC_LABELS[k] || k} ${fmtSrc(v)}`).join(' · ')}
          </p>
        )}
      </div>

      {/* ===== 5) 免责声明（全局唯一一句；mock/unavailable 状态已由上方 banner 显式说明）===== */}
      <p className="ae-foot">{FIXED_DISCLAIMER}</p>
    </section>
  )
}
