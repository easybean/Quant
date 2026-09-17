import { useEffect, useState } from 'react'
import { Dashboard } from './components/Dashboard'
import { DataCatalogue } from './components/DataCatalogue'
import { FactorCatalogue } from './components/FactorCatalogue'
import { FactorResearch } from './components/FactorResearch'
import { Layout } from './components/Layout'
import { PlaceholderPage } from './components/PlaceholderPage'
import { TaskOverview } from './components/TaskOverview'
import { BacktestWizard } from './components/BacktestWizard'
import { StrategyWorkspace } from './components/StrategyWorkspace'
import { VisualStrategyWorkspace } from './components/VisualStrategyWorkspace'
import { SecurityCatalogueWorkspace } from './components/SecurityCatalogueWorkspace'
import { AssetPoolWorkspace } from './components/AssetPoolWorkspace'
import { RiskPolicyWorkspace } from './components/RiskPolicyWorkspace'
import { DataSourceWorkspace } from './components/DataSourceWorkspace'
import { PaperAccountWorkspace } from './components/PaperAccountWorkspace'
import { MarketBrowser } from './components/MarketBrowser'
import { PageUsage } from './components/PageUsage'
import { findNavGroup, findNavItem } from './navigation'

export default function App() {
  const [activeId, setActiveId] = useState('workbench')
  const [expandedGroupId, setExpandedGroupId] = useState('overview')
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [theme, setTheme] = useState<'light' | 'dark'>('light')
  const { group, child } = findNavItem(activeId)

  useEffect(() => { document.documentElement.dataset.theme = theme }, [theme])
  const navigate = (id: string) => { const item = findNavItem(id); setActiveId(id); setExpandedGroupId(item.group.id); setMobileOpen(false) }
  const toggleGroup = (id: string) => {
    const target = findNavGroup(id)
    setCollapsed(false)
    navigate((target.children.find(item => item.availability !== 'planned') || target.children[0]).id)
  }
  let content
  if (activeId === 'workbench') content = <Dashboard />
  else if (activeId === 'market-browser') content = <MarketBrowser />
  else if (activeId === 'data-catalog') content = <DataCatalogue />
  else if (activeId === 'factor-catalogue') content = <FactorCatalogue />
  else if (activeId === 'factor-research') content = <FactorResearch />
  else if (activeId === 'new-backtest') content = <BacktestWizard />
  else if (activeId === 'strategy-templates') content = <StrategyWorkspace />
  else if (activeId === 'my-strategies' || activeId === 'strategy-details') content = <VisualStrategyWorkspace onNavigate={navigate} pageTitle={child.label} />
  else if (activeId === 'instruments') content = <SecurityCatalogueWorkspace />
  else if (activeId === 'datasets') content = <AssetPoolWorkspace onNavigate={navigate} />
  else if (activeId === 'risk-rules') content = <RiskPolicyWorkspace />
  else if (activeId === 'connections') content = <DataSourceWorkspace />
  else if (activeId === 'paper-accounts' || activeId === 'orders-fills' || activeId === 'cash-ledger') content = <PaperAccountWorkspace pageTitle={child.label} />
  else if (activeId === 'experiments') content = <TaskOverview view="experiments" pageTitle={child.label} />
  else if (activeId === 'backtest-jobs' || activeId === 'resources') content = <TaskOverview view="jobs" pageTitle={child.label} />
  else content = <PlaceholderPage group={group} child={child} />
  return <Layout activeId={activeId} activeGroup={group} activeChild={child} expandedGroupId={expandedGroupId} collapsed={collapsed} mobileOpen={mobileOpen} theme={theme} onNavigate={navigate} onToggleGroup={toggleGroup} onToggleCollapsed={() => setCollapsed((value) => !value)} onToggleTheme={() => setTheme((value) => value === 'light' ? 'dark' : 'light')} onOpenMenu={() => setMobileOpen(true)} onCloseMobile={() => setMobileOpen(false)}><PageUsage page={child} onNavigate={navigate}/>{content}</Layout>
}
