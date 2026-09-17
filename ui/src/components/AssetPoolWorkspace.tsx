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
    Promise.all([fetchAssetPoolDrafts(c.signal), fetchInstrumentDrafts(c.signal)]).then(([nextPools, nextDefinitions]) => { setPools(nextPools); setDefinitions(nextDefinitions) }).catch(reason => { if (!c.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取研究股票池') })
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
    if (!form.name.trim() || form.name.trim().length > 120) errors.name = '股票池名称为 1–120 个字符。'
    if (!form.purpose.trim() || form.purpose.trim().length > 1000) errors.purpose = '研究用途为 1–1000 个字符。'
    const count = Object.keys(form.selected).length
    if (count < 1 || count > 200) errors.instruments = '请选择 1–200 个已保存证券档案修订。'
    if (Object.keys(errors).length) { setFormErrors(errors); focusFirstError(errors, fieldIds); return }
    setFormErrors({}); setSaving(true)
    try {
      const instruments = Object.entries(form.selected).map(([instrument_draft_id, instrument_version]) => ({ instrument_draft_id, instrument_version }))
      const result = await saveAssetPoolDraft({ name: form.name, purpose: form.purpose, instruments }, editing?.id)
      setEditing(result); setHistory(await fetchAssetPoolDraftHistory(result.id, new AbortController().signal)); setRefresh(value => value + 1)
    } catch (reason) { const mapped = backendFieldErrors(reason, Object.keys(fieldIds)); if (Object.keys(mapped).length) { setFormErrors(mapped); focusFirstError(mapped, fieldIds) } else setError(reason instanceof Error ? reason.message : '保存失败') } finally { setSaving(false) }
  }
  if (error && (!pools || !definitions)) return <section className="empty-state wide"><AlertCircle size={24}/><strong>研究股票池暂不可用</strong><p>{error}</p><button className="detail-action" onClick={() => setRefresh(value => value + 1)}>重新读取</button></section>
  if (!pools || !definitions) return <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取研究股票池</strong><p>仅加载旧版证券名单引用和股票池版本；自动证券目录与研究资格分开。</p></section>
  return <div className="universe-workspace"><section className="page-heading"><div><p className="eyebrow">研究工具 · 版本化引用</p><h1>研究股票池</h1><p>定义策略从哪些股票或 ETF 中选择。这里保存证券名单，不保存行情；当前仍使用旧档案，自动证券目录尚未对接。</p></div><span className="static-boundary">研究配置 · 不可执行</span></section>
    {error && <div className="strategy-error"><AlertCircle size={17}/>{error}</div>}
    <section className="strategy-boundary"><ShieldAlert size={18}/><div><strong>股票池是研究名单，不是数据集或资金账户</strong><p>当前仍管理旧版固定定义引用；自动证券目录的股票池引用迁移待完成。不会声明研究资格、创建回测或连接账户。</p></div></section>
    <div className="universe-grid"><aside className="universe-drafts"><header><strong>我的研究股票池</strong><span>{pools.length} 项</span></header>{pools.length ? pools.map(pool => <button key={pool.id} className={editing?.id === pool.id ? 'is-active' : ''} onClick={() => edit(pool)}><span><strong>{pool.name}</strong><small>{pool.instrument_count} 只证券 · v{pool.version}</small></span><PencilLine size={14}/></button>) : <p>尚无研究股票池。证券资料由系统自动建档；当前此页仍引用旧版定义，自动目录引用迁移待完成，不需要你手工补建证券。</p>}</aside>
      <section className="universe-editor"><header><div><h2>{editing ? `编辑 ${editing.name}` : '新建研究股票池'}</h2><p>选择证券后保存研究名单；修订号用于以后复现名单，不会改写证券资料或下载行情。</p></div><span>v{editing?.version || 1}</span></header>
        <div className="universe-form"><label>股票池名称 <RequiredMark /><input {...labelledField(fieldIds.name, formErrors.name)} maxLength={120} value={form.name} onChange={e => { setFormErrors(v => ({ ...v, name: '' })); setForm(current => ({ ...current, name: e.target.value })) }}/><FieldError id={`${fieldIds.name}-error`} message={formErrors.name}/></label><label>研究用途 <RequiredMark /><textarea {...labelledField(fieldIds.purpose, formErrors.purpose)} maxLength={1000} value={form.purpose} onChange={e => { setFormErrors(v => ({ ...v, purpose: '' })); setForm(current => ({ ...current, purpose: e.target.value })) }} placeholder="例如：动量因子候选池，不代表已验证数据覆盖。"/><FieldError id={`${fieldIds.purpose}-error`} message={formErrors.purpose}/></label></div>
        <div id={fieldIds.instruments} tabIndex={-1} className="asset-pool-members" aria-invalid={Boolean(formErrors.instruments)} aria-describedby={formErrors.instruments ? `${fieldIds.instruments}-error` : undefined}><strong>选择股票或 ETF（当前旧档案） <RequiredMark /></strong>{definitions.length ? definitions.map(definition => <label key={definition.id}><input type="checkbox" checked={definition.id in form.selected} onChange={e => { setFormErrors(v => ({ ...v, instruments: '' })); toggle(definition, e.target.checked) }}/><span><b>{definition.name}</b><small>{String(definition.instrument.product)} · {String(definition.instrument.venue_symbol)} · 当前 v{definition.version}</small></span></label>) : <p>自动目录与此处旧版定义引用尚未完成迁移，不需要你手工创建证券。<button type="button" className="detail-action" onClick={() => onNavigate?.('instruments')}>查看自动证券档案</button></p>}<FieldError id={`${fieldIds.instruments}-error`} message={formErrors.instruments}/></div>
        <button className="strategy-save" disabled={saving} onClick={save}><Save size={15}/>{saving ? '保存中…' : editing ? '保存股票池新版本' : '创建研究股票池'}</button>
      </section></div>
    {history && <section className="strategy-history"><header><div><History size={17}/><strong>{editing?.name} 的股票池历史</strong></div><span>{history.length} 个版本</span></header>{history.map(item => <div key={item.id}><strong>v{item.version}</strong><span>{item.instrument_count} 只证券（旧档案） · {item.purpose}</span><time>{new Date(item.created_at).toLocaleString()}</time></div>)}</section>}
  </div>
}
