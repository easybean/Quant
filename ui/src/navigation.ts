import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  BarChart3,
  BriefcaseBusiness,
  ChartCandlestick,
  Database,
  FlaskConical,
  Landmark,
  LayoutDashboard,
  Settings2,
  ShieldCheck,
} from 'lucide-react'

export type NavChild = {
  id: string
  label: string
  description: string
  capabilities: string[]
}

export type NavGroup = {
  id: string
  label: string
  icon: LucideIcon
  children: NavChild[]
}

export const navigation: NavGroup[] = [
  {
    id: 'overview', label: '总览', icon: LayoutDashboard,
    children: [
      { id: 'workbench', label: '我的工作台', description: '最近策略、实验、运行任务与待处理问题。', capabilities: ['最近活动', '任务摘要', '系统状态'] },
      { id: 'run-overview', label: '运行概览', description: '模拟资产、策略状态、连接状态与风险提示。', capabilities: ['模拟资产', '连接状态', '风险提示'] },
    ],
  },
  {
    id: 'market-data', label: '市场与数据', icon: Database,
    children: [
      { id: 'market-browser', label: '行情浏览', description: '自选、搜索、K线、成交量和合约信息。', capabilities: ['行情检索', 'K线', '合约信息'] },
      { id: 'instruments', label: '资产与合约', description: '单个证券、交易对和期货合约的版本化定义。', capabilities: ['标的定义', '合约规则', '版本历史'] },
      { id: 'data-catalog', label: '数据目录', description: '覆盖范围、频率、来源、质量和数据版本。', capabilities: ['覆盖分析', '数据质量', '版本追踪'] },
      { id: 'corporate-actions', label: '股票事件', description: '拆分、分红、退市和历史股票池。', capabilities: ['公司行为', '退市标识', '历史成分'] },
      { id: 'index-constituents', label: '指数成分', description: '当前/历史名单、变动时间线和名单对比。', capabilities: ['历史名单', '变动对比', '时间线'] },
      { id: 'derivatives-reference', label: '衍生品资料', description: '日历、合约规则、换月映射和资金费。', capabilities: ['规则日历', '换月映射', '资金费'] },
    ],
  },
  {
    id: 'research', label: '研究实验室', icon: FlaskConical,
    children: [
      { id: 'factor-catalogue', label: '因子百科', description: '分类、定义、公式、适用市场和限制。', capabilities: ['因子分类', '公式版本', '适用性'] },
      { id: 'factor-research', label: '因子研究', description: '分布、相关性、IC、分组收益、衰减与成本。', capabilities: ['相关性', 'IC 分析', '成本检验'] },
      { id: 'datasets', label: '数据集与资产池', description: '引用已保存标的版本的研究资产池与用途说明。', capabilities: ['成员引用', '资产池版本', '研究用途'] },
      { id: 'experiments', label: '实验记录', description: '参数、数据/代码版本、指标与结论。', capabilities: ['实验追踪', '版本对比', 'Notebook'] },
    ],
  },
  {
    id: 'strategies', label: '策略中心', icon: ChartCandlestick,
    children: [
      { id: 'strategy-templates', label: '策略模板', description: '趋势、动量、均值回归和横截面选股。', capabilities: ['模板库', '策略说明', '适用范围'] },
      { id: 'my-strategies', label: '我的策略', description: '草稿、版本、参数、信号和仓位规则。', capabilities: ['草稿管理', '版本管理', '参数'] },
      { id: 'strategy-details', label: '策略详情', description: '逻辑说明、适用范围、依赖与验证记录。', capabilities: ['逻辑说明', '依赖检查', '验证记录'] },
    ],
  },
  {
    id: 'backtests', label: '回测中心', icon: Activity,
    children: [
      { id: 'new-backtest', label: '新建回测', description: '分步配置、能力检查、成本和成交假设。', capabilities: ['分步配置', '能力检查', '成本假设'] },
      { id: 'backtest-jobs', label: '回测任务', description: '排队、运行、取消和失败原因。', capabilities: ['任务队列', '运行状态', '失败诊断'] },
      { id: 'backtest-reports', label: '回测报告', description: '收益、风险、持仓、订单、成本和数据警告。', capabilities: ['收益风险', '成本报告', '数据警告'] },
      { id: 'result-comparison', label: '结果对比', description: '基准、参数、时间段与版本差异。', capabilities: ['多结果比较', '参数差异', '基准'] },
      { id: 'robustness', label: '稳健性验证', description: '样本外、滚动验证、成本压力测试和历史回放。', capabilities: ['样本外', '压力测试', '历史回放'] },
    ],
  },
  {
    id: 'portfolio', label: '组合与账户', icon: BriefcaseBusiness,
    children: [
      { id: 'portfolio-config', label: '组合配置', description: '策略资金分配、目标权重和再平衡。', capabilities: ['资金分配', '目标权重', '再平衡'] },
      { id: 'paper-accounts', label: '模拟账户', description: '币种、初始资金、保证金模式和账户约束。', capabilities: ['账户约束', '保证金模式', '初始资金'] },
      { id: 'positions-cash', label: '持仓与资金', description: '可用资金、冻结、保证金和未实现盈亏。', capabilities: ['持仓', '保证金', '资金状态'] },
      { id: 'cash-ledger', label: '资金流水', description: '成交、手续费、资金费、分红、结算及虚拟出入金。', capabilities: ['流水', '费用', '结算'] },
    ],
  },
  {
    id: 'paper-trading', label: '模拟交易', icon: Landmark,
    children: [
      { id: 'run-instances', label: '运行实例', description: '启动检查、运行、暂停、停止与恢复。', capabilities: ['启动检查', '生命周期', '恢复'] },
      { id: 'trade-monitoring', label: '交易监控', description: '信号、目标仓位、风险决定和执行进度。', capabilities: ['信号追踪', '风险决定', '执行进度'] },
      { id: 'orders-fills', label: '订单与成交', description: '生命周期、拒单原因、撤单和关联追踪。', capabilities: ['订单状态', '拒单原因', '成交关联'] },
      { id: 'reconciliation', label: '同步与对账', description: '外部模拟账户差异、断线恢复和待确认订单。', capabilities: ['账户对账', '断线恢复', '异常确认'] },
    ],
  },
  {
    id: 'risk', label: '风控中心', icon: ShieldCheck,
    children: [
      { id: 'risk-rules', label: '风控规则', description: '账户、组合、策略和品种限额。', capabilities: ['多层限额', '规则版本', '拦截说明'] },
      { id: 'risk-dashboard', label: '风险看板', description: '敞口、杠杆、集中度、保证金和回撤。', capabilities: ['风险敞口', '杠杆', '回撤'] },
      { id: 'risk-events', label: '风险事件', description: '告警、处置记录、紧急停单和恢复审批。', capabilities: ['事件记录', '处置流程', '恢复审批'] },
    ],
  },
  {
    id: 'performance', label: '绩效分析', icon: BarChart3,
    children: [
      { id: 'returns-risk', label: '收益与风险', description: '账户、组合、策略和市场分解。', capabilities: ['多维分解', '收益曲线', '风险指标'] },
      { id: 'cost-analysis', label: '成本分析', description: '手续费、滑点、资金费、借贷和换月成本。', capabilities: ['交易成本', '资金费', '换月成本'] },
      { id: 'attribution', label: '归因分析', description: '市场、行业、因子解释，展示残差及模型假设。', capabilities: ['因子归因', '残差', '模型假设'] },
    ],
  },
  {
    id: 'settings', label: '系统设置', icon: Settings2,
    children: [
      { id: 'connections', label: '数据源与交易连接', description: '能力、健康状态和凭证引用。', capabilities: ['连接健康', '能力清单', '凭证引用'] },
      { id: 'resources', label: '任务与资源', description: '采集、计算、调度、磁盘与容量。', capabilities: ['任务调度', '资源使用', '容量'] },
      { id: 'security', label: '用户与安全', description: '权限、审计和环境隔离。', capabilities: ['权限', '审计', '环境隔离'] },
      { id: 'maintenance', label: '备份与维护', description: '备份记录、恢复检查、版本与日志。', capabilities: ['备份记录', '恢复检查', '运行日志'] },
    ],
  },
]

export const navChildCount = navigation.reduce((total, group) => total + group.children.length, 0)

export function findNavItem(id: string) {
  for (const group of navigation) {
    const child = group.children.find((item) => item.id === id)
    if (child) return { group, child }
  }
  return { group: navigation[0], child: navigation[0].children[0] }
}

export function findNavGroup(id: string) {
  return navigation.find((group) => group.id === id) ?? navigation[0]
}
