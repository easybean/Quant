import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, ArrowRight, CircleCheck, Clock3, LoaderCircle, Search } from 'lucide-react'
import { fetchFactorCatalogue, type Factor, type FactorCatalogueResponse, type FactorStatus } from '../api'

const statusIcon: Record<FactorStatus, typeof CircleCheck> = { 可计算: CircleCheck, 待校准: Clock3, 不可运行: AlertCircle }

export function FactorCatalogue() {
  const [catalogue, setCatalogue] = useState<FactorCatalogueResponse | null>(null)
  const [category, setCategory] = useState('')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    fetchFactorCatalogue(controller.signal)
      .then((payload) => setCatalogue(payload))
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '目录服务暂不可用') })
    return () => controller.abort()
  }, [attempt])

  const factors = catalogue?.items ?? []
  const categories = catalogue?.categories ?? []
  useEffect(() => {
    if (categories.length && !categories.some((item) => item.name === category)) setCategory(categories[0].name)
  }, [categories, category])

  const visible = useMemo(() => factors.filter((factor) => factor.category === category && `${factor.name} ${factor.id} ${factor.tags.join(' ')}`.toLowerCase().includes(query.toLowerCase())), [factors, category, query])
  const selected = visible.find((factor) => factor.id === selectedId)
  useEffect(() => {
    if (!visible.some((factor) => factor.id === selectedId)) setSelectedId(visible[0]?.id ?? '')
  }, [visible, selectedId])
  const chooseCategory = (nextCategory: string) => {
    setCategory(nextCategory)
    setQuery('')
    setSelectedId('')
  }

  return <div className="catalogue-page">
    <section className="page-heading catalogue-heading"><div><p className="eyebrow">研究实验室 · 因子百科</p><h1>因子百科</h1><p>浏览真实目录中的指标定义、输入要求和适用边界。</p></div><span className="static-boundary">真实目录 · 预测能力未验证</span></section>
    <section className="catalogue-layout">
      <aside className="catalogue-categories" aria-label="因子分类"><p>技术因子分类</p>{categories.map((item) => <button type="button" key={item.name} className={item.name === category ? 'is-active' : ''} onClick={() => chooseCategory(item.name)}>{item.name}<span>{item.count}</span></button>)}</aside>
      <section className="factor-list-panel">
        <label className="factor-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} type="search" placeholder={`搜索${category || '因子'}因子`} aria-label="搜索因子" disabled={!catalogue} /></label>
        {error ? <div className="empty-state"><AlertCircle size={22} /><strong>无法加载真实因子目录</strong><p>{error}</p><button className="detail-action" type="button" onClick={() => setAttempt((value) => value + 1)}>重试 <ArrowRight size={15} /></button></div> : !catalogue ? <div className="empty-state"><LoaderCircle size={22} className="animate-spin" /><strong>正在加载真实因子目录</strong><p>正在读取本地只读因子注册表。</p></div> : <><div className="list-caption"><span>{visible.length} / {catalogue.count} 个定义</span><span>目录不代表收益验证</span></div><div className="factor-list">{visible.length ? visible.map((factor) => <FactorRow key={factor.id} factor={factor} selected={selected?.id === factor.id} onSelect={() => setSelectedId(factor.id)} />) : <div className="empty-state"><Search size={22} /><strong>没有匹配的因子</strong><p>请修改搜索条件，或切换左侧分类。</p></div>}</div></>}
      </section>
      <aside className="factor-detail">{selected ? <FactorDetail factor={selected} /> : <div className="empty-state"><Search size={22} /><strong>{error ? '等待目录服务恢复' : catalogue ? '当前分类没有因子' : '等待加载目录'}</strong><p>因子详情只来自真实只读目录。</p></div>}</aside>
    </section>
  </div>
}

function FactorRow({ factor, selected, onSelect }: { factor: Factor; selected: boolean; onSelect: () => void }) {
  const Icon = statusIcon[factor.status]
  return <button type="button" className={`factor-item ${selected ? 'is-selected' : ''}`} onClick={onSelect}><div><strong>{factor.name}</strong><code>{factor.id}</code><p>{factor.description}</p><span>{factor.measurement_type} · {factor.source}</span></div><span className={`factor-status ${factor.status === '不可运行' ? 'danger' : ''}`}><Icon size={14} />{factor.status}</span></button>
}

function FactorDetail({ factor }: { factor: Factor }) {
  return <><div className="detail-header"><div><p>{factor.category}</p><h2>{factor.name}</h2><code>{factor.id}</code></div><span className={`factor-status ${factor.status === '不可运行' ? 'danger' : ''}`}>{factor.status}</span></div><dl><div><dt>定义</dt><dd>{factor.description}</dd></div><div><dt>公式</dt><dd className="formula">{factor.formula}</dd></div><div><dt>用途</dt><dd>{factor.purpose}</dd></div><div><dt>输入</dt><dd>{factor.data_requirements}</dd></div><div><dt>限制与可用性</dt><dd>{factor.limitations} {factor.availability_note}</dd></div></dl><button className="detail-action" type="button" disabled>研究计算未开放 <ArrowRight size={15} /></button></>
}
