import { useEffect, useState } from 'react'
import { AlertCircle, History, KeyRound, LoaderCircle, PencilLine, Save, ShieldAlert } from 'lucide-react'
import { DataSourceDraft, fetchDataSourceDraftHistory, fetchDataSourceDrafts, saveDataSourceDraft } from '../api'

type Form = { name: string; provider: string; market: string; product: string; frequency: string; coverage_declaration: string; license_declaration: string; credential_reference: string }
const options: Record<string, Array<{ market: string; product: string; label: string }>> = {
  alpaca: [{ market: 'US', product: 'US_EQUITY', label: '美股 · 股票' }], nasdaq: [{ market: 'US', product: 'US_EQUITY', label: '美股 · 股票' }],
  binance: [{ market: 'CRYPTO', product: 'CRYPTO_SPOT', label: '加密 · 现货' }, { market: 'CRYPTO', product: 'CRYPTO_PERPETUAL', label: '加密 · 永续' }, { market: 'CRYPTO', product: 'CRYPTO_DELIVERY', label: '加密 · 交割' }],
  ctp: [{ market: 'CN_FUTURES', product: 'CN_COMMODITY_FUTURE', label: '国内期货 · 商品' }, { market: 'CN_FUTURES', product: 'CN_FINANCIAL_FUTURE', label: '国内期货 · 金融' }], ibkr: [{ market: 'US', product: 'US_EQUITY', label: '美股 · 股票' }, { market: 'OVERSEAS_FUTURES', product: 'OVERSEAS_FUTURE', label: '海外期货' }],
}
const blank: Form = { name: '美股数据声明', provider: 'alpaca', market: 'US', product: 'US_EQUITY', frequency: '1d', coverage_declaration: '待由授权与覆盖评估确认；此处不代表可用数据。', license_declaration: '待由授权条款确认；此处不代表许可已验证。', credential_reference: 'ALPACA_DATA_REF' }

function toForm(draft: DataSourceDraft): Form { return { name: draft.name, ...draft.source } }

