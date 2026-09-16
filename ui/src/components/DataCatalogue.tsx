import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, ArrowRight, Database, LoaderCircle, RefreshCw, ShieldAlert } from 'lucide-react'
import { fetchDataCatalogue, type DataCatalogueResponse } from '../api'

const findingLabels: Record<string, string> = {
  missing_required_value: '必需字段缺失',
  ohlc_relation_invalid: 'OHLC 关系异常',
  source_conflict_unmerged: '未合并来源冲突',
  supplier_download_gap: '供应商下载缺口',
  ticker_lifecycle_reuse_candidate: '代码生命周期复用候选',
}

const number = new Intl.NumberFormat('zh-CN')
const formatDate = (value: string) => value ? value.replace('T', ' ').replace(/\.\d+Z$/, ' UTC').replace('Z', ' UTC') : '未记录'

export function DataCatalogue() {
  const [catalogue, setCatalogue] = useState<DataCatalogueResponse | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [finding, setFinding] = useState('all')

  useEffect(() => {
    const controller = new AbortController()
    setCatalogue(null)
    setError('')
    fetchDataCatalogue(controller.signal)
      .then(setCatalogue)
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '数据目录暂不可用') })
    return () => controller.abort()
  }, [attempt])

  const findings = useMemo(() => Object.entries(catalogue?.quality.findings ?? {}).map(([key, count]) => ({ key, count, label: findingLabels[key] ?? key })), [catalogue])
  const selectedFindings = finding === 'all' ? findings : findings.filter((item) => item.key === finding)

  return <div className="data-catalogue-page">
    <section className="page-heading">
      <div><p className="eyebrow">市场与数据 · 只读目录</p><h1>数据目录</h1><p>展示预生成的数据盘点、质量与版本快照；刷新页面不会扫描行情或启动下载。</p></div>
      <span className="static-boundary">只读快照 · 不提供下载入口</span>
    </section>
    {error ? <CatalogueState icon={<AlertCircle size={24} />} title="无法加载数据目录" message={error} retry={() => setAttempt((value) => value + 1)} /> : !catalogue ? <CatalogueState icon={<LoaderCircle size={24} className="animate-spin" />} title="正在加载数据目录" message="正在读取预生成的盘点和质量快照。" /> : <>
      <section className="catalogue-meta"><span><RefreshCw size={14} />快照更新时间：{formatDate(catalogue.generated_at)}</span><span>市场：{catalogue.inventory.market || '未记录'}</span><span>频率：{catalogue.inventory.frequency || '未记录'}</span></section>
      <section className="data-metric-grid" aria-label="数据覆盖摘要">
        <Metric label="原始日线文件" value={catalogue.inventory.raw_files} detail={`${catalogue.inventory.date_range.start} 至 ${catalogue.inventory.date_range.end}`} />
        <Metric label="派生结构文件" value={catalogue.inventory.derived_files} detail={`结构版本：${catalogue.quality.structural.cleaning_status || '未记录'}`} />
        <Metric label="唯一代码" value={catalogue.inventory.unique_symbols} detail={`生命周期：${number.format(catalogue.inventory.lifecycle_records)}`} />
        <Metric label="审计发现" value={catalogue.quality.findings_rows} detail={`规则：${catalogue.quality.rule_version || '未记录'}`} warning />
      </section>
      <section className="data-catalogue-grid">
        <article className="data-card coverage-card"><header><div><h2>覆盖与来源</h2><p>盘点索引汇总，不代表完整全市场覆盖。</p></div><Database size={18} /></header><div className="source-table"><div className="source-head"><span>来源/分区</span><span>文件</span><span>覆盖区间</span><span>价格口径</span></div>{catalogue.inventory.sources.map((source) => <div className="source-row" key={source.name}><strong>{source.name}</strong><span>{number.format(source.files)}</span><span>{source.date_range.start} — {source.date_range.end}</span><span>{source.adjustment_status}</span></div>)}</div><p className="data-note">基准：{catalogue.inventory.benchmarks.length ? catalogue.inventory.benchmarks.join('、') : '未记录'}。同代码跨来源文件保持分开，未自动拼接。</p></article>
        <article className="data-card"><header><div><h2>版本与研究边界</h2><p>仅可追溯的版本可进入后续研究。</p></div></header><div className="version-list">{catalogue.inventory.versions.map((version) => <div key={`${version.name}-${version.version}`}><strong>{version.name}</strong><code>{version.version}</code><span>{version.scope}</span></div>)}</div><div className="boundary-callout"><ShieldAlert size={17} /><p>{catalogue.corporate_actions.scope} 当前 {catalogue.corporate_actions.event_count} 条事件、{catalogue.corporate_actions.golden_checks_passed}/{catalogue.corporate_actions.golden_checks_total} 个黄金样本通过；退市最终兑付：{catalogue.corporate_actions.delisting_resolution || 'unknown'}。</p></div></article>
      </section>
      <section className="data-card quality-card"><header><div><h2>质量与缺口</h2><p>按质量规则快照筛选汇总；每个数字均是发现数，不等同于可自动修复的数据。</p></div></header><div className="finding-filters" role="group" aria-label="质量发现筛选"><button className={finding === 'all' ? 'is-active' : ''} type="button" onClick={() => setFinding('all')}>全部 <span>{findings.length}</span></button>{findings.map((item) => <button key={item.key} className={finding === item.key ? 'is-active' : ''} type="button" onClick={() => setFinding(item.key)}>{item.label}<span>{number.format(item.count)}</span></button>)}</div><div className="finding-results">{selectedFindings.map((item) => <div key={item.key}><strong>{item.label}</strong><span>{number.format(item.count)} 条发现</span><p>{findingExplanation(item.key)}</p></div>)}</div>{catalogue.quality.limitations.length ? <div className="limitations"><strong>当前限制</strong><ul>{catalogue.quality.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}</section>
    </>}
  </div>
}

function Metric({ label, value, detail, warning = false }: { label: string; value: number; detail: string; warning?: boolean }) {
  return <article className={`data-metric ${warning ? 'is-warning' : ''}`}><span>{label}</span><strong>{number.format(value)}</strong><small>{detail}</small></article>
}

function CatalogueState({ icon, title, message, retry }: { icon: React.ReactNode; title: string; message: string; retry?: () => void }) {
  return <section className="empty-state wide">{icon}<strong>{title}</strong><p>{message}</p>{retry ? <button className="detail-action data-retry" type="button" onClick={retry}>重新读取快照 <ArrowRight size={15} /></button> : null}</section>
}

function findingExplanation(key: string) {
  if (key === 'supplier_download_gap') return '供应商请求的最新失败记录，不等同于停牌、休市或退市最终结果。'
  if (key === 'source_conflict_unmerged') return '同一代码在多个来源/分区有文件，系统未将其拼接成单一价格序列。'
  if (key === 'ticker_lifecycle_reuse_candidate') return '同一 ticker 可能对应多个生命周期，尚未完成稳定身份映射。'
  return '保留在质量报告中，后续研究应显式处理或隔离。'
}
