import { useEffect, useState } from 'react'
import { AlertCircle, History, LoaderCircle, PencilLine, Save, ShieldAlert } from 'lucide-react'
import { RiskPolicyDraft, fetchRiskPolicyDraftHistory, fetchRiskPolicyDrafts, saveRiskPolicyDraft } from '../api'

type Form = { name: string; max_instrument_exposure_pct: string; max_market_exposure_pct: string; max_gross_leverage: string; max_daily_loss_pct: string; max_drawdown_pct: string; max_orders_per_minute: string; trading_halted: boolean }
const blank: Form = { name: '全局风险保护线', max_instrument_exposure_pct: '10', max_market_exposure_pct: '35', max_gross_leverage: '1.5', max_daily_loss_pct: '2', max_drawdown_pct: '12', max_orders_per_minute: '20', trading_halted: false }

function toForm(draft: RiskPolicyDraft): Form {
  const p = draft.policy
  return { name: draft.name, max_instrument_exposure_pct: p.max_instrument_exposure_pct, max_market_exposure_pct: p.max_market_exposure_pct, max_gross_leverage: p.max_gross_leverage, max_daily_loss_pct: p.max_daily_loss_pct, max_drawdown_pct: p.max_drawdown_pct, max_orders_per_minute: String(p.max_orders_per_minute), trading_halted: p.trading_halted }
}

export function RiskPolicyWorkspace() {
  const [drafts, setDrafts] = useState<RiskPolicyDraft[] | null>(null); const [editing, setEditing] = useState<RiskPolicyDraft | null>(null); const [history, setHistory] = useState<RiskPolicyDraft[] | null>(null); const [form, setForm] = useState<Form>(blank); const [error, setError] = useState(''); const [saving, setSaving] = useState(false); const [refresh, setRefresh] = useState(0)
  useEffect(() => { const c = new AbortController(); setError(''); fetchRiskPolicyDrafts(c.signal).then(setDrafts).catch(reason => { if (!c.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取全局风控规则草稿') }); return () => c.abort() }, [refresh])
  const change = (key: Exclude<keyof Form, 'trading_halted'>, value: string) => setForm(current => ({ ...current, [key]: value }))
  const edit = async (draft: RiskPolicyDraft) => { setEditing(draft); setForm(toForm(draft)); setError(''); try { setHistory(await fetchRiskPolicyDraftHistory(draft.id, new AbortController().signal)) } catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取历史') } }
  const fresh = () => { setEditing(null); setHistory(null); setForm(blank); setError('') }
  const save = async () => { if (saving) return; setSaving(true); setError(''); try { const result = await saveRiskPolicyDraft({ ...form, scope: 'global', max_orders_per_minute: Number(form.max_orders_per_minute) }, editing?.id); setEditing(result); setHistory(await fetchRiskPolicyDraftHistory(result.id, new AbortController().signal)); setRefresh(value => value + 1) } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败') } finally { setSaving(false) } }
  if (error && !drafts) return <section className="empty-state wide"><AlertCircle size={24}/><strong>风控规则中心暂不可用</strong><p>{error}</p><button className="detail-action" onClick={() => setRefresh(value => value + 1)}>重新读取</button></section>
  if (!drafts) return <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取全局风控草稿</strong><p>只读取 SQLite 配置版本；不会读取行情、账户或订单。</p></section>
  return <div className="risk-policy-workspace"><section className="page-heading"><div><p className="eyebrow">风控中心 · 版本化定义</p><h1>全局风控规则</h1><p>保存账户与策略之外的全局保护线草稿；尚未连接账户，因此不会判断、拦截或执行任何交易。</p></div><span className="static-boundary">全局草稿 · 不可执行</span></section>
    {error && <div className="strategy-error"><AlertCircle size={17}/>{error}</div>}
    <section className="strategy-boundary"><ShieldAlert size={18}/><div><strong>策略内约束不在这里</strong><p>选股、持仓权重、调仓、止损等策略自身规则应随策略草稿版本保存。本页仅定义未来所有策略之外的全局安全边界。</p></div></section>
    <div className="risk-policy-grid"><aside className="risk-policy-drafts"><header><strong>我的全局规则</strong><span>{drafts.length} 项</span></header>{drafts.length ? drafts.map(item => <button key={item.id} className={editing?.id === item.id ? 'is-active' : ''} onClick={() => edit(item)}><span><strong>{item.name}</strong><small>全局 · v{item.version}{item.policy.trading_halted ? ' · 停单' : ''}</small></span><PencilLine size={14}/></button>) : <p>尚无草稿。填写右侧保护线即可创建首版。</p>}</aside>
      <section className="risk-policy-editor"><header><div><h2>{editing ? `编辑 ${editing.name}` : '新建全局规则草稿'}</h2><p>生效范围固定为全局；保存仅产生版本化配置，不会启用停单。</p></div>{editing ? <button className="detail-action" onClick={fresh}>新建副本</button> : <span>全局</span>}</header><div className="risk-policy-form">
        <label>草稿名称<input maxLength={120} value={form.name} onChange={e => change('name', e.target.value)} /></label><label>生效范围<input value="全局（固定）" disabled /></label>
        <label>单标的最大敞口 %<input inputMode="decimal" value={form.max_instrument_exposure_pct} onChange={e => change('max_instrument_exposure_pct', e.target.value)} /><small>必须不高于单市场敞口。</small></label><label>单市场最大敞口 %<input inputMode="decimal" value={form.max_market_exposure_pct} onChange={e => change('max_market_exposure_pct', e.target.value)} /></label>
        <label>总毛杠杆上限<input inputMode="decimal" value={form.max_gross_leverage} onChange={e => change('max_gross_leverage', e.target.value)} /><small>配置范围大于 0 且不高于 100。</small></label><label>单日最大亏损 %<input inputMode="decimal" value={form.max_daily_loss_pct} onChange={e => change('max_daily_loss_pct', e.target.value)} /><small>必须不高于最大回撤。</small></label>
        <label>最大回撤 %<input inputMode="decimal" value={form.max_drawdown_pct} onChange={e => change('max_drawdown_pct', e.target.value)} /></label><label>每分钟订单上限<input inputMode="numeric" value={form.max_orders_per_minute} onChange={e => change('max_orders_per_minute', e.target.value)} /></label>
        <label className="risk-halt"><input type="checkbox" checked={form.trading_halted} onChange={e => setForm(current => ({ ...current, trading_halted: e.target.checked }))}/><span><strong>停单开关（配置）</strong><small>仅保存未来执行器应遵守的状态；当前不会撤单、平仓或发出任何指令。</small></span></label>
      </div><button className="strategy-save" disabled={saving} onClick={save}><Save size={15}/>{saving ? '保存中…' : editing ? '保存新版本' : '创建全局规则草稿'}</button></section></div>
    {history && <section className="strategy-history"><header><div><History size={17}/><strong>{editing?.name} 的规则历史</strong></div><span>{history.length} 个版本</span></header>{history.map(item => <div key={item.id}><strong>v{item.version}</strong><span>标的 {item.policy.max_instrument_exposure_pct}% · 市场 {item.policy.max_market_exposure_pct}% · 杠杆 {item.policy.max_gross_leverage} · {item.policy.trading_halted ? '停单配置' : '允许配置'}</span><time>{new Date(item.created_at).toLocaleString()}</time></div>)}</section>}
  </div>
}
