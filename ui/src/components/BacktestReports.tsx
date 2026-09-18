import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, FileCheck2, GitCompareArrows, LoaderCircle, RefreshCw, ShieldAlert } from 'lucide-react'
import { compareBacktestReports, fetchBacktestReport, fetchBacktestReports, type BacktestComparison, type BacktestReport, type BacktestReportSummary } from '../api'

const money = (value: string) => `USD ${value}`
const format = (value?: string | null) => value ? value.replace('T', ' ').replace(/\.\d+Z$/, ' UTC').replace('Z', ' UTC') : '未记录'

export function BacktestReports({ view }: { view: 'reports' | 'compare' }) {
  const [items, setItems] = useState<BacktestReportSummary[] | null>(null)
  const [selected, setSelected] = useState('')
  const [report, setReport] = useState<BacktestReport | null>(null)
  const [left, setLeft] = useState(''); const [right, setRight] = useState('')
  const [comparison, setComparison] = useState<BacktestComparison | null>(null)
  const [error, setError] = useState(''); const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setItems(null); setError(''); setReport(null); setComparison(null)
    fetchBacktestReports(controller.signal).then(result => {
      setItems(result)
      const valid = result.filter(item => item.available)
      setSelected(current => current || valid[0]?.job_id || '')
      setLeft(current => current || valid[0]?.job_id || '')
      setRight(current => current || valid[1]?.job_id || '')
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取回测报告') })
    return () => controller.abort()
  }, [attempt])

  useEffect(() => {
    if (view !== 'reports' || !selected) return
    const controller = new AbortController(); setReport(null); setError('')
    fetchBacktestReport(selected, controller.signal).then(setReport).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取报告详情') })
    return () => controller.abort()
  }, [view, selected, attempt])

  useEffect(() => {
    if (view !== 'compare' || !left || !right) return
    const controller = new AbortController(); setComparison(null); setError('')
    compareBacktestReports(left, right, controller.signal).then(setComparison).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法比较报告') })
    return () => controller.abort()
  }, [view, left, right, attempt])

  const validItems = useMemo(() => (items || []).filter(item => item.available), [items])
  const title = view === 'reports' ? '回测报告' : '结果对比'
  const description = view === 'reports'
    ? '只读取已发布的合成验收工件，账本、成交、费用与版本均需通过归属和哈希检查。'
    : '仅在数据、资产池、日历、完整合成 bar 输入、订单/成本模型、原始价格口径、币种、区间和初始资金均相同的合同内比较终值账本。'
  return <div className="backtest-reports">
    <section className="page-heading"><div><p className="eyebrow">历史回测 · 合成验收工件</p><h1>{title}</h1><p>{description}</p></div><span className="static-boundary">只读 · 不启动回测</span></section>
    {error ? <Failure message={error} retry={() => setAttempt(value => value + 1)} /> : items === null ? <Loading /> : !validItems.length ? <Empty unavailable={items.filter(item => !item.available)} /> : view === 'compare' && validItems.length < 2 ? <NeedTwoReports /> : view === 'reports'
      ? <ReportView items={items} selected={selected} setSelected={setSelected} report={report} />
      : <ComparisonView items={validItems} left={left} right={right} setLeft={setLeft} setRight={setRight} comparison={comparison} />}
  </div>
}

function ReportView({ items, selected, setSelected, report }: { items: BacktestReportSummary[]; selected: string; setSelected: (id: string) => void; report: BacktestReport | null }) {
  return <div className="backtest-report-grid"><aside className="backtest-report-list"><header><strong>已发布工件</strong><span>{items.length} 项</span></header>{items.map(item => item.available && item.contract && item.metrics ? <button type="button" key={item.job_id} className={selected === item.job_id ? 'is-active' : ''} onClick={() => setSelected(item.job_id)}><strong>合成验收 · {item.job_id.slice(0, 8)}</strong><span>{item.contract.date_range.start} 至 {item.contract.date_range.end}</span><small>{money(item.metrics.total_pnl)} · {item.metrics.total_return_pct}%</small></button> : <p className="report-unavailable" key={item.job_id}>任务 {item.job_id.slice(0, 8)}：{item.reason || '工件未通过核验'}</p>)}</aside>
    <main className="backtest-report-detail">{!report ? <Loading /> : <ReportDetail report={report} />}</main></div>
}

