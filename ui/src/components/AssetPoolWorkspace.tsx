import { useEffect, useState } from 'react'
import { AlertCircle, History, LoaderCircle, PencilLine, Save, ShieldAlert } from 'lucide-react'
import { AssetPoolDraft, InstrumentDraft, fetchAssetPoolDraftHistory, fetchAssetPoolDrafts, fetchInstrumentDrafts, saveAssetPoolDraft } from '../api'
import { backendFieldErrors, FieldError, focusFirstError, labelledField, RequiredMark } from './FormValidation'

type Form = { name: string; purpose: string; selected: Record<string, number> }
const blank: Form = { name: '', purpose: '', selected: {} }

export function AssetPoolWorkspace({ onNavigate }: { onNavigate?: (page: string) => void }) {
  const [pools, setPools] = useState<AssetPoolDraft[] | null>(null)
  const [definitions, setDefinitions] = useState<InstrumentDraft[] | null>(null)
  const [editing, setEditing] = useState<AssetPoolDraft | null>(null)
  const [history, setHistory] = useState<AssetPoolDraft[] | null>(null)
  const [form, setForm] = useState<Form>(blank)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [formErrors, setFormErrors] = useState<Record<string, string>>({})
  const fieldIds = { name: 'asset-pool-name', purpose: 'asset-pool-purpose', instruments: 'asset-pool-instruments' }
  useEffect(() => {
    const c = new AbortController(); setError('')
    Promise.all([fetchAssetPoolDrafts(c.signal), fetchInstrumentDrafts(c.signal)]).then(([nextPools, nextDefinitions]) => { setPools(nextPools); setDefinitions(nextDefinitions) }).catch(reason => { if (!c.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取研究资产池') })
    return () => c.abort()
  }, [refresh])
  const edit = async (pool: AssetPoolDraft) => {
    setFormErrors({})
    setEditing(pool); setForm({ name: pool.name, purpose: pool.purpose, selected: Object.fromEntries(pool.instruments.map(ref => [ref.instrument_draft_id, ref.instrument_version])) }); setError('')
    try { setHistory(await fetchAssetPoolDraftHistory(pool.id, new AbortController().signal)) } catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取历史') }
  }
  const toggle = (definition: InstrumentDraft, enabled: boolean) => setForm(current => {
    const selected = { ...current.selected }
    if (enabled) selected[definition.id] = definition.version
    else delete selected[definition.id]
    return { ...current, selected }
  })
  const save = async () => {
    if (saving) return; setError('')
    const errors: Record<string, string> = {}
    if (!form.name.trim() || form.name.trim().length > 120) errors.name = '资产池名称为 1–120 个字符。'
    if (!form.purpose.trim() || form.purpose.trim().length > 1000) errors.purpose = '研究用途为 1–1000 个字符。'
    const count = Object.keys(form.selected).length
    if (count < 1 || count > 200) errors.instruments = '请选择 1–200 个已保存标的版本。'
    if (Object.keys(errors).length) { setFormErrors(errors); focusFirstError(errors, fieldIds); return }
    setFormErrors({}); setSaving(true)
    try {
      const instruments = Object.entries(form.selected).map(([instrument_draft_id, instrument_version]) => ({ instrument_draft_id, instrument_version }))
      const result = await saveAssetPoolDraft({ name: form.name, purpose: form.purpose, instruments }, editing?.id)
      setEditing(result); setHistory(await fetchAssetPoolDraftHistory(result.id, new AbortController().signal)); setRefresh(value => value + 1)
    } catch (reason) { const mapped = backendFieldErrors(reason, Object.keys(fieldIds)); if (Object.keys(mapped).length) { setFormErrors(mapped); focusFirstError(mapped, fieldIds) } else setError(reason instanceof Error ? reason.message : '保存失败') } finally { setSaving(false) }
  }
  if (error && (!pools || !definitions)) return <section className="empty-state wide"><AlertCircle size={24}/><strong>研究资产池暂不可用</strong><p>{error}</p><button className="detail-action" onClick={() => setRefresh(value => value + 1)}>重新读取</button></section>
  if (!pools || !definitions) return <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取研究资产池</strong><p>仅加载已保存标的定义和资产池版本；不会读取行情。</p></section>
  return <div className="universe-workspace"><section className="page-heading"><div><p className="eyebrow">研究实验室 · 版本化引用</p><h1>数据集与资产池</h1><p>为研究保存标的定义版本的固定引用和用途说明；不复制合约字段，也不代表数据覆盖。</p></div><span className="static-boundary">研究配置 · 不可执行</span></section>
    {error && <div className="strategy-error"><AlertCircle size={17}/>{error}</div>}
    <section className="strategy-boundary"><ShieldAlert size={18}/><div><strong>仅管理研究成员引用</strong><p>不会读取行情、声明数据覆盖、创建回测或交易任务，也不会连接外部账户。</p></div></section>
    <div className="universe-grid"><aside className="universe-drafts"><header><strong>我的研究资产池</strong><span>{pools.length} 项</span></header>{pools.length ? pools.map(pool => <button key={pool.id} className={editing?.id === pool.id ? 'is-active' : ''} onClick={() => edit(pool)}><span><strong>{pool.name}</strong><small>{pool.instrument_count} 个固定引用 · v{pool.version}</small></span><PencilLine size={14}/></button>) : <p>尚无研究资产池。先在“资产与合约”保存标的定义，再在右侧选择其版本。</p>}</aside>
      <section className="universe-editor"><header><div><h2>{editing ? `编辑 ${editing.name}` : '新建研究资产池'}</h2><p>成员只保存已存在的标的定义版本。更新资产池不会改写任何标的。</p></div><span>v{editing?.version || 1}</span></header>
        <div className="universe-form"><label>资产池名称 <RequiredMark /><input {...labelledField(fieldIds.name, formErrors.name)} maxLength={120} value={form.name} onChange={e => { setFormErrors(v => ({ ...v, name: '' })); setForm(current => ({ ...current, name: e.target.value })) }}/><FieldError id={`${fieldIds.name}-error`} message={formErrors.name}/></label><label>研究用途 <RequiredMark /><textarea {...labelledField(fieldIds.purpose, formErrors.purpose)} maxLength={1000} value={form.purpose} onChange={e => { setFormErrors(v => ({ ...v, purpose: '' })); setForm(current => ({ ...current, purpose: e.target.value })) }} placeholder="例如：动量因子候选池，不代表已验证数据覆盖。"/><FieldError id={`${fieldIds.purpose}-error`} message={formErrors.purpose}/></label></div>
        <div id={fieldIds.instruments} tabIndex={-1} className="asset-pool-members" aria-invalid={Boolean(formErrors.instruments)} aria-describedby={formErrors.instruments ? `${fieldIds.instruments}-error` : undefined}><strong>引用已保存标的版本 <RequiredMark /></strong>{definitions.length ? definitions.map(definition => <label key={definition.id}><input type="checkbox" checked={definition.id in form.selected} onChange={e => { setFormErrors(v => ({ ...v, instruments: '' })); toggle(definition, e.target.checked) }}/><span><b>{definition.name}</b><small>{String(definition.instrument.product)} · {String(definition.instrument.venue_symbol)} · 当前 v{definition.version}</small></span></label>) : <p>暂无可引用的标的定义。<button type="button" className="detail-action" onClick={() => onNavigate?.('instruments')}>前往“资产与合约”创建</button></p>}<FieldError id={`${fieldIds.instruments}-error`} message={formErrors.instruments}/></div>
        <button className="strategy-save" disabled={saving} onClick={save}><Save size={15}/>{saving ? '保存中…' : editing ? '保存资产池新版本' : '创建研究资产池'}</button>
      </section></div>
    {history && <section className="strategy-history"><header><div><History size={17}/><strong>{editing?.name} 的资产池历史</strong></div><span>{history.length} 个版本</span></header>{history.map(item => <div key={item.id}><strong>v{item.version}</strong><span>{item.instrument_count} 个标的定义版本引用 · {item.purpose}</span><time>{new Date(item.created_at).toLocaleString()}</time></div>)}</section>}
  </div>
}
