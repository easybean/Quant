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
        <label className="global-search"><Search size={16} /><input type="search" placeholder="搜索功能、因子或资产" aria-label="全局搜索（仅界面原型）" /></label>
        <span className="environment-badge">模拟环境</span>
        <span className="data-status"><i aria-hidden="true" /> 数据接口未接入</span>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        <button className="icon-button notification-button" type="button" aria-label="通知（当前没有新通知）"><Bell size={18} /><span /></button>
      </div>
    </header>
  )
}
