import { useEffect, useState } from 'react'
import { AlertCircle, LoaderCircle, Search, ShieldAlert } from 'lucide-react'
import { fetchSecurityCatalogue, type SecurityCatalogue, type SecurityRecord } from '../api'
import { InstrumentWorkspace } from './UniverseWorkspace'

const display = (value: string | null | undefined) => value || '来源未提供'
export function SecurityCatalogueWorkspace() {
  const [query, setQuery] = useState('')
  const [appliedQuery, setAppliedQuery] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [data, setData] = useState<SecurityCatalogue | null>(null)
  const [selected, setSelected] = useState<SecurityRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    fetchSecurityCatalogue(appliedQuery, controller.signal).then(result => { setData(result); setSelected(result.items[0] ?? null) }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '证券目录读取失败') }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [appliedQuery, attempt])
  const search = () => { setAppliedQuery(query.trim()); setAttempt(value => value + 1) }
  return <div className="universe-workspace">
    <section className="page-heading"><div><p className="eyebrow">行情与资料 · 自动证券档案</p><h1>证券档案</h1><p>证券资料由系统从现有美股主表自动建立，你只需搜索、查看，不必填写身份编号、时区或交易规则。</p></div><span className="static-boundary">自动建档 · 只读</span></section>
    <section className="strategy-boundary"><ShieldAlert size={18}/><div><strong>证券资料与研究资格分开</strong><p>保留活跃与历史退市证券的不同记录；内部编号仅用于追溯来源记录，不是已验证的永久证券身份。交易规则和行情归属尚未核验的记录不会自动变成合格回测资产。</p></div></section>
    <section className="quote-controls" aria-label="证券档案搜索"><label><Search size={16}/><input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') search() }} maxLength={120} aria-label="股票代码或名称" placeholder="搜索代码或名称，例如 TSLA"/></label><button className="quote-primary" onClick={search} disabled={loading}>{loading ? '读取中…' : '搜索档案'}</button><span className="security-catalogue-count">{data ? `已建档 ${data.total_records.toLocaleString()} 条来源记录 · 隔离 ${data.quarantined_records} 条测试记录` : '正在读取自动目录'}</span></section>
    {error ? <section className="empty-state wide"><AlertCircle size={24}/><strong>证券目录暂不可用</strong><p>{error}</p><button className="detail-action" onClick={() => setAttempt(value => value + 1)}>重试</button></section> : loading ? <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取证券档案</strong><p>只读取已导入目录，不下载行情或创建交易任务。</p></section> : data && <>
<div className="universe-grid security-catalogue-grid"><aside className="universe-drafts"><header><strong>证券档案</strong><span>{data.total} 条匹配</span></header>{data.items.map(item => <button key={item.catalog_id} className={selected?.catalog_id === item.catalog_id ? 'is-active' : ''} onClick={() => setSelected(item)}><span><strong>{item.symbol} · {item.name}</strong><small>{display(item.exchange)} · {item.status === 'active' ? '主表标记活跃' : item.status === 'delisted' ? '历史退市' : item.status}</small></span></button>)}{!data.items.length && <p>{data.total_records ? '未找到匹配证券，请换一个代码或名称。' : '证券目录尚未导入，请由系统维护人员执行自动建档，不需要你手工创建。'}</p>}{data.total > data.items.length && <p>显示前 {data.items.length} 条；输入代码或名称缩小范围，未缩减建档数量。</p>}</aside><section className="universe-editor">{selected ? <><header><div><h2>{selected.symbol} · {selected.name}</h2><p>下面的资料来自主表；缺失字段保留未知，不由系统猜测。</p></div></header><dl className="security-record-details">{[
['上市市场', display(selected.exchange)], ['证券类型', selected.asset_type.toLowerCase() === 'etf' ? 'ETF' : selected.asset_type.toLowerCase() === 'stock' ? '股票' : selected.asset_type],
        ['主表状态', selected.status === 'active' ? '活跃（不等于当前可交易）' : selected.status === 'delisted' ? '历史退市' : selected.status],
        ['来源记载上市日期', display(selected.ipo_date)], ['来源记载退市日期', display(selected.delisting_date)], ['来源资料截至', display(selected.source_as_of)],
        ['身份核验', '待核验（内部来源记录编号）'], ['历史行情绑定', '待核验'], ['交易规则 / 日历', '待核验，不使用未经证实的统一规格'],
        ['来源', display(selected.source)], ['内部档案编号', selected.catalog_id],
      ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p className="security-record-note">股票池中的正式证券映射与合格研究快照仍需系统核验。不会要求你补填技术字段，也不会把当前主表名单冒充历史指数成分股。</p></> : <div className="empty-state"><Search size={24}/><strong>没有可展示的档案</strong><p>输入代码或名称搜索。</p></div>}</section></div>
    </>}
    <details className="instrument-advanced"><summary>维护工具：旧版手工定义与历史草稿（普通使用不需要）</summary><p>仅保留现有草稿的维护入口，不作为自动建档步骤。</p><InstrumentWorkspace/></details>
  </div>
}
