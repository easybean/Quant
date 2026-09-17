import { Bell, Menu, Search } from 'lucide-react'
import type { NavGroup, NavChild } from '../navigation'
import { ThemeToggle } from './ThemeToggle'

type HeaderProps = {
  group: NavGroup
  child: NavChild
  theme: 'light' | 'dark'
  onToggleTheme: () => void
  onOpenMenu: () => void
}

export function Header({ group, child, theme, onToggleTheme, onOpenMenu }: HeaderProps) {
  return (
    <header className="topbar">
      <button className="mobile-menu icon-button" type="button" onClick={onOpenMenu} aria-label="打开导航菜单"><Menu size={19} /></button>
      <div className="breadcrumbs" aria-label="当前位置">
        <span>{group.label}</span><span className="breadcrumb-divider">/</span><strong>{child.label}</strong>
      </div>
      <div className="topbar-actions">
        <label className="global-search" title="全局搜索尚未开发，请使用各页面的搜索框"><Search size={16} /><input type="search" disabled placeholder="全局搜索待开发" aria-label="全局搜索尚未开发" /></label>
        <span className="environment-badge">研究环境 · 非实盘</span>
        <span className="data-status"><i aria-hidden="true" /> 接口状态见工作台</span>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        <span className="icon-button" title="通知功能尚未开发" aria-label="通知功能尚未开发"><Bell size={18}/></span>
      </div>
    </header>
  )
}
