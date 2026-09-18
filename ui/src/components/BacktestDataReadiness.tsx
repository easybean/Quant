import { useEffect, useState } from 'react'

type Readiness = { available: boolean; message?: string; snapshot_id?: string; assessed_at?: string; range?: { start: string; end: string }; symbols?: string[]; checks?: { code: string; label: string; status: string; detail: string }[] }
const labels: Record<string, string> = { passed: '本项已核验', partial: '部分完成', blocked: '仍阻断' }

export function BacktestDataReadiness() {
  const [data, setData] = useState<Readiness | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setData(null); setError('')
    const base = (import.meta.env.VITE_API_BASE_URL || 'http://192.168.1.132:8511').replace(/\/$/, '')
    fetch(`${base}/api/v1/backtests/data-readiness`, { signal: controller.signal, headers: { Accept: 'application/json' } })
      .then(async response => { if (!response.ok) throw new Error(`读取数据验收失败（HTTP ${response.status}）`); return response.json() as Promise<Readiness> })
      .then(setData).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取数据验收失败') })
    return () => controller.abort()
  }, [attempt])
  return <section className="data-card" aria-label="真实回测数据验收"><header><div><h2>真实回测数据验收</h2><p>显示固定证据副本的验收进度，不是实时行情覆盖，也不会自动开放回测。</p></div><button type="button" className="detail-action" onClick={() => setAttempt(value => value + 1)}>刷新数据验收</button></header>
    {error ? <p role="alert">{error}；数据仍按未合格处理，可点击刷新重试。</p> : !data ? <p role="status">正在读取数据验收…</p> : !data.available ? <p>{data.message}</p> : <>
      <p>工程验收股票：{data.symbols?.join('、')}；固定区间 {data.range?.start} 至 {data.range?.end}。</p>
      <p>证据版本：{data.snapshot_id}；审核时间：{data.assessed_at ? new Date(data.assessed_at).toLocaleString('zh-CN', { hour12: false }) : '未知'}。</p>
      {data.checks?.map(check => <div className="sync-provider-row" key={check.code}><strong>{check.label}</strong><span>{labels[check.status] || '状态未知'}</span><small>{check.detail}</small></div>)}
      <p className="data-note">单项通过不代表数据集合格。正式回测继续阻断，五股结果不代表全市场策略有效性。</p>
    </>}
  </section>
}
