import type { ResearchResult } from '../api'

// 移动端「研究摘要」视图。纯展示层：不改后端 / 抓取 / 评分 / 数据结构，
// 只把已有字段重排成用户可读的摘要，并对内部口径（质量分/成长分/资产负债/估值分/
// 最终分预览/机器评分/AI 初判字段名）一律不展示。

interface Props {
  result: ResearchResult
}

// 规则总分取整（与桌面 asScore 同口径）。
function asScore(v: unknown): number | null {
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? Math.round(n) : null
}

// 结构化数字（pe/pb/market_cap）的展示格式；缺失/非有限值返回 null。
function fmtNum(v: unknown): string | null {
  if (v == null) return null
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return null
    return Number.isInteger(v) ? String(v) : v.toFixed(1)
  }
  const s = String(v).trim()
  return s === '' || s === '—' ? null : s
}

interface ParsedMetric {
  name: string
  value: string
  tone: 'up' | 'down'
}

// 解析后端确定格式的一条优势/风险：「{指标} 数据上偏强（{值}）」/「…偏弱（…）」。
// 关键防编造：
//   1) 必须含「数据上偏强/偏弱」闸门：叙述句（如「生意质量接近满分（27/30）」）不含此短语，会被正确跳过；
//   2) 括号值必须含数字，且拒绝形如 27/30 的分数；
//   不满足返回 null（该条退化为整句原文展示），绝不凭空造出指标名或数值。
function parseMetric(s: string): ParsedMetric | null {
  const tone: 'up' | 'down' | null = s.includes('数据上偏强')
    ? 'up'
    : s.includes('数据上偏弱')
      ? 'down'
      : null
  if (!tone) return null
  const m = s.match(/[（(]([^）)]+)[）)]\s*$/)
  if (!m || m.index == null) return null
  const value = m[1].trim()
  if (!/\d/.test(value) || /^\d+\s*\/\s*\d+$/.test(value)) return null
  const name = s
    .slice(0, m.index)
    .replace(/\s*数据上(偏强|偏弱).*$/, '')
    .replace(/[（(][^）)]*[）)]/g, '') // 去掉「(5年均值)」之类括注，仅留指标名
    .trim()
  if (!name) return null
  return { name, value, tone }
}

// 一句话结论：优先后端自然语言 raw.ai_reason；缺失时仅依据「是否存在优势/风险」确定性地
// 合成一句中性话术。绝不输出「AI 初判：质优」字段名格式，绝不编造数值或下投资结论。
function oneLineConclusion(r: ResearchResult): string {
  const reason = r.raw && typeof r.raw.ai_reason === 'string' ? r.raw.ai_reason.trim() : ''
  if (reason.length >= 8) return reason
  const hasUp = (r.strengths ?? []).length > 0
  const hasDown = (r.risks ?? []).length > 0
  if (hasUp && hasDown) return '财务数据上既有亮点也有需要注意之处，建议人工复核后再判断。'
  if (hasUp) return '财务数据上未见明显硬伤，可纳入进一步研究清单。'
  if (hasDown) return '财务数据上存在需要注意之处，建议谨慎并人工复核。'
  return '财务数据已抓取，建议人工复核后再判断。'
}

// 关键数据（≤4）：PE/PB/市值来自结构化 financials；ROE/ROIC/毛利率仅在 strengths/risks 文本
// 中真实出现时解析展示，缺失则省略（不补占位、不用评分分项顶替、不编造）。不足 2 项时用 PB/市值兜底。
const PARSED_KEY_ORDER = ['ROE', 'ROIC', '毛利率']
function pickKeyData(r: ResearchResult): { label: string; value: string }[] {
  const fin = r.financials ?? {}
  const parsed: Record<string, string> = {}
  for (const s of [...(r.strengths ?? []), ...(r.risks ?? [])]) {
    const p = parseMetric(s)
    if (p && !(p.name in parsed)) parsed[p.name] = p.value
  }
  const out: { label: string; value: string }[] = []
  const pe = fmtNum(fin.pe)
  if (pe != null) out.push({ label: 'PE', value: pe })
  for (const k of PARSED_KEY_ORDER) {
    if (out.length >= 4) break
    if (parsed[k] != null) out.push({ label: k, value: parsed[k] })
  }
  if (out.length < 2) {
    const pb = fmtNum(fin.pb)
    if (pb != null && out.length < 4) out.push({ label: 'PB', value: pb })
    const mc = fmtNum(fin.market_cap)
    if (mc != null && out.length < 4) out.push({ label: '市值', value: mc })
  }
  return out.slice(0, 4)
}

