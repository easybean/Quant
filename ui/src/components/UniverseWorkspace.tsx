import { useEffect, useState } from 'react'
import { AlertCircle, History, LoaderCircle, PencilLine, Save, ShieldAlert } from 'lucide-react'
import { InstrumentDraft, fetchInstrumentDraftHistory, fetchInstrumentDrafts, saveInstrumentDraft } from '../api'

type Form = Record<string, string>
const blank: Form = { name: '示例美股定义', product: 'US_EQUITY', venue: 'NYSE', identity_key: 'US-EXAMPLE-001', venue_symbol: 'EXM', base_currency: 'USD', quote_currency: 'USD', settlement_currency: 'USD', expiry: '', timezone: 'America/New_York', calendar_id: 'US-EQUITY-DRAFT', valid_from: '2024-01-01', rule_version: 'draft-v1', quantity_unit: 'share', multiplier: '1', tick_size: '0.01', lot_size: '1', min_notional: '' }
const derivatives = new Set(['CN_FUTURE', 'GLOBAL_FUTURE', 'CRYPTO_PERPETUAL', 'CRYPTO_DELIVERY'])
const expiryRequired = new Set(['CN_FUTURE', 'GLOBAL_FUTURE', 'CRYPTO_DELIVERY'])
const crypto = new Set(['CRYPTO_SPOT', 'CRYPTO_PERPETUAL', 'CRYPTO_DELIVERY'])
const products = [['US_EQUITY', '美股'], ['CRYPTO_SPOT', '加密现货'], ['CRYPTO_PERPETUAL', '加密永续'], ['CRYPTO_DELIVERY', '加密交割'], ['CN_FUTURE', '国内期货'], ['GLOBAL_FUTURE', '海外期货']]
const field = (form: Form, key: string) => form[key] || ''

function formToInstrument(form: Form): Record<string, unknown> {
  const product = form.product
  const instrument: Record<string, unknown> = { instrument_id: `${form.venue}:${form.identity_key}`, identity_key: form.identity_key, asset: product === 'US_EQUITY' ? 'equity' : (crypto.has(product) ? 'crypto' : 'future'), product, venue: form.venue, venue_symbol: form.venue_symbol, timezone: form.timezone, calendar_id: form.calendar_id, linear: derivatives.has(product) && form.contract_model === 'linear', inverse: derivatives.has(product) && form.contract_model === 'inverse', symbol_history: [{ venue_symbol: form.venue_symbol, valid_from: form.valid_from }], rules: [{ valid_from: form.valid_from, rule_version: form.rule_version, quantity_unit: form.quantity_unit, tick_size: form.tick_size, lot_size: form.lot_size }] }
  if (crypto.has(product)) Object.assign(instrument, { base_currency: form.base_currency, quote_currency: form.quote_currency, settlement_currency: form.settlement_currency })
  if (derivatives.has(product)) Object.assign((instrument.rules as Record<string, unknown>[])[0], { multiplier: form.multiplier })
  if (expiryRequired.has(product)) instrument.expiry = form.expiry
  return instrument
}
function instrumentToForm(draft: InstrumentDraft): Form {
  const item = draft.instrument; const rule = (item.rules as Record<string, unknown>[] || [])[0] || {}; const symbol = (item.symbol_history as Record<string, unknown>[] || [])[0] || {}
  return { ...blank, name: draft.name, product: String(item.product || blank.product), venue: String(item.venue || ''), identity_key: String(item.identity_key || ''), venue_symbol: String(item.venue_symbol || ''), base_currency: String(item.base_currency || ''), quote_currency: String(item.quote_currency || ''), settlement_currency: String(item.settlement_currency || ''), expiry: String(item.expiry || ''), timezone: String(item.timezone || ''), calendar_id: String(item.calendar_id || ''), valid_from: String(symbol.valid_from || rule.valid_from || ''), rule_version: String(rule.rule_version || ''), quantity_unit: String(rule.quantity_unit || ''), multiplier: String(rule.multiplier || '1'), tick_size: String(rule.tick_size || ''), lot_size: String(rule.lot_size || '') }
}

