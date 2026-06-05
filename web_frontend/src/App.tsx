import { useState, useEffect, useRef } from 'react'
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
    } catch (raw) {
      const msg = raw instanceof Error ? raw.message : String(raw)
      if (/^HTTP_/i.test(msg)) {
        const detail = msg.replace(/^HTTP_/i, '').replace(/_/g, ' ')
        setErrorMsg(`后端返回错误：${detail}`)
      } else if (raw instanceof TypeError || /fetch|NetworkError/i.test(msg)) {
        setErrorMsg('无法连接到后端，请确认 API 已启动并允许当前前端地址访问（CORS）')
      } else {
        setErrorMsg('数据加载异常：后端返回格式可能异常，请稍后重试')
      }
      setStatus('error')
    }
  }

  // 深链接：?q=TICKER 进入页面即自动查询一次（便于分享结果 URL，也便于截图回归）。
  // 用 ref 防止 StrictMode 开发态重复触发。
  const deepLinkDone = useRef(false)
  useEffect(() => {
    if (deepLinkDone.current) return
    deepLinkDone.current = true
    const q = new URLSearchParams(window.location.search).get('q')
    if (q && q.trim()) run(q)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const idle = status === 'idle'

  return (
    <div className="page">
      <Header />
      <main className={idle ? 'home' : 'home has-result'}>
        {/* 首页 Hero 仅初始态显示；loading/done/error 结果态隐藏，主视觉收敛到搜索栏 + 结果卡 */}
        {idle && (
          <section className="hero">
            <h1>找到值得深入研究的股票</h1>
            <p className="hero-sub">输入股票代码，快速得到质量、估值与风险的研究优先级</p>
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
