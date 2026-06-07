import { useState, useEffect, useRef } from 'react'
import Header from './components/Header'
import SearchBar from './components/SearchBar'
import ExampleChips from './components/ExampleChips'
import ResultPreview from './components/ResultPreview'
import { fetchResearch, type ResearchResult, type Status } from './api'

export default function App() {
  const [ticker, setTicker] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [result, setResult] = useState<ResearchResult | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  // 取消上一个在途请求 + 序号守卫：保证「最新请求优先」，旧响应永不覆盖新结果。
  const abortRef = useRef<AbortController | null>(null)
  const seqRef = useRef(0)

  async function run(value: string) {
    const t = value.trim()
    if (!t) return

    // 取消上一个在途请求（防竞态：旧响应覆盖新结果）。
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    const mySeq = ++seqRef.current

    setTicker(t)
    setStatus('loading')
    setResult(null)
    setErrorMsg('')
    try {
      const data = await fetchResearch(t, { signal: controller.signal })
      if (mySeq !== seqRef.current) return // 已有更新的请求发起，丢弃本次结果
      setResult(data)
      setStatus('done')
    } catch (raw) {
      // 被新请求取代 / 组件卸载导致的主动中止：静默忽略，不打断 UI。
      if (raw instanceof DOMException && raw.name === 'AbortError') return
      if (mySeq !== seqRef.current) return
      const msg = raw instanceof Error ? raw.message : String(raw)
      if (msg === 'TIMEOUT') {
        setErrorMsg('请求超时：后端响应较慢或暂未返回，请稍后重试')
      } else if (/^HTTP_/i.test(msg)) {
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

  // 卸载时中止在途请求，避免内存泄漏与卸载后 setState。
  useEffect(() => () => abortRef.current?.abort(), [])

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
        {/* 首页 chips 仅在 idle 渲染，loading 期间不会出现，故无需 disabled */}
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