function ReportDetail({ report }: { report: BacktestReport }) {
  return <><header className="report-detail-heading"><div><span className="synthetic-label"><ShieldAlert size={14}/>合成验收 · 非真实回测</span><h2>任务 {report.job_id}</h2><p>{report.contract.date_range.start} 至 {report.contract.date_range.end} · {report.contract.price_basis} 价格 · {report.contract.currency}</p></div><span className="report-hash"><CheckCircle2 size={14}/>报告哈希已核验</span></header>
    <section className="report-metrics"><Metric label="初始资金" value={money(report.metrics.initial_cash)} /><Metric label="期末权益" value={money(report.metrics.terminal_equity)} /><Metric label="总损益" value={money(report.metrics.total_pnl)} /><Metric label="总回报" value={`${report.metrics.total_return_pct}%`} /><Metric label="费用" value={money(report.metrics.fees)} /><Metric label="可用现金" value={money(report.metrics.free_cash)} /></section>
    <section className="report-section"><h3>持仓与成交</h3><div className="report-table"><div className="report-row report-head"><span>证券</span><span>数量</span><span>标记价</span><span>市值</span></div>{report.holdings.map(item => <div className="report-row" key={item.symbol}><strong>{item.symbol}</strong><span>{item.quantity}</span><span>{money(item.mark_price)}</span><span>{money(item.market_value)}</span></div>)}</div><div className="report-table fills"><div className="report-row report-head"><span>成交日</span><span>数量</span><span>价格</span><span>费用</span></div>{report.fills.map((item, index) => <div className="report-row" key={`${item.day}-${index}`}><span>{item.day}</span><span>{item.quantity}</span><span>{money(item.price)}</span><span>{money(item.fee_usd)}</span></div>)}</div></section>
    <section className="report-section report-provenance"><h3>合同与可追溯性</h3><dl><div><dt>数据 / 资产池</dt><dd>{report.contract.dataset_version} · {report.contract.asset_pool_version}</dd></div><div><dt>日历 / 公司行为</dt><dd>{report.contract.calendar_version} · {report.contract.corporate_actions}</dd></div><div><dt>引擎 / 代码</dt><dd>{String(report.versions.engine.name)} {String(report.versions.engine.version)} · {report.versions.code_version}</dd></div><div><dt>完整合成 bar SHA-256</dt><dd className="hash-value">{report.contract.bars_sha256}</dd></div><div><dt>输入指纹（策略与参数）</dt><dd className="hash-value">{report.versions.input_fingerprint}</dd></div><div><dt>订单 / 成本合同</dt><dd className="hash-value">{JSON.stringify(report.contract.execution_contract)}</dd></div><div><dt>工件 SHA-256（本次读取指纹）</dt><dd className="hash-value">{report.artifact.sha256}</dd></div><div><dt>报告哈希（已核验）</dt><dd className="hash-value">{report.artifact.report_hash}</dd></div><div><dt>创建时间</dt><dd>{format(report.created_at)}</dd></div></dl></section>
    <SignalEvidence report={report} />
    <section className="report-warnings"><h3>数据与指标边界</h3><ul>{report.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></section>
  </>
}

function ComparisonView({ items, left, right, setLeft, setRight, comparison }: { items: BacktestReportSummary[]; left: string; right: string; setLeft: (id: string) => void; setRight: (id: string) => void; comparison: BacktestComparison | null }) {
  return <><section className="comparison-picker"><label>报告 A<select value={left} onChange={event => setLeft(event.target.value)}>{items.map(item => <option value={item.job_id} key={item.job_id}>{item.job_id} · {item.contract?.date_range.start}</option>)}</select></label><GitCompareArrows size={20}/><label>报告 B<select value={right} onChange={event => setRight(event.target.value)}>{items.map(item => <option value={item.job_id} key={item.job_id}>{item.job_id} · {item.contract?.date_range.start}</option>)}</select></label></section>{!comparison ? <Loading /> : !comparison.comparable ? <section className="comparison-blocked"><ShieldAlert size={22}/><div><strong>这两个工件不可比较</strong><p>不同合同的终值不能相减或排名。需一致：{comparison.non_comparable_reasons.join('、')}。</p></div></section> : <section className="comparison-result"><header><span className="synthetic-label"><FileCheck2 size={14}/>同一合成合同</span><h2>终值账本差异（B − A）</h2><p>{comparison.comparison_scope}</p></header><div className="comparison-metrics"><Metric label="期末权益" value={money(comparison.differences!.terminal_equity)} /><Metric label="总损益" value={money(comparison.differences!.total_pnl)} /><Metric label="总回报" value={`${comparison.differences!.total_return_pct}%`} /><Metric label="费用" value={money(comparison.differences!.fees)} /></div>{Object.keys(comparison.version_differences || {}).length ? <div className="comparison-versions"><strong>版本差异</strong><pre>{JSON.stringify(comparison.version_differences, null, 2)}</pre></div> : <p className="comparison-equal">输入版本相同；若损益也相同，说明固定合成输入下的结果一致。</p>}</section>}</>
}

function SignalEvidence({ report }: { report: BacktestReport }) {
  const evidence = report as BacktestReport & { equity_curve?: Record<string, string>[]; signals?: Record<string, string>[]; orders?: Record<string, string>[] }
  if (!evidence.equity_curve) return null
  return <><section className="report-section"><h3>每日净值（策略驱动）</h3><p>初始权益归一为 1；日终按原始收盘价标记，包含已记费用；负数量为卖出。</p><div className="report-table"><div className="report-row report-head"><span>日期</span><span>权益 / 净值</span><span>现金</span><span>持仓股数</span></div>{evidence.equity_curve.map(row => <div className="report-row" key={row.day}><span>{row.day}</span><span>{row.equity} / {row.nav}</span><span>{row.free_cash}</span><span>{row.position}</span></div>)}</div></section>
    <section className="report-section"><h3>收盘信号与次日订单</h3><p>信号只决定下一交易日订单；日线不推断唯一盘中路径，DAY 剩余数量当日取消。</p>{evidence.signals?.map((row, i) => <p key={i}>{row.day}：目标仓位 {row.target_weight}；{row.reason}</p>)}<div className="report-table"><div className="report-row report-head"><span>执行日</span><span>方向 / 请求</span><span>成交 / 撤余</span><span>状态</span></div>{evidence.orders?.map((row, i) => <div className="report-row" key={i}><span>{row.day}</span><span>{row.side === 'buy' ? '买入' : '卖出'} / {row.requested_quantity}</span><span>{row.filled_quantity} / {row.cancelled_quantity}</span><span>{row.status}</span></div>)}</div></section></>
}

function Metric({ label, value }: { label: string; value: string }) { return <article><span>{label}</span><strong>{value}</strong></article> }
function Loading() { return <section className="empty-state wide"><LoaderCircle className="animate-spin" size={24}/><strong>正在读取已发布工件</strong><p>不会扫描市场数据、执行策略或创建任务。</p></section> }
function Failure({ message, retry }: { message: string; retry: () => void }) { return <section className="empty-state wide"><AlertCircle size={24}/><strong>无法读取回测报告</strong><p>{message}</p><button className="detail-action data-retry" onClick={retry}><RefreshCw size={15}/>重新读取</button></section> }
function Empty({ unavailable }: { unavailable: BacktestReportSummary[] }) { return <section className="empty-state wide"><ShieldAlert size={24}/><strong>暂无可验证的合成验收报告</strong><p>成功的合成任务原子发布工件并通过任务归属、固定路径及哈希核验后，才会显示在这里；不会以示例数据替代。</p>{unavailable.length ? <small>{unavailable.length} 个已索引工件未通过核验，未向页面暴露其内容。</small> : null}</section> }
function NeedTwoReports() { return <section className="empty-state wide"><GitCompareArrows size={24}/><strong>至少需要两份可验证工件</strong><p>结果对比不会复制或生成报告。提交并完成另一项同一合成验收任务后，才能检查合同并比较终值账本。</p></section> }
