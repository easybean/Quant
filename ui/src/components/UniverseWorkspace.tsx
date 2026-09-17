import { useEffect, useState } from 'react'
import { AlertCircle, History, LoaderCircle, PencilLine, Save, ShieldAlert } from 'lucide-react'
import { InstrumentDraft, fetchInstrumentDraftHistory, fetchInstrumentDrafts, saveInstrumentDraft } from '../api'
import { backendFieldErrors, FieldError, focusFirstError, labelledField, RequiredMark } from './FormValidation'
import { isIsoDate } from '../inputValidation'
import { idempotencyKey } from '../idempotency'

type Form = Record<string, string>
const blank: Form = { name: '', product: 'US_EQUITY', venue: '', identity_key: '', venue_symbol: '', base_currency: 'USD', quote_currency: 'USD', settlement_currency: 'USD', expiry: '', timezone: 'America/New_York', calendar_id: 'US-EQUITY-DRAFT', valid_from: '', rule_version: 'draft-v1', quantity_unit: 'share', contract_model: 'linear', multiplier: '1', tick_size: '0.01', lot_size: '1', min_notional: '' }
const hints: Record<string, string> = {
  name: '方便你识别的名称，例如“特斯拉普通股”；只是显示名称，不决定行情来源。',
  product: '当前只做美股，请选“美股”。其他产品仅保留旧草稿兼容，不表示已经支持研究或交易。',
  venue: '这只证券的上市市场，不是券商或数据源。按证券资料确认后选择；不知道时不要猜，系统不会从代码自动认定。',
  venue_symbol: '你搜索行情时使用的代码，例如 TSLA。代码可能改名或被其他公司复用，因此不等于永久身份。',
  identity_key: '系统内部的永久档案编号，用来区分不同证券生命周期。新建可点击生成；它不是官方证券标识，也不会自动绑定历史行情。修改旧草稿时通常保留原编号。',
  timezone: '解释当地交易时间所用的时区。美股草稿预设纽约时区；不是你电脑的北京时间。',
  calendar_id: '系统用来引用交易日、休市和交易时段规则的名字。当前只有美股草稿占位，选择它不表示真实交易日历已核验。',
  valid_from: '从哪天开始，交易代码映射和这组规则才生效。当前表单两者共用此日期；不是行情下载起点，也不要随意写成上市日。历史有效日期需有资料依据。',
  rule_version: '这组交易规则的版本标签，用于追溯，例如 draft-v1。保存时左上角v1/v2是草稿修订号，两者不是同一个版本。',
  quantity_unit: '数量的计量单位。美股按“股”记录；并不意味着券商支持碎股。',
  tick_size: '报价允许变化的最小步长，例如0.01表示一美分。当前值只是未核验草稿预设，实际规则可能因证券、价格和历史时点不同；应根据适用规则确认。',
  lot_size: '本配置允许的数量步长，例如1表示数量按整数股递增，不是买入金额。是否支持碎股取决于具体交易场所和券商，不能由此选项推断。',
  base_currency: '交易对左侧资产，例如 BTC/USD 中的 BTC；不是账户资金。',
  quote_currency: '价格的计价币种，例如 BTC/USD 中的 USD。',
  settlement_currency: '盈亏、费用或结算使用的币种，需按合约规则确认。',
  contract_model: '线性或反向决定合约计价与盈亏方式；必须以合约资料为准，不可仅凭名称选择。',
  multiplier: '每个合约单位对应的价值换算系数，需查合约规格；不是杠杆倍数。',
  expiry: '合约终止的日期，按交易所资料填写；不是回测结束日期。',
}
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
  return { ...blank, contract_model: item.inverse ? 'inverse' : 'linear', name: draft.name, product: String(item.product || blank.product), venue: String(item.venue || ''), identity_key: String(item.identity_key || ''), venue_symbol: String(item.venue_symbol || ''), base_currency: String(item.base_currency || ''), quote_currency: String(item.quote_currency || ''), settlement_currency: String(item.settlement_currency || ''), expiry: String(item.expiry || ''), timezone: String(item.timezone || ''), calendar_id: String(item.calendar_id || ''), valid_from: String(symbol.valid_from || rule.valid_from || ''), rule_version: String(rule.rule_version || ''), quantity_unit: String(rule.quantity_unit || ''), multiplier: String(rule.multiplier || '1'), tick_size: String(rule.tick_size || ''), lot_size: String(rule.lot_size || '') }
}