export function InstrumentWorkspace() {
  const [drafts, setDrafts] = useState<InstrumentDraft[] | null>(null); const [editing, setEditing] = useState<InstrumentDraft | null>(null); const [history, setHistory] = useState<InstrumentDraft[] | null>(null); const [form, setForm] = useState<Form>(blank); const [error, setError] = useState(''); const [saving, setSaving] = useState(false); const [refresh, setRefresh] = useState(0)
  useEffect(() => { const c = new AbortController(); setError(''); fetchInstrumentDrafts(c.signal).then(setDrafts).catch(reason => { if (!c.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取标的定义草稿') }); return () => c.abort() }, [refresh])
  const change = (key: string, value: string) => setForm(current => ({ ...current, [key]: value }))
  const edit = async (draft: InstrumentDraft) => { setEditing(draft); setForm(instrumentToForm(draft)); setError(''); try { setHistory(await fetchInstrumentDraftHistory(draft.id, new AbortController().signal)) } catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取历史') } }
  const save = async () => { if (saving) return; setSaving(true); setError(''); try { const result = await saveInstrumentDraft({ name: form.name, instrument: formToInstrument(form) }, editing?.id); setEditing(result); setHistory(await fetchInstrumentDraftHistory(result.id, new AbortController().signal)); setRefresh(value => value + 1) } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败') } finally { setSaving(false) } }
  if (error && !drafts) return <section className="empty-state wide"><AlertCircle size={24}/><strong>标的定义暂不可用</strong><p>{error}</p><button className="detail-action" onClick={() => setRefresh(value => value + 1)}>重新读取</button></section>
  if (!drafts) return <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取配置草稿</strong><p>只读取 SQLite 草稿；不会读取行情或连接账户。</p></section>
  const isCrypto = crypto.has(form.product); const isDerivative = derivatives.has(form.product)
  return <div className="universe-workspace"><section className="page-heading"><div><p className="eyebrow">市场与数据 · 版本化定义</p><h1>资产与合约</h1><p>保存单个证券、交易对或期货合约的稳定身份和基础规则；不代表行情覆盖或可交易性。</p></div><span className="static-boundary">定义草稿 · 不可执行</span></section>
    {error && <div className="strategy-error"><AlertCircle size={17}/>{error}</div>}
    <section className="strategy-boundary"><ShieldAlert size={18}/><div><strong>仅保存参考配置</strong><p>不会读取行情、声明数据覆盖、创建回测或交易任务，也不会连接外部账户。</p></div></section>
    <div className="universe-grid"><aside className="universe-drafts"><header><strong>已保存标的</strong><span>{drafts.length} 项</span></header>{drafts.length ? drafts.map(item => <button key={item.id} className={editing?.id === item.id ? 'is-active' : ''} onClick={() => edit(item)}><span><strong>{item.name}</strong><small>{String(item.instrument.product)} · v{item.version}</small></span><PencilLine size={14}/></button>) : <p>尚无标的定义。保存一个单标的或合约后，才可在研究资产池中引用它。</p>}</aside>
      <section className="universe-editor"><header><div><h2>{editing ? `编辑 ${editing.name}` : '新建标的定义'}</h2><p>每个草稿只描述一个标的或合约；更新会保留可引用的历史版本。</p></div><span>v{editing?.version || 1}</span></header><div className="universe-form">
        <label>定义名称<input value={field(form, 'name')} onChange={e => change('name', e.target.value)} /></label><label>市场产品<select value={form.product} onChange={e => change('product', e.target.value)}>{products.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label>市场 / venue<input value={field(form, 'venue')} onChange={e => change('venue', e.target.value)} /></label><label>稳定身份键 <small>不能使用 ticker</small><input value={field(form, 'identity_key')} onChange={e => change('identity_key', e.target.value)} /></label>
        <label>当前交易代码<input value={field(form, 'venue_symbol')} onChange={e => change('venue_symbol', e.target.value)} /></label><label>市场时区<input value={field(form, 'timezone')} onChange={e => change('timezone', e.target.value)} /></label>
        <label>日历标识<input value={field(form, 'calendar_id')} onChange={e => change('calendar_id', e.target.value)} /></label><label>规则生效日<input type="date" value={field(form, 'valid_from')} onChange={e => change('valid_from', e.target.value)} /></label>
        {isCrypto && <><label>基础币种<input value={field(form, 'base_currency')} onChange={e => change('base_currency', e.target.value)} /></label><label>报价币种<input value={field(form, 'quote_currency')} onChange={e => change('quote_currency', e.target.value)} /></label><label>结算币种<input value={field(form, 'settlement_currency')} onChange={e => change('settlement_currency', e.target.value)} /></label></>}
        {isDerivative && <><label>合约模型<select value={field(form, 'contract_model') || 'linear'} onChange={e => change('contract_model', e.target.value)}><option value="linear">线性</option><option value="inverse">反向</option></select></label><label>合约乘数<input inputMode="decimal" value={field(form, 'multiplier')} onChange={e => change('multiplier', e.target.value)} /></label></>}
        {expiryRequired.has(form.product) && <label>到期日<input type="date" value={field(form, 'expiry')} onChange={e => change('expiry', e.target.value)} /></label>}
        <label>规则版本<input value={field(form, 'rule_version')} onChange={e => change('rule_version', e.target.value)} /></label><label>数量单位<input value={field(form, 'quantity_unit')} onChange={e => change('quantity_unit', e.target.value)} /></label><label>最小变动价位<input inputMode="decimal" value={field(form, 'tick_size')} onChange={e => change('tick_size', e.target.value)} /></label><label>最小下单单位<input inputMode="decimal" value={field(form, 'lot_size')} onChange={e => change('lot_size', e.target.value)} /></label>
      </div><button className="strategy-save" disabled={saving} onClick={save}><Save size={15}/>{saving ? '保存中…' : editing ? '保存新版本' : '创建标的定义'}</button></section></div>
    {history && <section className="strategy-history"><header><div><History size={17}/><strong>{editing?.name} 的定义历史</strong></div><span>{history.length} 个版本</span></header>{history.map(item => <div key={item.id}><strong>v{item.version}</strong><span>{String(item.instrument.product)} · {String(item.instrument.venue_symbol)}</span><time>{new Date(item.created_at).toLocaleString()}</time></div>)}</section>}
  </div>
}
