import { useEffect, useMemo, useRef, useState } from 'react'
import { idempotencyKey } from '../idempotency'
import { AlertCircle, CheckCircle2, CircleAlert, LoaderCircle, Play, RefreshCw, ShieldAlert } from 'lucide-react'
import { BacktestAvailability, Job, fetchBacktestAvailability, fetchJob, submitBacktest } from '../api'
import { BacktestDataReadiness } from './BacktestDataReadiness'
import { SignalBacktest } from './SignalBacktest'

type Mode = 'formal' | 'synthetic' | 'signal'
type Template = 'buy_and_hold' | 'dual_moving_average'

const terminal = new Set(['succeeded', 'failed', 'cancelled'])

function syntheticBody(template: Template, availability: BacktestAvailability): Record<string, unknown> {
  const fixture = availability.synthetic_acceptance
  const strategyParameters = template === 'buy_and_hold'
    ? { symbol: 'ACME', start_date: '2024-01-02' }
    : { symbol: 'ACME', fast_window: 2, slow_window: 3 }
  return {
    kind: 'backtest', operation: fixture.operation, data_snapshot: fixture.dataset_version, code_version: 'p3-04-wizard-v2-reference-accounting',
    strategy: {
      template,
      parameters: strategyParameters,
      data_requirements: { dataset_version: fixture.dataset_version, universe_version: fixture.asset_pool_version, calendar_version: fixture.calendar_version, price_basis: 'raw' },
    },
    parameters: {
      asset_pool: { version: fixture.asset_pool_version, kind: 'synthetic', symbols: ['ACME'], corporate_actions: 'not_applicable' },
      calendar_version: fixture.calendar_version, nautilus_version: fixture.engine_version,
      initial_cash: '1000', limit_price: '100', requested_quantity: '10',
      bars: [
        { day: '2024-01-02', available_time: '2024-01-02T21:00:00Z', close: '100', limit_fill_price: '100', fill_quantity: '0' },
        { day: '2024-01-03', available_time: '2024-01-03T21:00:00Z', close: '101', limit_fill_price: '100', fill_quantity: '4' },
        { day: '2024-01-04', available_time: '2024-01-04T21:00:00Z', close: '102', limit_fill_price: '100', fill_quantity: '0' },
      ],
    },
  }
}