export default function MobileResultView({ result }: Props) {
  const pending = result.state === 'pending'
  const canonical = result.canonical ?? result.ticker
  const score = asScore(result.total_score)
  const sub = [result.company_name, result.market]
    .filter((s): s is string => Boolean(s) && s !== canonical)
    .join(' · ')

  // 研究优先级拆「主结论 + 括注」：「高研究优先级（人工确认）」→ 主「高研究优先级」+ 注「人工确认」
  const prioRaw = result.research_priority || ''
  const prioMatch = prioRaw.match(/^(.*?)[（(]([^）)]*)[）)]\s*$/)
  const prioMain = prioMatch ? prioMatch[1].trim() : prioRaw
  const prioQual = prioMatch ? prioMatch[2].trim() : ''

  const strengths = (result.strengths ?? []).slice(0, 3)
  const risks = (result.risks ?? []).slice(0, 2)
  const keyData = pickKeyData(result)
  const takeaway = oneLineConclusion(result)
  // 关键指标 chip：取已解析出的优势指标，最多 2 个，作为卡内一眼概览（非按钮）。
  const chipMetrics = strengths
    .map(parseMetric)
    .filter((p): p is ParsedMetric => p != null)
    .slice(0, 2)

  // 一条优势/风险：能解析成「指标名 + 值 + 偏强/偏弱」则按阅读节奏排版；否则整句原文展示。
  const renderReason = (s: string, i: number) => {
    const p = parseMetric(s)
    if (!p) {
      return (
        <li key={i} className="mrv-reason">
          <span className="mrv-reason-text">{s}</span>
        </li>
      )
    }
    return (
      <li key={i} className="mrv-reason">
        <div className="mrv-reason-lead">
          <span className="mrv-reason-name">{p.name}</span>{' '}
          <span className="mrv-reason-val">{p.value}</span>
        </div>
        <div className="mrv-reason-note">{p.tone === 'up' ? '数据上偏强' : '数据上偏弱'}</div>
      </li>
    )
  }

  return (
    <div className="mrv">
      <div className="mrv-card">
        {/* 1) 顶部结论卡 */}
        <div className="mrv-id">
          <span className="mrv-ticker">{canonical}</span>
          <span className="mrv-pill">{pending ? '部分待补录' : '数据完整'}</span>
        </div>
        {sub && <div className="mrv-name">{sub}</div>}
        <div className="mrv-score-label">规则总分</div>
        <div className="mrv-score">
          {!pending && score != null ? (
            <>
              <span className="mrv-score-num">{score}</span>
              <span className="mrv-score-max">/ 100</span>
            </>
          ) : (
            <span className="mrv-score-na">数据不足 · 待补录</span>
          )}
        </div>
        {prioMain && <div className="mrv-prio">{prioMain}</div>}
        <div className="mrv-prio-meta">{prioQual ? `${prioQual} · ` : ''}非买卖建议</div>
        {chipMetrics.length > 0 && (
          <div className="mrv-keychips">
            {chipMetrics.map((c) => (
              <span key={c.name} className="mrv-chip">
                {c.name} {c.value}
              </span>
            ))}
          </div>
        )}

        <div className="mrv-divider" />

        {/* 2-6) 阅读型正文 */}
        <div className="mrv-body">
          {/* 2) 一句话结论 */}
          <p className="mrv-takeaway">{takeaway}</p>

          {/* 3) 为什么值得看 */}
          {strengths.length > 0 && (
            <section>
              <h3 className="mrv-sec-t">为什么值得看</h3>
              <ul className="mrv-reasons">{strengths.map(renderReason)}</ul>
            </section>
          )}

          {/* 4) 需要注意 */}
          <section>
            <h3 className="mrv-sec-t">需要注意</h3>
            {risks.length > 0 ? (
              <ul className="mrv-reasons">{risks.map(renderReason)}</ul>
            ) : (
              <p className="mrv-empty">
                暂无显著硬伤，但仍需人工复核年报、竞争格局与资本配置历史。
              </p>
            )}
          </section>

          {/* 5) 关键数据 */}
          {keyData.length > 0 && (
            <section>
              <h3 className="mrv-sec-t">关键数据</h3>
              <div className="mrv-data-grid">
                {keyData.map((d) => (
                  <div key={d.label} className="mrv-tile">
                    <span className="mrv-tile-k">{d.label}</span>
                    <span className="mrv-tile-v">{d.value}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* 6) 数据口径 */}
          <p className="mrv-source">自动抓取 + 本地补录；研究优先级不是投资建议。</p>
        </div>
      </div>
    </div>
  )
}
