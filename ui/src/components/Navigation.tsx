import { ChevronDown, ChevronLeft, ChevronRight, X } from 'lucide-react'
import { useState } from 'react'
import { navigation } from '../navigation'

type NavigationProps = {
  activeId: string
  expandedGroupId: string
  collapsed: boolean
  mobileOpen: boolean
  onNavigate: (id: string) => void
  onToggleGroup: (id: string) => void
  onToggleCollapsed: () => void
  onCloseMobile: () => void
}

export function Navigation({ activeId, expandedGroupId, collapsed, mobileOpen, onNavigate, onToggleGroup, onToggleCollapsed, onCloseMobile }: NavigationProps) {
  const [showPlanned, setShowPlanned] = useState(false)
  return (
    <>
      {mobileOpen && <button className="nav-scrim" type="button" aria-label="关闭导航" onClick={onCloseMobile} />}
      <aside className={`sidebar ${collapsed ? 'is-collapsed' : ''} ${mobileOpen ? 'is-mobile-open' : ''}`}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">Q</div>
          {!collapsed && <div><strong>量化工作台</strong><small>研究 · 模拟 · 复盘</small></div>}
          <button type="button" className="close-mobile icon-button" onClick={onCloseMobile} aria-label="关闭导航"><X size={18} /></button>
        </div>
        <nav className="primary-nav" aria-label="主导航">
          {!collapsed && <label className="nav-planned-toggle"><input type="checkbox" checked={showPlanned} onChange={event => setShowPlanned(event.target.checked)}/>显示后续功能（待开发）</label>}
          {navigation.map((group) => {
            const children = group.children.filter(child => showPlanned || child.availability !== 'planned' || child.id === activeId)
            if (!children.length) return null
            const isExpanded = expandedGroupId === group.id
            const groupActive = group.children.some((child) => child.id === activeId)
            const Icon = group.icon
            return (
              <section className="nav-group" key={group.id}>
                <button
                  type="button"
                  className={`nav-group-button ${groupActive ? 'has-active' : ''}`}
                  onClick={() => onToggleGroup(group.id)}
                  aria-expanded={isExpanded}
                  title={collapsed ? group.label : undefined}
                >
                  <Icon size={18} strokeWidth={1.8} />
                  {!collapsed && <><span>{group.label}</span><ChevronDown size={15} className={isExpanded ? 'chevron-open' : ''} /></>}
                </button>
                {!collapsed && isExpanded && <div className="secondary-nav">
                  {children.map((child) => (
                    <button
                      className={`nav-child ${activeId === child.id ? 'is-active' : ''}`}
                      key={child.id}
                      type="button"
                      onClick={() => onNavigate(child.id)}
                      title={child.availability === 'planned' ? `${child.label}：待开发` : undefined}
                    >
                      {child.label}
                      {child.availability === 'planned' && <span className="nav-availability status-tag" aria-hidden="true">待开发</span>}
                    </button>
                  ))}
                </div>}
              </section>
            )
          })}
        </nav>
        <div className="sidebar-footer">
          <span className="offline-indicator"><i /> {!collapsed && '本地原型'}</span>
          <button className="collapse-button" type="button" onClick={onToggleCollapsed} aria-label={collapsed ? '展开导航' : '收起导航'}>
            {collapsed ? <ChevronRight size={17} /> : <><ChevronLeft size={17} /><span>收起导航</span></>}
          </button>
        </div>
      </aside>
    </>
  )
}