export function InstrumentWorkspace() {
  const [drafts, setDrafts] = useState<InstrumentDraft[] | null>(null); const [editing, setEditing] = useState<InstrumentDraft | null>(null); const [history, setHistory] = useState<InstrumentDraft[] | null>(null); const [form, setForm] = useState<Form>(blank); const [error, setError] = useState(''); const [saving, setSaving] = useState(false); const [refresh, setRefresh] = useState(0); const [formErrors, setFormErrors] = useState<Record<string, string>>({})
  useEffect(() => { const c = new AbortController(); setError(''); fetchInstrumentDrafts(c.signal).then(setDrafts).catch(reason => { if (!c.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取标的定义草稿') }); return () => c.abort() }, [refresh])
  const change = (key: string, value: string) => { setForm(current => ({ ...current, [key]: value })); setFormErrors(v => ({ ...v, [key]: '' })) }
  const edit = async (draft: InstrumentDraft) => { setFormErrors({}); setEditing(draft); setForm(instrumentToForm(draft)); setError(''); try { setHistory(await fetchInstrumentDraftHistory(draft.id, new AbortController().signal)) } catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取历史') } }
  const save = async () => { if (saving) return; setError(''); const errors: Record<string, string> = {}; const ids = Object.fromEntries(Object.keys(form).map(key => [key, `instrument-${key}`])); for (const key of ['name', 'venue', 'identity_key', 'venue_symbol', 'timezone', 'calendar_id', 'valid_from', 'rule_version', 'quantity_unit', 'tick_size', 'lot_size'] as const) if (!form[key].trim()) errors[key] = '此字段为必填项。'; if (form.name.trim().length > 120) errors.name = '定义名称为 1–120 个字符。'; if (!isIsoDate(form.valid_from)) errors.valid_from = '请输入有效的 YYYY-MM-DD 日期。'; for (const key of ['tick_size', 'lot_size'] as const) if (!/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(form[key]) || Number(form[key]) <= 0) errors[key] = '请输入大于 0 的十进制数。'; if (isDerivative && (!/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(form.multiplier) || Number(form.multiplier) <= 0)) errors.multiplier = '合约乘数必须为大于 0 的十进制数。'; if (expiryRequired.has(form.product) && (!isIsoDate(form.expiry))) errors.expiry = '请输入有效的到期日。'; if (isCrypto) for (const key of ['base_currency', 'quote_currency', 'settlement_currency'] as const) if (!form[key].trim()) errors[key] = '加密产品必须提供此币种。'; if (Object.keys(errors).length) { setFormErrors(errors); focusFirstError(errors, ids); return } setFormErrors({}); setSaving(true); try { const result = await saveInstrumentDraft({ name: form.name, instrument: formToInstrument(form) }, editing?.id); setEditing(result); setHistory(await fetchInstrumentDraftHistory(result.id, new AbortController().signal)); setRefresh(value => value + 1) } catch (reason) { const mapped = backendFieldErrors(reason, Object.keys(ids)); if (Object.keys(mapped).length) { setFormErrors(mapped); focusFirstError(mapped, ids) } else setError(reason instanceof Error ? reason.message : '保存失败') } finally { setSaving(false) } }
  if (error && !drafts) return <section className="empty-state wide"><AlertCircle size={24}/><strong>标的定义暂不可用</strong><p>{error}</p><button className="detail-action" onClick={() => setRefresh(value => value + 1)}>重新读取</button></section>
  if (!drafts) return <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取配置草稿</strong><p>只读取 SQLite 草稿；不会读取行情或连接账户。</p></section>
  const isCrypto = crypto.has(form.product); const isDerivative = derivatives.has(form.product)
  const inputField = (key: string, label: string, type = 'text', hint = hints[key]) => <label>{label} <RequiredMark/><input {...labelledField(`instrument-${key}`, formErrors[key])} type={type} list={['tick_size', 'lot_size', 'base_currency', 'quote_currency', 'settlement_currency'].includes(key) ? `instrument-options-${key}` : undefined} inputMode={['tick_size', 'lot_size', 'multiplier'].includes(key) ? 'decimal' : undefined} value={field(form, key)} onChange={e => change(key, e.target.value)}/><FieldError id={`instrument-${key}-error`} message={formErrors[key]}/>{hint && <small>{hint}</small>}</label>
  const selectField = (key: string, label: string, choices: string[][]) => <label>{label} <RequiredMark/><select {...labelledField(`instrument-${key}`, formErrors[key])} value={field(form, key)} onChange={e => change(key, e.target.value)}><option value="">请选择，未知请先确认</option>{form[key] && !choices.some(([value]) => value === form[key]) && <option value={form[key]}>{form[key]}（已有草稿值，未核验）</option>}{choices.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select><FieldError id={`instrument-${key}-error`} message={formErrors[key]}/><small>{hints[key]}</small></label>
  return <div className="universe-workspace"><section className="page-heading"><div><p className="eyebrow">市场与数据 · 版本化定义</p><h1>资产与合约</h1><p>这里给证券建档，供研究资产池引用；如果只是看股票行情，不必填写此页，直接使用“行情浏览”。</p></div><span className="static-boundary">定义草稿 · 不可执行</span></section>
    {error && <div className="strategy-error" role="alert"><AlertCircle size={17}/>{error}</div>}
    <section className="strategy-boundary"><ShieldAlert size={18}/><div><strong>仅保存参考配置</strong><p>不会读取行情、声明数据覆盖、创建回测或交易任务，也不会连接外部账户。</p></div></section>
    <div className="universe-grid"><aside className="universe-drafts"><header><strong>已保存标的</strong><span>{drafts.length} 项</span></header>{drafts.length ? drafts.map(item => <button key={item.id} className={editing?.id === item.id ? 'is-active' : ''} onClick={() => edit(item)}><span><strong>{item.name}</strong><small>{String(item.instrument.product)} · v{item.version}</small></span><PencilLine size={14}/></button>) : <p>尚无标的定义。保存一个单标的或合约后，才可在研究资产池中引用它。</p>}</aside>
      <section className="universe-editor"><header><div><h2>{editing ? `编辑 ${editing.name}` : '新建标的定义'}</h2><p>每个草稿只描述一个标的或合约；更新会保留可引用的历史版本。</p></div><span>v{editing?.version || 1}</span></header><div className="universe-form">
        {inputField('name', '定义名称')}<label>市场产品 <RequiredMark/><select {...labelledField('instrument-product', formErrors.product)} value={form.product} onChange={e => { setFormErrors({}); change('product', e.target.value) }}>{products.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select><FieldError id="instrument-product-error" message={formErrors.product}/><small>{hints.product}</small></label>
        {form.product === 'US_EQUITY' ? selectField('venue', '上市市场', [['NASDAQ', 'NASDAQ（纳斯达克）'], ['NYSE', 'NYSE（纽约证券交易所）'], ['NYSEARCA', 'NYSE Arca']]) : inputField('venue', '市场 / venue')}
        {inputField('venue_symbol', '股票 / 交易代码')}
        {isCrypto && <>{inputField('base_currency', '基础币种')}{inputField('quote_currency', '报价币种')}{inputField('settlement_currency', '结算币种')}</>}
{isDerivative && <><label>合约模型 <RequiredMark/><select {...labelledField('instrument-contract_model', formErrors.contract_model)} value={field(form, 'contract_model') || 'linear'} onChange={e => change('contract_model', e.target.value)}><option value="linear">线性</option><option value="inverse">反向</option></select><FieldError id="instrument-contract_model-error" message={formErrors.contract_model}/><small>{hints.contract_model}</small></label>{inputField('multiplier', '合约乘数')}</>}
        {expiryRequired.has(form.product) && inputField('expiry', '到期日', 'date')}
        {selectField('quantity_unit', '数量单位', form.product === 'US_EQUITY' ? [['share', '股（share）']] : [['share', '股'], ['contract', '张 / 份合约'], ['coin', '币']])}
      </div><details className="instrument-advanced" open={Object.keys(formErrors).some(key => ['identity_key', 'timezone', 'calendar_id', 'valid_from', 'rule_version', 'tick_size', 'lot_size'].includes(key) && formErrors[key]) || undefined}><summary>身份、有效日期与交易规则（高级设置，保存前需确认）</summary><p>这些是参考草稿，不是已验证交易规格。必填项未确认时，保存会在对应字段提示；不会自动放开真实回测或交易。</p><div className="universe-form">
        <label>稳定档案编号 <RequiredMark/><div className="instrument-id-row"><input {...labelledField('instrument-identity_key', formErrors.identity_key)} value={field(form, 'identity_key')} onChange={e => change('identity_key', e.target.value)}/><button type="button" className="detail-action" onClick={() => { try { change('identity_key', idempotencyKey('draft-security')) } catch(reason) { setError(reason instanceof Error ? reason.message : '无法生成编号') } }}>生成新编号</button></div><FieldError id="instrument-identity_key-error" message={formErrors.identity_key}/><small>{hints.identity_key}</small></label>
        {form.product === 'US_EQUITY' ? selectField('timezone', '市场时区', [['America/New_York', '纽约时间（America/New_York）']]) : inputField('timezone', '市场时区')}
        {form.product === 'US_EQUITY' ? selectField('calendar_id', '交易日历', [['US-EQUITY-DRAFT', '美股日历草稿（未核验）']]) : inputField('calendar_id', '交易日历标识')}
        {inputField('valid_from', '代码与规则生效日', 'date')}{inputField('rule_version', '交易规则版本')}{inputField('tick_size', '最小报价步长')}{inputField('lot_size', '数量步长')}
      </div></details><datalist id="instrument-options-tick_size"><option value="0.01"/><option value="0.005"/><option value="0.001"/></datalist><datalist id="instrument-options-lot_size"><option value="1"/><option value="0.1"/><option value="0.01"/></datalist>{['base_currency', 'quote_currency', 'settlement_currency'].map(key => <datalist key={key} id={`instrument-options-${key}`}><option value="USD"/><option value="USDT"/><option value="BTC"/><option value="ETH"/></datalist>)}
      <button className="strategy-save" disabled={saving} onClick={save}><Save size={15}/>{saving ? '保存中…' : editing ? '保存新版本' : '创建标的定义'}</button></section></div>
    {history && <section className="strategy-history"><header><div><History size={17}/><strong>{editing?.name} 的定义历史</strong></div><span>{history.length} 个版本</span></header>{history.map(item => <div key={item.id}><strong>v{item.version}</strong><span>{String(item.instrument.product)} · {String(item.instrument.venue_symbol)}</span><time>{new Date(item.created_at).toLocaleString()}</time></div>)}</section>}
  </div>
}
