import { CircleAlert, DatabaseZap, Layers3 } from 'lucide-react'
import type { NavGroup, NavChild } from '../navigation'

export function PlaceholderPage({ group, child }: { group: NavGroup; child: NavChild }) {
  return <div className="placeholder-page">
    <section className="page-heading"><div><p className="eyebrow">{group.label}</p><h1>{child.label}</h1><p>{child.description}</p></div><span className="static-boundary">尚未接入真实数据</span></section>
    <section className="placeholder-surface">
      <div className="placeholder-icon"><Layers3 size={26} /></div>
      <div className="placeholder-copy"><h2>功能准备中</h2><p>该界面已保留在工作台信息架构中，待服务端能力、数据快照和权限策略接入后开放。</p></div>
      <div className="capability-row">{child.capabilities.map((item) => <span key={item}>{item}</span>)}</div>
    </section>
    <section className="state-grid">
      <article><DatabaseZap size={19} /><div><strong>数据状态</strong><p>暂无可用数据源，本页面不发起网络请求。</p></div><span>不可用</span></article>
      <article><CircleAlert size={19} /><div><strong>安全边界</strong><p>当前原型不会启动任务、写入配置或进行交易。</p></div><span>只读原型</span></article>
    </section>
    <section className="empty-state wide"><Layers3 size={25} /><strong>等待服务端接口</strong><p>接入后，这里会根据权限、数据覆盖和能力检查显示加载、空数据、失败或可操作状态。</p></section>
  </div>
}
