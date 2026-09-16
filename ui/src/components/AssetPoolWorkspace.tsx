import { useEffect, useState } from 'react'
import { AlertCircle, History, LoaderCircle, PencilLine, Save, ShieldAlert } from 'lucide-react'
import { AssetPoolDraft, InstrumentDraft, fetchAssetPoolDraftHistory, fetchAssetPoolDrafts, fetchInstrumentDrafts, saveAssetPoolDraft } from '../api'

type Form = { name: string; purpose: string; selected: Record<string, number> }
const blank: Form = { name: '', purpose: '', selected: {} }

export function AssetPoolWorkspace() {
  const [pools, setPools] = useState<AssetPoolDraft[] | null>(null)
  const [definitions, setDefinitions] = useState<InstrumentDraft[] | null>(null)
  const [editing, setEditing] = useState<AssetPoolDraft | null>(null)
  const [history, setHistory] = useState<AssetPoolDraft[] | null>(null)
  const [form, setForm] = useState<Form>(blank)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const c = new AbortController(); setError('')
    Promise.all([fetchAssetPoolDrafts(c.signal), fetchInstrumentDrafts(c.signal)]).then(([nextPools, nextDefinitions]) => { setPools(nextPools); setDefinitions(nextDefinitions) }).catch(reason => { if (!c.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取研究资产池') })
    return () => c.abort()
  }, [refresh])
  const edit = async (pool: AssetPoolDraft) => {
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
    if (saving) return; setSaving(true); setError('')
    try {
      const instruments = Object.entries(form.selected).map(([instrument_draft_id, instrument_version]) => ({ instrument_draft_id, instrument_version }))
      const result = await saveAssetPoolDraft({ name: form.name, purpose: form.purpose, instruments }, editing?.id)
      setEditing(result); setHistory(await fetchAssetPoolDraftHistory(result.id, new AbortController().signal)); setRefresh(value => value + 1)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败') } finally { setSaving(false) }
  }
  if (error && (!pools || !definitions)) return <section className="empty-state wide"><AlertCircle size={24}/><strong>研究资产池暂不可用</strong><p>{error}</p><button className="detail-action" onClick={() => setRefresh(value => value + 1)}>重新读取</button></section>
  if (!pools || !definitions) return <section className="empty-state wide"><LoaderCircle size={24} className="animate-spin"/><strong>正在读取研究资产池</strong><p>仅加载已保存标的定义和资产池版本；不会读取行情。</p></section>
  return <div className="universe-workspace"><section className="page-heading"><div><p className="eyebrow">研究实验室 · 版本化引用</p><h1>数据集与资产池</h1><p>为研究保存标的定义版本的固定引用和用途说明；不复制合约字段，也不代表数据覆盖。</p></div><span className="static-boundary">研究配置 · 不可执行</span></section>
    {error && <div className="strategy-error"><AlertCircle size={17}/>{error}</div>}
    <section className="strategy-boundary"><ShieldAlert size={18}/><div><strong>仅管理研究成员引用</strong><p>不会读取行情、声明数据覆盖、创建回测或交易任务，也不会连接外部账户。</p></div></section>
    <div className="universe-grid"><aside className="universe-drafts"><header><strong>我的研究资产池</strong><span>{pools.length} 项</span></header>{pools.length ? pools.map(pool => <button key={pool.id} className={editing?.id === pool.id ? 'is-active' : ''} onClick={() => edit(pool)}><span><strong>{pool.name}</strong><small>{pool.instrument_count} 个固定引用 · v{pool.version}</small></span><PencilLine size={14}/></button>) : <p>尚无研究资产池。先在“资产与合约”保存标的定义，再在右侧选择其版本。</p>}</aside>
      <section className="universe-editor"><header><div><h2>{editing ? `编辑 ${editing.name}` : '新建研究资产池'}</h2><p>成员只保存已存在的标的定义版本。更新资产池不会改写任何标的。</p></div><span>v{editing?.version || 1}</span></header>
        <div className="universe-form"><label>资产池名称<input value={form.name} onChange={e => setForm(current => ({ ...current, name: e.target.value }))}/></label><label>研究用途<textarea value={form.purpose} onChange={e => setForm(current => ({ ...current, purpose: e.target.value }))} placeholder="例如：动量因子候选池，不代表已验证数据覆盖。"/></label></div>
        <div className="asset-pool-members"><strong>引用已保存标的版本</strong>{definitions.length ? definitions.map(definition => <label key={definition.id}><input type="checkbox" checked={definition.id in form.selected} onChange={e => toggle(definition, e.target.checked)}/><span><b>{definition.name}</b><small>{String(definition.instrument.product)} · {String(definition.instrument.venue_symbol)} · 当前 v{definition.version}</small></span></label>) : <p>暂无可引用的标的定义。请先前往“市场与数据 → 资产与合约”创建。</p>}</div>
        <button className="strategy-save" disabled={saving || !definitions.length} onClick={save}><Save size={15}/>{saving ? '保存中…' : editing ? '保存资产池新版本' : '创建研究资产池'}</button>
      </section></div>
    {history && <section className="strategy-history"><header><div><History size={17}/><strong>{editing?.name} 的资产池历史</strong></div><span>{history.length} 个版本</span></header>{history.map(item => <div key={item.id}><strong>v{item.version}</strong><span>{item.instrument_count} 个标的定义版本引用 · {item.purpose}</span><time>{new Date(item.created_at).toLocaleString()}</time></div>)}</section>}
  </div>
}
