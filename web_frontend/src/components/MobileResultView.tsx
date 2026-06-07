import type { ResearchResult } from '../api'
import AiEvidence from './AiEvidence'

// 移动端结果视图：仅承载页面骨架（ticker / 公司 / 数据状态 / 研究优先级），
// 评分主叙事（分数 / 七维分项 / 解释 / 数据透明度 / 免责）统一交给共享组件 AiEvidence，
// 保证桌面与移动展示一致、逻辑单一来源。纯展示层，不改后端 / 抓取 / 评分 / 数据结构。

interface Props {
  result: ResearchResult
}

export default function MobileResultView({ result }: Props) {
  const pending = result.state === 'pending'
  const canonical = result.canonical ?? result.ticker
  const sub = [result.company_name, result.market]
    .filter((s): s is string => Boolean(s) && s !== canonical)
    .join(' · ')

  // 研究优先级拆「主结论 + 括注」：「高研究优先级（人工确认）」→ 主「高研究优先级」+ 注「人工确认」
  const prioRaw = result.research_priority || ''
  const prioMatch = prioRaw.match(/^(.*?)[（(]([^）)]*)[）)]\s*$/)
  const prioMain = prioMatch ? prioMatch[1].trim() : prioRaw
  const prioQual = prioMatch ? prioMatch[2].trim() : ''

  return (
    <div className="mrv">
      {/* 页面骨架：标识 + 数据状态 + 研究优先级（非投资建议） */}
      <div className="mrv-head">
        <div className="mrv-id">
          <span className="mrv-ticker">{canonical}</span>
          <span className="mrv-pill">{pending ? '部分待补录' : '数据完整'}</span>
        </div>
        {sub && <div className="mrv-name">{sub}</div>}
        {prioMain && (
          <>
            <div className="mrv-prio">{prioMain}</div>
            <div className="mrv-prio-meta">{prioQual ? `${prioQual} · ` : ''}非买卖建议</div>
          </>
        )}
      </div>

      {/* AI 证据评分主叙事（与桌面共享同一组件） */}
      <AiEvidence result={result} />
    </div>
  )
}
