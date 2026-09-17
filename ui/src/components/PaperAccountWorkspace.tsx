import { useEffect, useRef, useState } from 'react'
import { FieldError, RequiredMark } from './FormValidation'
import { focusFirstError, labelledField } from '../formValidation'
import { validatePaperAccount, paperServerError, paperFieldIds, type PaperAccountForm } from '../paperAccountValidation'
import { AlertCircle, BookOpen, Landmark, LoaderCircle, Plus, Save } from 'lucide-react'
import { createPaperAccount, fetchPaperAccounts, fetchPaperLedger, LedgerView, PaperAccount } from '../api'

type Form = PaperAccountForm
const blank: Form = { name: '', base_currency: 'USD', initial_cash: '100000', margin_mode: 'cash' }

export function PaperAccountWorkspace({ pageTitle = '模拟账户' }: { pageTitle?: string }) {
  const [accounts, setAccounts] = useState<PaperAccount[] | null>(null); const [active, setActive] = useState<PaperAccount | null>(null); const [ledger, setLedger] = useState<LedgerView | null>(null); const [form, setForm] = useState<Form>(blank); const [error, setError] = useState(''); const [saving, setSaving] = useState(false); const [refresh, setRefresh] = useState(0)
  const [formErrors, setFormErrors] = useState<Record<string, string>>({})
  const inFlight = useRef(false)
  const change = <K extends keyof Form>(key: K, value: Form[K]) => { setForm(v => ({ ...v, [key]: value })); setFormErrors(v => ({ ...v, [key]: '' })) }
  useEffect(() => { const c = new AbortController(); setError(''); fetchPaperAccounts(c.signal).then(setAccounts).catch(reason => { if (!c.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取模拟账户') }); return () => c.abort() }, [refresh])
  const select = async (account: PaperAccount) => { setActive(account); setLedger(null); setError(''); try { setLedger(await fetchPaperLedger(account.id, new AbortController().signal)) } catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取台账') } }
  const create = async () => {
    if (inFlight.current) return
    const errors = validatePaperAccount(form); setFormErrors(errors); setError('')
    if (Object.keys(errors).length) { focusFirstError(errors, paperFieldIds); return }
    inFlight.current = true; setSaving(true)
    try {
      const account = await createPaperAccount({ ...form, base_currency: form.base_currency.trim(), initial_cash: form.initial_cash.trim() })
      setActive(account); setLedger(null); setForm(blank); setRefresh(value => value + 1)
      setLedger(await fetchPaperLedger(account.id, new AbortController().signal))
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '创建失败'
      const fields = paperServerError(message); setFormErrors(fields)
      if (Object.keys(fields).length) focusFirstError(fields, paperFieldIds)
      else setError(message)
    } finally { inFlight.current = false; setSaving(false) }
  }
  if (error && !accounts) return <section className="empty-state wide"><AlertCircle size={24}/><strong>模拟账户暂不可用</strong><p>{error}</p><button className="detail-action" onClick={() => setRefresh(value => value + 1)}>重新读取</button></section>
  if (!accounts) return <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取模拟账户</strong><p>仅读取本地配置与台账，不会连接行情或券商。</p></section>
  return <div className="paper-workspace"><section className="page-heading"><div><p className="eyebrow">账户与资金 · 本地模拟记录</p><h1>{pageTitle}</h1><p>创建本地模拟账户并只读查看不可变订单、成交、费用和审计事件。当前未连接行情或券商，无法自行产生订单或成交。</p></div><span className="static-boundary">本地台账 · 不可执行</span></section>
    {error && <div className="strategy-error"><AlertCircle size={17}/>{error}</div>}
    <section className="strategy-boundary"><Landmark size={18}/><div><strong>只管理账户与已有记录，不自动交易</strong><p>账户初始资金仅是明确配置。台账只能由未来经过校验的流程追加；本页没有“下单”“生成成交”或外部账户同步入口。</p></div></section>
    <div className="paper-grid"><aside className="paper-accounts"><header><strong>模拟账户</strong><span>{accounts.length} 个</span></header>{accounts.length ? accounts.map(account => <button key={account.id} className={active?.id === account.id ? 'is-active' : ''} onClick={() => select(account)}><span><strong>{account.name}</strong><small>{account.base_currency} · 初始 {account.initial_cash} · {account.margin_mode}</small></span><BookOpen size={14}/></button>) : <p>尚无模拟账户。填写右侧基础配置即可创建第一个账户。</p>}</aside>
      <section className="paper-editor"><header><div><h2>创建模拟账户</h2><p>账户类型固定为模拟；基础币种和初始现金创建后作为台账起点保留。</p></div><Plus size={17}/></header><div className="paper-form">
        <label>账户名称 <RequiredMark/><input {...labelledField(paperFieldIds.name, formErrors.name)} maxLength={120} value={form.name} onChange={e => change('name', e.target.value)}/><FieldError id="paper-name-error" message={formErrors.name}/></label>
        <label>基础币种 <RequiredMark/><input {...labelledField(paperFieldIds.base_currency, formErrors.base_currency)} maxLength={12} value={form.base_currency} onChange={e => change('base_currency', e.target.value.toUpperCase())}/><FieldError id="paper-currency-error" message={formErrors.base_currency}/></label>
        <label>初始现金 <RequiredMark/><input {...labelledField(paperFieldIds.initial_cash, formErrors.initial_cash)} inputMode="decimal" value={form.initial_cash} onChange={e => change('initial_cash', e.target.value)}/><FieldError id="paper-cash-error" message={formErrors.initial_cash}/></label>
        <label>保证金模式 <RequiredMark/><select {...labelledField(paperFieldIds.margin_mode, formErrors.margin_mode)} value={form.margin_mode} onChange={e => change('margin_mode', e.target.value as Form['margin_mode'])}><option value="cash">现金</option><option value="cross">全仓</option><option value="isolated">逐仓</option></select><FieldError id="paper-margin-error" message={formErrors.margin_mode}/></label>
      </div><button className="strategy-save" disabled={saving} onClick={create}><Save size={15}/>{saving ? '创建中…' : '创建模拟账户'}</button></section></div>
    {active && <section className="paper-ledger"><header><div><BookOpen size={17}/><strong>{active.name} 的交易记录（只读）</strong></div><span>{ledger?.reconciliation.event_count ?? '…'} 条事件</span></header>{ledger ? <><div className="paper-balance"><span>初始现金 <strong>{ledger.reconciliation.initial_cash} {active.base_currency}</strong></span><span>已记录现金变动 <strong>{ledger.reconciliation.recorded_cash_delta}</strong></span><span>账本现金 <strong>{ledger.reconciliation.reconciled_cash} {active.base_currency}</strong></span></div>{ledger.items.length ? ledger.items.map(item => <div className="paper-event" key={item.id}><strong>{item.event_type}</strong><span>{item.dedupe_key}</span><span>现金 {item.cash_delta} · 持仓 {item.position_delta}</span><time>{new Date(item.created_at).toLocaleString()}</time></div>) : <div className="paper-empty"><BookOpen size={22}/><strong>台账尚无订单、成交、费用或审计事件</strong><p>本页不生成示例成交。没有行情、券商或撮合器连接，因此不会自行产生订单。</p></div>}<p className="paper-scope">{ledger.reconciliation.scope}</p></> : <div className="paper-empty"><LoaderCircle size={20} className="animate-spin"/><p>正在读取已记录的账户事件…</p></div>}</section>}
  </div>
}
