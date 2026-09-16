import type { ReactNode } from 'react'
import type { NavGroup, NavChild } from '../navigation'
import { Header } from './Header'
import { Navigation } from './Navigation'

type LayoutProps = {
  activeId: string
  activeGroup: NavGroup
  activeChild: NavChild
  expandedGroupId: string
  collapsed: boolean
  mobileOpen: boolean
  theme: 'light' | 'dark'
  onNavigate: (id: string) => void
  onToggleGroup: (id: string) => void
  onToggleCollapsed: () => void
  onToggleTheme: () => void
  onOpenMenu: () => void
  onCloseMobile: () => void
  children: ReactNode
}

export function Layout(props: LayoutProps) {
  return (
    <div className={`app-shell ${props.collapsed ? 'sidebar-collapsed' : ''}`}>
      <Navigation
        activeId={props.activeId} expandedGroupId={props.expandedGroupId} collapsed={props.collapsed} mobileOpen={props.mobileOpen}
        onNavigate={props.onNavigate} onToggleGroup={props.onToggleGroup} onToggleCollapsed={props.onToggleCollapsed} onCloseMobile={props.onCloseMobile}
      />
      <div className="main-region">
        <Header group={props.activeGroup} child={props.activeChild} theme={props.theme} onToggleTheme={props.onToggleTheme} onOpenMenu={props.onOpenMenu} />
        <main className="page-content">{props.children}</main>
      </div>
    </div>
  )
}
