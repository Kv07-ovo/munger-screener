import { useState } from 'react'
import Header from './components/Header'
import SearchBar from './components/SearchBar'
import ExampleChips from './components/ExampleChips'
import ResultPreview from './components/ResultPreview'
import { fetchResearch, type ResearchResult } from './api'

type Status = 'idle' | 'loading' | 'done' | 'error'

export default function App() {
  const [ticker, setTicker] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [result, setResult] = useState<ResearchResult | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  async function run(value: string) {
    const t = value.trim()
    if (!t) return
    setTicker(t)
    setStatus('loading')
    setResult(null)
    setErrorMsg('')
    try {
      const data = await fetchResearch(t)
      setResult(data)
      setStatus('done')
    } catch {
      setErrorMsg('无法连接到后端，请确认 API 已启动（http://localhost:8000）')
      setStatus('error')
    }
  }

  const idle = status === 'idle'

  return (
    <div className="page">
      <Header />
      <main className={idle ? 'home' : 'home has-result'}>
        {/* 首页 Hero 仅初始态显示；loading/done/error 结果态隐藏，主视觉收敛到搜索栏 + 结果卡 */}
        {idle && (
          <section className="hero">
            <h1>你好，User</h1>
            <p className="hero-sub">这里是 Kv的选股小猫</p>
          </section>
        )}

        <SearchBar
          value={ticker}
          onChange={setTicker}
          onSubmit={() => run(ticker)}
          loading={status === 'loading'}
        />

        {/* 首页示例 chips 仅在初始态出现（kv_1/kv_3）；错误/数据不足态的 chips 由 ResultPreview 渲染 */}
        {idle && <ExampleChips tickers={['MSFT', 'V', 'KO']} onPick={(t) => run(t)} />}

        <ResultPreview
          status={status}
          result={result}
          errorMsg={errorMsg}
          ticker={ticker}
          onPick={(t) => run(t)}
        />
      </main>

      <footer className="disclaimer">
        研究优先级仅表示「值得进一步研究的程度」，不是投资建议。
      </footer>
    </div>
  )
}