export function BacktestWizard() {
  const [availability, setAvailability] = useState<BacktestAvailability | null>(null)
  const [mode, setMode] = useState<Mode>('formal')
  const [template, setTemplate] = useState<Template>('buy_and_hold')
  const [acknowledged, setAcknowledged] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const inFlight = useRef(false)
  const pendingKey = useRef<{ body: string; key: string } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setAvailability(null); setError('')
    fetchBacktestAvailability(controller.signal).then(setAvailability).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取回测门禁')
    })
    return () => controller.abort()
  }, [attempt])
  useEffect(() => {
    if (!job || terminal.has(job.status)) return
    const timer = window.setInterval(() => fetchJob(job.id, new AbortController().signal).then(setJob).catch(reason => setError(reason instanceof Error ? reason.message : '无法更新任务状态')), 1500)
    return () => window.clearInterval(timer)
  }, [job])

  const preflight = useMemo(() => {
    if (!availability) return []
    const synthetic = availability.synthetic_acceptance
    return mode === 'formal'
      ? [
          ['策略与订单', '待定', '两套策略模板已定义，但正式调仓/市价语义尚未验收。'],
          ['股票池与数据', '阻断', availability.formal_backtest_reason],
          ['区间与可用时点', '阻断', '尚无可用于正式回测的固定股票池快照，不能把现有下载目录当作合格输入。'],
          ['资金与成本', '阻断', '真实手续费、滑点、成交量约束和公司行为/退市总回报尚未完成验收。'],
        ]
      : [
          ['策略', '通过', template === 'buy_and_hold' ? '买入持有 v1，ACME。' : '双均线 v1，ACME，快线 2 / 慢线 3。'],
          ['股票池与数据', '通过', `${synthetic.asset_pool_version} · ${synthetic.dataset_version}`],
          ['区间与成交', '通过', '固定 2024-01-02 至 2024-01-04；仅买入限价，显式成交 4 股、撤余下 6 股。'],
          ['资金与成本', '通过', '初始 USD 1,000；限价 USD 100；首次成交固定手续费 USD 1；无滑点。'],
        ]
  }, [availability, mode, template])

  const submit = async () => {
    if (!availability || mode !== 'synthetic' || !acknowledged || inFlight.current || (job && !terminal.has(job.status))) return
    inFlight.current = true
    setSubmitting(true); setError(''); setJob(null)
    try {
      const body = syntheticBody(template, availability)
      // One key is retained for this click; retries and accidental double-clicks
      // resolve to the same durable job rather than enqueueing another run.
      const encoded = JSON.stringify(body)
      if (!pendingKey.current || pendingKey.current.body !== encoded) pendingKey.current = { body: encoded, key: idempotencyKey('backtest') }
      setJob(await submitBacktest(body, pendingKey.current.key))
      pendingKey.current = null
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '回测提交失败')
    } finally { inFlight.current = false; setSubmitting(false) }
  }

  return <div className="backtest-wizard">
    <section className="page-heading"><div><p className="eyebrow">历史回测 · 受控提交</p><h1>新建回测</h1><p>先检查策略、数据、执行能力与成本。正式回测未验收时不会创建任务或生成虚构结果。</p></div><span className="static-boundary">持久化任务 · 提交前门禁</span></section>
    {error ? <section className="empty-state wide"><AlertCircle size={24}/><strong>无法继续</strong><p>{error}</p><button className="detail-action data-retry" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={15}/>重新读取门禁</button></section> : !availability ? <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取回测能力</strong><p>只读取服务端能力登记，不会扫描行情或创建任务。</p></section> : <>
      {mode === 'formal' && <BacktestDataReadiness />}
      <div className="wizard-mode-grid">
        <button type="button" className={`wizard-mode ${mode === 'signal' ? 'is-active' : ''}`} onClick={() => setMode('signal')}><Play size={19}/><span><strong>策略驱动闭环验收</strong><small>合成数据；信号实际驱动次日买卖与净值</small></span></button>
        <button type="button" className={`wizard-mode ${mode === 'formal' ? 'is-active' : ''}`} onClick={() => { setMode('formal'); setAcknowledged(false); setJob(null) }}><ShieldAlert size={19}/><span><strong>正式美股日线回测</strong><small>当前受 P3-03A 数据与成本门禁阻断</small></span></button>
        <button type="button" className={`wizard-mode ${mode === 'synthetic' ? 'is-active' : ''}`} onClick={() => { setMode('synthetic'); setJob(null) }}><CheckCircle2 size={19}/><span><strong>合成验收样例</strong><small>仅验证已验收的限价部分成交路径</small></span></button>
      </div>
      {mode === 'signal' ? <SignalBacktest availability={availability} /> : <section className="wizard-shell">
        <div className="wizard-steps" aria-label="回测配置步骤">
          <WizardStep number="1" title="策略" text="选定版本化模板" />
          <WizardStep number="2" title="股票池" text="固定快照与范围" />
          <WizardStep number="3" title="区间" text="可用时点与日历" />
          <WizardStep number="4" title="资金与成本" text="成交假设" />
          <WizardStep number="5" title="提交检查" text="能力与数据门禁" />
        </div>
        <div className="wizard-content">
          <section className="wizard-card"><h2>配置摘要</h2><div className="strategy-picker"><button type="button" className={template === 'buy_and_hold' ? 'is-selected' : ''} onClick={() => setTemplate('buy_and_hold')}><strong>买入持有</strong><span>buy-and-hold-v1</span></button><button type="button" className={template === 'dual_moving_average' ? 'is-selected' : ''} onClick={() => setTemplate('dual_moving_average')}><strong>双均线</strong><span>dual-moving-average-v1</span></button></div>
            <dl className="wizard-summary"><div><dt>股票池</dt><dd>{mode === 'formal' ? '未登记合格的真实股票池' : 'synthetic-us-equity-pool-v1 · ACME'}</dd></div><div><dt>日期区间</dt><dd>{mode === 'formal' ? '待 P3-03A 固定快照后选择' : '2024-01-02 至 2024-01-04（不可编辑验收输入）'}</dd></div><div><dt>资金</dt><dd>{mode === 'formal' ? '正式账户与保证金模型未开放' : 'USD 1,000 初始现金；不使用保证金'}</dd></div><div><dt>成本与成交</dt><dd>{mode === 'formal' ? '真实成本模型未验收' : '买入限价 USD 100；首次成交费 USD 1；无滑点'}</dd></div></dl>
          </section>
          <section className="wizard-card preflight"><h2>提交前检查</h2>{preflight.map(([name, status, detail]) => <div className="preflight-row" key={name}><span className={`preflight-status ${status === '通过' ? 'pass' : status === '阻断' ? 'blocked' : ''}`}>{status === '通过' ? <CheckCircle2 size={15}/> : <CircleAlert size={15}/>} {status}</span><div><strong>{name}</strong><p>{detail}</p></div></div>)}</section>
          {mode === 'formal' ? <section className="wizard-blocked"><ShieldAlert size={19}/><div><strong>正式回测不会提交</strong><p>{availability.formal_backtest_reason} 完成前，日期、资金和成本输入不能绕过门禁。</p></div></section> : <section className={`wizard-ack ${acknowledged ? 'is-acknowledged' : ''}`}><button type="button" aria-pressed={acknowledged} onClick={() => setAcknowledged(value => !value)}>{acknowledged ? <CheckCircle2 size={18}/> : <CircleAlert size={18}/>}</button><div><strong>我理解这只是合成验收，不是历史收益回测</strong><p>{availability.synthetic_acceptance.scope}</p></div></section>}
          {mode === 'synthetic' ? <button className="wizard-submit" type="button" disabled={!acknowledged || submitting || Boolean(job && !terminal.has(job.status))} onClick={submit}>{submitting || (job && !terminal.has(job.status)) ? <LoaderCircle className="animate-spin" size={17}/> : <Play size={17}/>} {submitting ? '正在提交…' : job && !terminal.has(job.status) ? '任务运行中…' : '提交合成验收任务'}</button> : <button className="wizard-submit blocked" type="button" disabled><ShieldAlert size={17}/>正式回测受门禁阻断</button>}
          {job ? <section className={`wizard-job ${job.status}`}><strong>{job.status === 'succeeded' ? '验收任务已发布' : job.status === 'failed' ? '任务失败' : job.status === 'cancelled' ? '任务已取消' : `任务${job.status === 'queued' ? '已排队' : '运行中'}`}</strong><p>任务 ID：{job.id}</p>{job.failure ? <p className="task-failure">{job.failure.code}：{job.failure.message}</p> : <p>{job.status === 'succeeded' ? '结果产物已原子发布，可在“回测任务”查看状态；它只适用于合成验收范围。' : '浏览器关闭后任务仍会继续；此处每 1.5 秒刷新状态。'}</p>}</section> : null}
        </div>
      </section>}
    </>}
  </div>
}

function WizardStep({ number, title, text }: { number: string; title: string; text: string }) {
  return <div className="wizard-step"><span>{number}</span><div><strong>{title}</strong><small>{text}</small></div></div>
}
