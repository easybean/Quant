import { useEffect, useRef, useState } from 'react'
import { fetchJob, submitBacktest, type BacktestAvailability, type Job } from '../api'
import { idempotencyKey } from '../idempotency'

export function SignalBacktest({ availability }: { availability: BacktestAvailability }) {
  const fixture = availability.signal_acceptance
  const [template, setTemplate] = useState('dual_moving_average')
  const [ack, setAck] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef<{ body: string; key: string } | null>(null)
  const inFlight = useRef(false)
  const running = Boolean(job && !['succeeded', 'failed', 'cancelled'].includes(job.status))
  useEffect(() => {
    if (!running || !job) return
    const controller = new AbortController()
    const timer = window.setInterval(() => fetchJob(job.id, controller.signal).then(setJob).catch(e => { if (!controller.signal.aborted) setError(String(e)) }), 1500)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [job, running])
  async function submit() {
    if (inFlight.current || running || !fixture?.available) return
    if (!ack) { setError('请先勾选下方合成验收范围确认。'); document.getElementById('signal-ack')?.focus(); return }
    const body = { kind: 'backtest', operation: fixture.operation, data_snapshot: fixture.dataset_version, code_version: 'signal-wizard-v1', parameters: fixture.parameters,
      strategy: { template, parameters: template === 'buy_and_hold' ? { symbol: 'ACME', start_date: '2024-01-02' } : { symbol: 'ACME', fast_window: 2, slow_window: 3 },
        data_requirements: { dataset_version: fixture.dataset_version, universe_version: fixture.asset_pool_version, calendar_version: fixture.calendar_version, price_basis: 'raw' } } }
    const encoded = JSON.stringify(body)
    if (pending.current?.body !== encoded) pending.current = { body: encoded, key: idempotencyKey('signal') }
    inFlight.current = true; setBusy(true); setError('')
    try { setJob(await submitBacktest(body, pending.current.key)); pending.current = null }
    catch(e) { setError(e instanceof Error ? e.message : '提交失败，可重试同一任务') }
    finally { inFlight.current = false; setBusy(false) }
  }
  if (!fixture?.available) return <section className="wizard-blocked">服务端尚未开放策略闭环验收，请确认部署版本；不会退回固定成交冒充策略。</section>
  return <section className="wizard-card" aria-label="策略驱动闭环验收"><h2>策略驱动闭环验收</h2><p>{fixture.scope}</p>
    <p>收盘生成目标仓位 → 下一交易日限价单 → 成交与成本 → 每日净值。买卖由策略决定，不预填成交数量。</p>
    <div className="strategy-picker"><button type="button" aria-pressed={template === 'buy_and_hold'} disabled={busy || running} onClick={() => setTemplate('buy_and_hold')}>买入持有基准</button><button type="button" aria-pressed={template === 'dual_moving_average'} disabled={busy || running} onClick={() => setTemplate('dual_moving_average')}>双均线策略（2 / 3）</button></div>
    <p>固定 ACME 合成样本；初始 USD 1,000；单证券、整股、只做多、不融资。样本与成本版本固定，不调参挑收益。</p>
    <label><input id="signal-ack" type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} aria-required="true"/><span style={{ color: '#c0392b' }} aria-hidden="true"> *</span> 我理解这是策略闭环工程验收，不是真实股票历史收益</label>
    {error && <p role="alert">{error}</p>}
    <button type="button" className="wizard-submit" disabled={busy || running} onClick={submit}>{busy ? '正在提交…' : running ? '策略任务运行中…' : '运行策略闭环验收'}</button>
    {job && <div className="wizard-job"><strong>{job.status === 'succeeded' ? '策略闭环任务已发布' : job.status === 'failed' ? '策略任务失败' : '策略任务处理中'}</strong><p>任务 ID：{job.id}</p>{job.failure && <p role="alert">{job.failure.message}</p>}{job.status === 'succeeded' && <p>请在“回测报告”查看每日净值、信号和订单；也可切换另一模板提交作对照。</p>}</div>}
  </section>
}
