import { CircleAlert, DatabaseZap, Layers3 } from 'lucide-react'
import type { NavGroup, NavChild } from '../navigation'

export function PlaceholderPage({ group, child }: { group: NavGroup; child: NavChild }) {
  return <div className="placeholder-page">
    <section className="page-heading"><div><p className="eyebrow">{group.label}</p><h1>{child.label}</h1><p>{child.description}</p></div><span className="static-boundary">待开发</span></section>
    <section className="placeholder-surface">
      <div className="placeholder-icon"><Layers3 size={26} /></div>
      <div className="placeholder-copy"><h2>此功能尚未开发</h2><p>{child.reason || '所需的数据、服务端能力或权限策略尚未接入。'} 当前页面不提供模拟数据、替代结果或可操作控件。</p></div>
      <div className="capability-row">{child.capabilities.map((item) => <span key={item}>{item}</span>)}</div>
    </section>
    <section className="state-grid">
      <article><DatabaseZap size={19} /><div><strong>当前状态</strong><p>所需的数据或服务端能力尚未接入，本页面不会发起请求。</p></div><span>不可用</span></article>
      <article><CircleAlert size={19} /><div><strong>功能边界</strong><p>不会启动任务、写入配置、生成结果或进行交易。</p></div><span>无可操作项</span></article>
    </section>
    <section className="empty-state wide"><Layers3 size={25} /><strong>等待后续接入</strong><p>功能可用后，将按实际数据覆盖、权限和能力门禁展示状态；在此之前不作能力承诺。</p></section>
  </div>
}