export function DataSourceWorkspace() {
  const [drafts, setDrafts] = useState<DataSourceDraft[] | null>(null); const [editing, setEditing] = useState<DataSourceDraft | null>(null); const [history, setHistory] = useState<DataSourceDraft[] | null>(null); const [form, setForm] = useState<Form>(blank); const [error, setError] = useState(''); const [saving, setSaving] = useState(false); const [refresh, setRefresh] = useState(0)
  useEffect(() => { const c = new AbortController(); setError(''); fetchDataSourceDrafts(c.signal).then(setDrafts).catch(reason => { if (!c.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取数据源接入草稿') }); return () => c.abort() }, [refresh])
  const change = (key: keyof Form, value: string) => setForm(current => ({ ...current, [key]: value }))
  const changeProvider = (provider: string) => { const choice = options[provider][0]; setForm(current => ({ ...current, provider, market: choice.market, product: choice.product })) }
  const changeProduct = (value: string) => { const choice = options[form.provider].find(item => item.product === value)!; setForm(current => ({ ...current, market: choice.market, product: choice.product })) }
  const edit = async (draft: DataSourceDraft) => { setEditing(draft); setForm(toForm(draft)); setError(''); try { setHistory(await fetchDataSourceDraftHistory(draft.id, new AbortController().signal)) } catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取历史') } }
  const fresh = () => { setEditing(null); setHistory(null); setForm(blank); setError('') }
  const save = async () => { if (saving) return; setSaving(true); setError(''); try { const result = await saveDataSourceDraft({ ...form, verification_status: 'declared' }, editing?.id); setEditing(result); setHistory(await fetchDataSourceDraftHistory(result.id, new AbortController().signal)); setRefresh(value => value + 1) } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败') } finally { setSaving(false) } }
  if (error && !drafts) return <section className="empty-state wide"><AlertCircle size={24}/><strong>数据源接入中心暂不可用</strong><p>{error}</p><button className="detail-action" onClick={() => setRefresh(value => value + 1)}>重新读取</button></section>
  if (!drafts) return <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取接入草稿</strong><p>只读取 SQLite 配置；不会读取凭证、行情或外部账户。</p></section>
  return <div className="data-source-workspace"><section className="page-heading"><div><p className="eyebrow">系统设置 · 版本化声明</p><h1>数据源接入中心</h1><p>记录供应商、市场产品、频率、覆盖与许可声明；所有记录都是“已声明”，并非已验证的接入或数据覆盖。</p></div><span className="static-boundary">声明草稿 · 不可执行</span></section>
    {error && <div className="strategy-error"><AlertCircle size={17}/>{error}</div>}
    <section className="strategy-boundary"><ShieldAlert size={18}/><div><strong>没有凭证输入框，也不会检测连接</strong><p>仅保存服务器端凭证引用名（例如 ALPACA_DATA_REF）。不要输入密钥、令牌、密码、文件路径或账户信息；本页不会读取、验证或调用任何供应商。</p></div></section>
    <div className="data-source-grid"><aside className="data-source-drafts"><header><strong>接入草稿</strong><span>{drafts.length} 项</span></header>{drafts.length ? drafts.map(item => <button key={item.id} className={editing?.id === item.id ? 'is-active' : ''} onClick={() => edit(item)}><span><strong>{item.name}</strong><small>{item.source.provider} · {item.source.product} · 已声明 v{item.version}</small></span><PencilLine size={14}/></button>) : <p>尚无草稿。填写右侧声明即可创建首版。</p>}</aside>
      <section className="data-source-editor"><header><div><h2>{editing ? `编辑 ${editing.name}` : '新建数据源接入草稿'}</h2><p>供应商和市场/产品组合由服务端白名单校验；保存不会改变任何外部系统。</p></div>{editing ? <button className="detail-action" onClick={fresh}>新建副本</button> : <span>已声明</span>}</header><div className="data-source-form">
        <label>草稿名称<input maxLength={120} value={form.name} onChange={e => change('name', e.target.value)} /></label><label>供应商<select value={form.provider} onChange={e => changeProvider(e.target.value)}>{Object.keys(options).map(item => <option key={item} value={item}>{item}</option>)}</select></label>
        <label>市场 / 产品<select value={form.product} onChange={e => changeProduct(e.target.value)}>{options[form.provider].map(item => <option key={item.product} value={item.product}>{item.label}</option>)}</select><small>市场固定为 {form.market}。</small></label><label>声明频率<select value={form.frequency} onChange={e => change('frequency', e.target.value)}>{['1m', '5m', '1h', '1d'].map(item => <option key={item}>{item}</option>)}</select></label>
        <label>覆盖声明<textarea maxLength={1000} value={form.coverage_declaration} onChange={e => change('coverage_declaration', e.target.value)} /><small>写明预期范围与未知项；不会被当作实际覆盖。</small></label><label>许可声明<textarea maxLength={1000} value={form.license_declaration} onChange={e => change('license_declaration', e.target.value)} /><small>写明待确认的许可边界；不会被当作已获授权。</small></label>
        <label className="data-source-reference"><KeyRound size={15}/>服务器凭证引用名<input maxLength={80} value={form.credential_reference} onChange={e => change('credential_reference', e.target.value.toUpperCase())} /><small>仅大写字母、数字、下划线；不是路径，不是凭证值。</small></label><label>验证状态<input value="已声明（固定，未验证）" disabled /></label>
      </div><button className="strategy-save" disabled={saving} onClick={save}><Save size={15}/>{saving ? '保存中…' : editing ? '保存新版本' : '创建接入草稿'}</button></section></div>
    {history && <section className="strategy-history"><header><div><History size={17}/><strong>{editing?.name} 的声明历史</strong></div><span>{history.length} 个版本</span></header>{history.map(item => <div key={item.id}><strong>v{item.version}</strong><span>{item.source.provider} · {item.source.market}/{item.source.product} · {item.source.frequency} · 已声明</span><time>{new Date(item.created_at).toLocaleString()}</time></div>)}</section>}
  </div>
}
