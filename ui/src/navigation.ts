import type { LucideIcon } from 'lucide-react'
import { Activity, BarChart3, BriefcaseBusiness, ChartCandlestick, Database, FlaskConical, Landmark, LayoutDashboard, Settings2, ShieldCheck } from 'lucide-react'

export type NavAvailability = 'ready' | 'limited' | 'planned'
export type NavChild = { id: string; label: string; description: string; capabilities: string[]; availability: NavAvailability; reason?: string }
export type NavGroup = { id: string; label: string; icon: LucideIcon; children: NavChild[] }

export const navigation: NavGroup[] = [
  { id: 'overview', label: '总览', icon: LayoutDashboard, children: [
    { id: 'workbench', label: '研究工作台', description: '查看本地数据、指标和只读 API 状态摘要；不启动策略、任务或交易连接。', capabilities: ['数据摘要', '指标摘要', '接口状态'], availability: 'ready' },
    { id: 'run-overview', label: '运行概览', description: '计划汇总模拟运行、策略状态和风险提示；当前尚未提供运行实例或连接状态。', capabilities: ['模拟运行汇总', '策略状态', '风险提示'], availability: 'planned', reason: '运行器和连接状态尚未接入。' },
  ] },
  { id: 'market-data', label: '行情与资料', icon: Database, children: [
    { id: 'market-browser', label: '行情浏览', description: '浏览已有美股日线及其周/月汇总；不是实时行情，且不提供交易功能。', capabilities: ['行情检索', 'K 线浏览', '数据口径提示'], availability: 'ready' },
    { id: 'instruments', label: '证券档案', description: '查询系统自动建立的美股证券档案与生命周期；不在此手工创建证券或确认可交易性。', capabilities: ['自动证券档案', '来源与生命周期', '状态提示'], availability: 'ready' },
    { id: 'data-catalog', label: '数据覆盖与质量', description: '查看本地数据盘点的覆盖、来源、频率和质量信息；不代表完整全市场覆盖或统一复权。', capabilities: ['覆盖盘点', '数据质量', '版本信息'], availability: 'ready' },
    { id: 'corporate-actions', label: '分红、拆股与退市', description: '计划查看分红、拆股、并购和退市资料；当前未接入完整公司行为或退市回报。', capabilities: ['公司行为', '退市标识', '历史成分'], availability: 'planned', reason: '完整公司行为与退市数据尚未接入。' },
    { id: 'index-constituents', label: '指数成分股', description: '计划查看标普 500、道琼斯等指数名单及历史变动；当前没有可用的历史成员数据。', capabilities: ['历史成员', '变动对比', '时间线'], availability: 'planned', reason: '指数成员快照尚未接入。' },
    { id: 'derivatives-reference', label: '期货与加密资料', description: '计划维护期货与加密合约规则、换月和资金费资料；当前没有相关规则或交易数据。', capabilities: ['合约规则', '换月资料', '资金费资料'], availability: 'planned', reason: '衍生品数据与规则尚未接入。' },
  ] },
  { id: 'research', label: '研究工具', icon: FlaskConical, children: [
    { id: 'factor-catalogue', label: '选股指标（因子）', description: '查看已登记指标的定义、公式和可计算状态；不能在此修改公式或直接运行研究。', capabilities: ['指标目录', '公式版本', '适用性说明'], availability: 'ready' },
    { id: 'factor-research', label: '指标检验', description: '对管理员配置的合格固定快照提交指标检验；门禁未通过时不会用演示或不完整数据替代。', capabilities: ['IC 检验', '分组结果', '固定快照'], availability: 'limited', reason: '仅接受合格固定快照，不是正式回测。' },
    { id: 'datasets', label: '研究股票池', description: '引用和维护历史保存的股票池版本；它仍是旧版引用，未自动关联证券档案。', capabilities: ['股票池引用', '版本记录', '研究用途'], availability: 'limited', reason: '旧版资产池引用尚未与证券档案自动关联。' },
    { id: 'experiments', label: '研究记录', description: '查看服务端已发布的研究记录及其参数、数据和代码版本；当前为记录查看，不创建新实验。', capabilities: ['实验记录', '版本追溯', '产物索引'], availability: 'ready' },
  ] },
  { id: 'strategies', label: '策略配置', icon: ChartCandlestick, children: [
    { id: 'strategy-templates', label: '策略模板', description: '基于两个固定模板保存版本化策略草稿；不支持新增模板，也不会运行策略或读取行情。', capabilities: ['两个固定模板', '草稿版本', '参数校验'], availability: 'limited', reason: '仅提供两个固定模板，草稿不可执行。' },
    { id: 'my-strategies', label: '我的策略', description: '保存可视化策略定义及其版本；定义不会计算信号、回测、生成订单或连接账户。', capabilities: ['策略草稿', '因子规则', '版本历史'], availability: 'limited', reason: '仅保存不可执行的策略定义。' },
    { id: 'strategy-details', label: '策略详情', description: '查看和编辑同一套可视化策略定义；当前没有独立的已验证策略详情或运行结果。', capabilities: ['策略定义', '约束说明', '版本历史'], availability: 'limited', reason: '与“我的策略”共用策略定义工作区。' },
  ] },
  { id: 'backtests', label: '历史回测', icon: Activity, children: [
    { id: 'new-backtest', label: '新建回测', description: '运行合成美股日线限价验收路径；真实市场回测因资产池、公司行为和退市数据门禁而受阻。', capabilities: ['合成验收', '限价路径', '前置检查'], availability: 'limited', reason: '仅支持合成日线验收，不开放正式回测。' },
    { id: 'backtest-jobs', label: '回测任务', description: '查看服务端研究和回测任务的排队、运行和失败信息；不在此新建或取消任务。', capabilities: ['任务状态', '失败原因', '产物索引'], availability: 'ready' },
    { id: 'backtest-reports', label: '回测报告', description: '计划展示回测收益、成本、持仓和数据警告；当前没有正式回测报告可查看。', capabilities: ['收益风险', '成本报告', '数据警告'], availability: 'planned', reason: '正式回测与报告能力尚未开放。' },
    { id: 'result-comparison', label: '结果对比', description: '计划比较不同回测结果、参数和时间段；当前没有结果对比功能。', capabilities: ['结果比较', '参数差异', '基准比较'], availability: 'planned', reason: '可比较的正式回测结果尚不可用。' },
    { id: 'robustness', label: '策略可靠性检验', description: '计划进行样本外、滚动和成本压力检验；当前没有可靠性检验任务或结果。', capabilities: ['样本外检验', '压力测试', '历史回放'], availability: 'planned', reason: '稳健性研究流程尚未接入。' },
  ] },
  { id: 'portfolio', label: '账户与资金', icon: BriefcaseBusiness, children: [
    { id: 'portfolio-config', label: '策略资金分配', description: '计划配置策略资金分配、权重和再平衡；当前不会保存或执行组合配置。', capabilities: ['资金分配', '目标权重', '再平衡'], availability: 'planned', reason: '组合构建与执行能力尚未接入。' },
    { id: 'paper-accounts', label: '模拟账户', description: '查看和维护模拟账户工作区；不连接外部账户，也不代表真实资金或可交易余额。', capabilities: ['模拟账户', '账户约束', '初始资金'], availability: 'limited', reason: '仅限本地模拟账户工作区。' },
    { id: 'positions-cash', label: '持仓与资金', description: '计划查看模拟持仓、保证金和资金状态；当前没有独立持仓或资金看板。', capabilities: ['持仓', '保证金', '资金状态'], availability: 'planned', reason: '持仓和资金计算尚未接入。' },
    { id: 'cash-ledger', label: '资金流水', description: '查看模拟账户工作区中的资金流水；不代表真实结算，也不会写入外部账户。', capabilities: ['流水记录', '费用记录', '模拟结算'], availability: 'limited', reason: '仅限模拟账户工作区。' },
  ] },
  { id: 'paper-trading', label: '模拟运行', icon: Landmark, children: [
    { id: 'run-instances', label: '模拟策略运行', description: '计划管理模拟策略的启动、暂停和恢复；当前没有模拟运行器可操作。', capabilities: ['启动检查', '运行状态', '恢复'], availability: 'planned', reason: '模拟运行器尚未接入。' },
    { id: 'trade-monitoring', label: '交易监控', description: '计划跟踪模拟信号、风控决定和执行进度；当前不生成信号或订单。', capabilities: ['信号追踪', '风险决定', '执行进度'], availability: 'planned', reason: '模拟执行链路尚未接入。' },
    { id: 'orders-fills', label: '订单与成交', description: '查看模拟账户工作区的订单和成交记录；不发送、撤销或同步外部订单。', capabilities: ['订单状态', '成交记录', '拒单说明'], availability: 'limited', reason: '仅限模拟账户工作区，不连接外部订单。' },
    { id: 'reconciliation', label: '模拟对账', description: '计划核对外部模拟账户差异和待确认订单；当前没有外部账户连接或对账功能。', capabilities: ['账户对账', '断线恢复', '异常确认'], availability: 'planned', reason: '外部模拟账户连接尚未接入。' },
  ] },
  { id: 'risk', label: '风险控制', icon: ShieldCheck, children: [
    { id: 'risk-rules', label: '风险规则', description: '保存版本化的全局风险保护线草稿；规则不会连接账户、拦截订单或执行停单。', capabilities: ['全局规则草稿', '版本历史', '参数校验'], availability: 'limited', reason: '仅保存全局草稿，未接入执行器。' },
    { id: 'risk-dashboard', label: '风险看板', description: '计划展示敞口、杠杆、集中度和回撤；当前没有账户或仓位数据可计算风险。', capabilities: ['风险敞口', '杠杆', '回撤'], availability: 'planned', reason: '账户与持仓数据尚未接入。' },
    { id: 'risk-events', label: '风险事件', description: '计划记录告警、处置和恢复审批；当前没有风险事件或紧急停单流程。', capabilities: ['事件记录', '处置流程', '恢复审批'], availability: 'planned', reason: '风险事件工作流尚未接入。' },
  ] },
  { id: 'performance', label: '收益分析', icon: BarChart3, children: [
    { id: 'returns-risk', label: '收益与风险', description: '计划分析账户、组合和策略的收益风险；当前没有合格回测或模拟运行结果。', capabilities: ['收益曲线', '风险指标', '维度分解'], availability: 'planned', reason: '合格结果数据尚不可用。' },
    { id: 'cost-analysis', label: '成本分析', description: '计划分析手续费、滑点、资金费和换月成本；当前没有可审计的完整成本数据。', capabilities: ['交易成本', '资金费', '换月成本'], availability: 'planned', reason: '完整成本与成交数据尚未接入。' },
    { id: 'attribution', label: '收益来源分析', description: '计划解释市场、行业和因子对收益的影响；当前没有可用于归因的合格结果。', capabilities: ['因子归因', '残差分析', '模型假设'], availability: 'planned', reason: '合格结果与归因模型尚未接入。' },
  ] },
  { id: 'settings', label: '系统维护', icon: Settings2, children: [
    { id: 'connections', label: '数据来源配置', description: '保存数据供应商、覆盖和许可声明草稿；不读取凭证、不检查健康状态，也不连接券商。', capabilities: ['来源声明', '许可声明', '凭证引用名'], availability: 'limited', reason: '仅保存声明草稿，不验证外部连接。' },
    { id: 'resources', label: '后台任务', description: '查看服务端后台任务的状态和失败原因；当前不提供资源容量、磁盘或调度控制。', capabilities: ['任务状态', '失败原因', '产物索引'], availability: 'ready' },
    { id: 'security', label: '用户与安全', description: '计划管理权限、审计和环境隔离；当前没有用户管理或权限配置功能。', capabilities: ['权限管理', '审计记录', '环境隔离'], availability: 'planned', reason: '身份与权限系统尚未接入。' },
    { id: 'maintenance', label: '备份与维护', description: '计划查看备份、恢复检查、版本和运行日志；当前没有维护操作或备份状态。', capabilities: ['备份记录', '恢复检查', '运行日志'], availability: 'planned', reason: '维护与备份服务尚未接入。' },
  ] },
]

export const navChildCount = navigation.reduce((total, group) => total + group.children.length, 0)
export function findNavItem(id: string) { for (const group of navigation) { const child = group.children.find((item) => item.id === id); if (child) return { group, child } }; return { group: navigation[0], child: navigation[0].children[0] } }
export function findNavGroup(id: string) { return navigation.find((group) => group.id === id) ?? navigation[0] }
