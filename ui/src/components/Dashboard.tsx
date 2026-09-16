import { useEffect, useState } from 'react'
import { AlertCircle, Database, FlaskConical, LoaderCircle, RefreshCw, ServerCog, ShieldAlert } from 'lucide-react'
import { fetchDataCatalogue, fetchFactorCatalogue, fetchHealth, type DataCatalogueResponse, type FactorCatalogueResponse, type HealthResponse } from '../api'

type DashboardSnapshot = { data: DataCatalogueResponse; factors: FactorCatalogueResponse; health: HealthResponse }

const number = new Intl.NumberFormat('zh-CN')
const formatDate = (value: string) => value ? value.replace('T', ' ').replace(/\.\d+Z$/, ' UTC').replace('Z', ' UTC') : '未记录'

export function Dashboard() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setSnapshot(null)
    setError('')
    Promise.all([fetchDataCatalogue(controller.signal), fetchFactorCatalogue(controller.signal), fetchHealth(controller.signal)])
      .then(([data, factors, health]) => setSnapshot({ data, factors, health }))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '总览快照暂不可用')
      })
    return () => controller.abort()
  }, [attempt])

  return <div className="dashboard">
    <section className="page-heading">
      <div><p className="eyebrow">总览 · 只读研究状态</p><h1>研究工作台</h1><p>仅展示已发布的数据、因子目录与服务快照；不生成净值、不启动实验或交易。</p></div>
      <span className="static-boundary">只读快照 · 无模拟订单</span>
    </section>
    {error ? <DashboardState icon={<AlertCircle size={24} />} title="无法加载工作台快照" message={error} retry={() => setAttempt((value) => value + 1)} /> : !snapshot ? <DashboardState icon={<LoaderCircle size={24} className="animate-spin" />} title="正在加载工作台快照" message="正在读取预生成数据目录、因子目录和只读服务状态。" /> : <DashboardContent snapshot={snapshot} />}
  </div>
}

function DashboardContent({ snapshot }: { snapshot: DashboardSnapshot }) {
  const { data, factors, health } = snapshot
  const metrics = [
    { label: '原始日线文件', value: number.format(data.inventory.raw_files), unit: '份', note: `${data.inventory.market || '市场未记录'} · ${data.inventory.frequency || '频率未记录'} · 盘点快照`, icon: Database },
    { label: '因子定义', value: number.format(factors.count), unit: '项', note: `${factors.categories.length} 个主分类 · 因子目录快照`, icon: FlaskConical },
    { label: '质量发现', value: number.format(data.quality.findings_rows), unit: '条', note: `${data.quality.rule_version || '规则版本未记录'} · 不等同于可修复数量`, icon: ShieldAlert },
    { label: '只读 API', value: health.status === 'ok' ? '在线' : '未知', unit: health.schema_version, note: '健康端点实时响应；不代表运行器或交易连接', icon: ServerCog },
  ]
  const states = [
    ['数据目录', '已就绪', `固定快照 · ${formatDate(data.generated_at)}`],
    ['因子目录', '已就绪', `${number.format(factors.count)} 项 · ${formatDate(factors.generated_at)}`],
    ['回测运行器', '未配置', '暂无已发布的运行器状态或实例'],
    ['模拟交易', '未启用', '暂无已发布的账户、订单或成交快照'],
  ]
  return <>
    <section className="catalogue-meta dashboard-meta"><span><RefreshCw size={14} />数据快照：{formatDate(data.generated_at)}</span><span>因子目录：{formatDate(factors.generated_at)}</span><span>覆盖：{data.inventory.date_range.start || '未记录'} 至 {data.inventory.date_range.end || '未记录'}</span></section>
    <section className="metric-grid" aria-label="工作台真实指标">
      {metrics.map((metric) => { const Icon = metric.icon; return <article className="metric-card" key={metric.label}>
        <div className="metric-icon"><Icon size={18} /></div><div><p>{metric.label}</p><strong className="numeric">{metric.value}</strong><span>{metric.unit}</span></div><small>{metric.note}</small>
      </article> })}
    </section>
    <section className="dashboard-grid">
      <article className="panel chart-panel">
        <div className="panel-heading"><div><h2>组合净值</h2><p>尚未接入已验证的组合运行与基准快照。</p></div><span className="static-boundary">暂无数据</span></div>
        <EmptyPanel title="暂无组合净值" message="回测运行、模拟账户和净值序列尚未生成；系统不会用示例曲线或零值代替。" />
      </article>
      <article className="panel status-panel">
        <div className="panel-heading"><div><h2>系统状态</h2><p>数据和目录状态来自本次只读响应。</p></div></div>
        <ul className="status-list">
          {states.map(([name, status, source]) => <li key={name}><span><i className={`status-dot ${status === '已就绪' ? 'ready' : 'neutral'}`} />{name}</span><div className="dashboard-status-copy"><strong>{status}</strong><small>{source}</small></div></li>)}
        </ul>
        <div className="panel-note">“未配置”和“未启用”表示没有可展示的真实运行快照，不代表数值为 0。</div>
      </article>
    </section>
    <section className="panel experiments-panel">
      <div className="panel-heading"><div><h2>近期实验</h2><p>尚未建立实验记录库，因此不显示占位策略或虚构运行记录。</p></div><span className="static-boundary">暂无记录</span></div>
      <EmptyPanel title="暂无已发布实验" message="完成可复现实验并生成结果快照后，才会在这里显示参数、数据范围、时间和指标。" compact />
    </section>
  </>
}

function DashboardState({ icon, title, message, retry }: { icon: React.ReactNode; title: string; message: string; retry?: () => void }) {
  return <section className="empty-state wide">{icon}<strong>{title}</strong><p>{message}</p>{retry ? <button className="detail-action data-retry" type="button" onClick={retry}>重新读取快照 <RefreshCw size={15} /></button> : null}</section>
}

function EmptyPanel({ title, message, compact = false }: { title: string; message: string; compact?: boolean }) {
  return <div className={`dashboard-empty ${compact ? 'compact' : ''}`}><strong>{title}</strong><p>{message}</p></div>
}
