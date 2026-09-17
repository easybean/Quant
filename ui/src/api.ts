export type FactorStatus = '可计算' | '待校准' | '不可运行'

export type Factor = {
  id: string
  name: string
  category: string
  source: string
  measurement_type: string
  tags: string[]
  required_columns: string[]
  intended_use: string
  redundancy_group: string
  description: string
  formula: string
  purpose: string
  data_requirements: string
  limitations: string
  runnable: boolean
  availability_note: string
  status: FactorStatus
}

export type FactorCategory = { name: string; count: number }
export type FactorCatalogueResponse = {
  schema_version: string
  generated_at: string
  categories: FactorCategory[]
  count: number
  items: Factor[]
}

export type DataCatalogueResponse = {
  schema_version: string
  generated_at: string
  inventory: {
    market: string
    frequency: string
    raw_files: number
    derived_files: number
    unique_symbols: number
    lifecycle_records: number
    rows: number
    date_range: { start: string; end: string }
    benchmarks: string[]
    sources: Array<{ name: string; files: number; symbols: number; date_range: { start: string; end: string }; adjustment_status: string }>
    versions: Array<{ name: string; version: string; scope: string }>
  }
  quality: {
    rule_version: string
    findings_rows: number
    findings: Record<string, number>
    limitations: string[]
    structural: { run_at: string; cleaning_status: string; raw_files: number; derived_files_written: number }
  }
  corporate_actions: {
    source: string
    snapshot_id: string
    event_count: number
    golden_checks_passed: number
    golden_checks_total: number
    scope: string
    delisting_resolution: string
  }
}
export type DailySeries = { series_id: string; symbol: string; provider: string; namespace: string; first_date: string; last_date: string; rows: number; quality_warning: string }
export type DailyBar = { date: string; open: number | null; high: number | null; low: number | null; close: number | null; volume: number | null; source: string; adjustment_status: string }
export type DailyBarsResponse = { schema_version: string; series: DailySeries; requested_range: { start: string; end: string }; returned_rows: number; max_returned_rows: number; source: string[]; adjustment_status: string[]; price_basis: string; quality_warning: string; limitations: string[]; bars: DailyBar[] }

export type HealthResponse = {
  status: 'ok'
  schema_version: string
}
export type JobFailure = { code: string; message: string }
export type Job = { id: string; kind: 'research' | 'backtest'; operation: string; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'blocked'; strategy: Record<string, unknown>; parameters: Record<string, unknown>; data_snapshot: string; code_version: string; failure: JobFailure | null; cancel_requested: boolean; attempts: number; created_at: string; started_at: string | null; finished_at: string | null; artifacts: Array<{ name: string; kind: string; job_id: string }> }
export type Experiment = { id: string; job_id: string; strategy: Record<string, unknown>; parameters: Record<string, unknown>; data_snapshot: string; code_version: string; status: string; artifacts: Job['artifacts']; created_at: string }
export type FactorResearchAvailability = { available: boolean; snapshots: string[]; message: string }
export type BacktestAvailability = {
  formal_backtest_available: boolean
  formal_backtest_reason: string
  synthetic_acceptance: {
    available: boolean
    operation: 'synthetic_daily_limit'
    dataset_version: string
    asset_pool_version: string
    calendar_version: string
    engine_version: string
    scope: string
    limitations: string[]
  }
}
export type BacktestReportContract = { dataset_version: string; asset_pool_version: string; calendar_version: string; engine_version: string; price_basis: string; currency: 'USD'; date_range: { start: string; end: string }; bars_sha256: string; initial_cash: string; corporate_actions: string; execution_contract: Record<string, string> }
export type BacktestReportMetrics = { initial_cash: string; free_cash: string; terminal_equity: string; total_pnl: string; total_return_pct: string; fees: string; unrealized_pnl: string }
export type BacktestReportSummary = { job_id: string; available: boolean; reason?: string; label?: string; contract?: BacktestReportContract; metrics?: BacktestReportMetrics; versions?: { backtest_schema: string; engine: Record<string, unknown>; code_version: string; input_fingerprint: string; strategy: Record<string, unknown> }; created_at?: string | null }
export type BacktestReport = Omit<BacktestReportSummary, 'available' | 'label' | 'contract' | 'metrics' | 'versions'> & { available: true; label: string; contract: BacktestReportContract; metrics: BacktestReportMetrics; versions: { backtest_schema: string; engine: Record<string, unknown>; code_version: string; input_fingerprint: string; strategy: Record<string, unknown> }; artifact: { name: string; sha256: string; sha256_status: 'computed_read_time_only'; report_hash: string; report_hash_verified: boolean }; holdings: Array<{ symbol: string; quantity: string; mark_price: string; market_value: string; price_basis: string }>; fills: Array<{ day: string; quantity: string; price: string; fee_usd: string }>; execution: Record<string, unknown>; warnings: string[] }
export type BacktestComparison = { schema_version: string; left: BacktestReportSummary; right: BacktestReportSummary; comparable: boolean; non_comparable_reasons: string[]; comparison_scope: string; differences?: { terminal_equity: string; total_pnl: string; total_return_pct: string; fees: string }; version_differences?: Record<string, { left: unknown; right: unknown }> }
export type StrategyTemplate = { id: 'buy_and_hold' | 'dual_moving_average'; version: string; name: string; description: string; parameters: Record<string, { type: string; required: boolean; default?: string; range?: string; description: string }> }
export type StrategyDraft = { id: string; draft_id: string; name: string; template: StrategyTemplate['id']; template_version: string; parameters: Record<string, unknown>; created_at: string; updated_at?: string; version: number }
export type VisualSignalRule = { factor_id: string; operator: 'gt' | 'gte' | 'lt' | 'lte'; threshold: number; direction: 'long' | 'exclude' }
export type VisualStrategyDefinition = { asset_pool: { asset_pool_draft_id: string; asset_pool_version: number }; signal_rules: VisualSignalRule[]; combination: 'all' | 'any'; rebalance_frequency: 'daily' | 'weekly' | 'monthly'; allocation: { mode: 'equal_weight' | 'fixed_weight'; target_weight?: number }; constraints: { max_position_weight: number; max_holdings: number } }
export type VisualStrategyDraft = { id: string; draft_id: string; name: string; definition: VisualStrategyDefinition; created_at: string; updated_at?: string; version: number; schema_version: string }
export type InstrumentDraft = { id: string; draft_id: string; name: string; instrument: Record<string, unknown>; created_at: string; updated_at?: string; version: number; schema_version: string }
export type LegacyInstrumentMember = { instrument_draft_id: string; instrument_version: number }
export type SecurityCatalogueMember = { member_type: 'security_catalogue_v1'; catalog_id: string; source_checksum: string; record_snapshot: SecurityRecord }
export type AssetPoolMember = LegacyInstrumentMember | SecurityCatalogueMember
export type AssetPoolDraft = { id: string; draft_id: string; name: string; purpose: string; instruments: AssetPoolMember[]; instrument_count: number; legacy_instrument_count?: number; catalogue_member_count?: number; research_qualified?: false; created_at: string; updated_at?: string; version: number; schema_version: string }
export type RiskPolicy = { scope: 'global'; max_instrument_exposure_pct: string; max_market_exposure_pct: string; max_gross_leverage: string; max_daily_loss_pct: string; max_drawdown_pct: string; max_orders_per_minute: number; trading_halted: boolean }
export type RiskPolicyDraft = { id: string; draft_id: string; name: string; policy: RiskPolicy; created_at: string; updated_at?: string; version: number; schema_version: string }
export type DataSource = { provider: 'alpaca' | 'nasdaq' | 'binance' | 'ctp' | 'ibkr'; market: 'US' | 'CRYPTO' | 'CN_FUTURES' | 'OVERSEAS_FUTURES'; product: string; frequency: '1m' | '5m' | '1h' | '1d'; coverage_declaration: string; license_declaration: string; credential_reference: string; verification_status: 'declared' }
export type DataSourceDraft = { id: string; draft_id: string; name: string; source: DataSource; created_at: string; updated_at?: string; version: number; schema_version: string }
export type PaperAccount = { id: string; name: string; account_type: 'paper'; base_currency: string; initial_cash: string; margin_mode: 'cash' | 'cross' | 'isolated'; created_at: string; schema_version: string }
export type LedgerEvent = { id: string; account_id: string; event_type: 'order' | 'fill' | 'fee' | 'audit'; dedupe_key: string; event: Record<string, unknown>; cash_delta: string; position_delta: string; created_at: string; schema_version: string }
export type LedgerView = { items: LedgerEvent[]; reconciliation: { initial_cash: string; recorded_cash_delta: string; reconciled_cash: string; net_recorded_position: string; event_count: number; scope: string } }

const baseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://192.168.1.132:8511').replace(/\/$/, '')
export const factorCatalogueUrl = `${baseUrl}/api/v1/factors`
export const dataCatalogueUrl = `${baseUrl}/api/v1/data-catalogue`
export const dailySearchUrl = `${baseUrl}/api/v1/market-data/us-daily/search`
export const dailyBarsUrl = `${baseUrl}/api/v1/market-data/us-daily/bars`
export const healthUrl = `${baseUrl}/api/v1/health`
export const jobsUrl = `${baseUrl}/api/v1/jobs`
export const experimentsUrl = `${baseUrl}/api/v1/experiments`
export const factorResearchAvailabilityUrl = `${baseUrl}/api/v1/factor-research/availability`
export const backtestAvailabilityUrl = `${baseUrl}/api/v1/backtests/availability`
export const backtestReportsUrl = `${baseUrl}/api/v1/backtests/reports`
export const strategyTemplatesUrl = `${baseUrl}/api/v1/strategy-templates`
export const strategyDraftsUrl = `${baseUrl}/api/v1/strategy-drafts`
export const visualStrategyDraftsUrl = `${baseUrl}/api/v1/visual-strategy-drafts`
export const instrumentDraftsUrl = `${baseUrl}/api/v1/instrument-drafts`
export type SecurityRecord = { catalog_id: string; symbol: string; name: string | null; exchange: string | null; asset_type: string; status: string; ipo_date: string | null; delisting_date: string | null; source_as_of: string | null; source_checksum: string | null; identity_status: string; source?: string; quarantined?: boolean; research_qualified: false }
export type SecurityCatalogue = { items: SecurityRecord[]; total: number; total_records: number; quarantined_records: number; research_qualified: false }
export async function fetchSecurityCatalogue(query: string, signal: AbortSignal): Promise<SecurityCatalogue> {
  const params = new URLSearchParams({ query, limit: '50' })
  const response = await fetch(`${baseUrl}/api/v1/security-catalogue?${params}`, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`证券目录读取失败（${response.status}），请重试。`)
  const payload: unknown = await response.json()
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as SecurityCatalogue).items) || typeof (payload as SecurityCatalogue).total_records !== 'number') throw new Error('证券目录返回了无效数据，请重试。')
  return payload as SecurityCatalogue
}
export const assetPoolDraftsUrl = `${baseUrl}/api/v1/asset-pool-drafts`
export const riskPolicyDraftsUrl = `${baseUrl}/api/v1/risk-policy-drafts`
export const paperAccountsUrl = `${baseUrl}/api/v1/paper-accounts`
export const dataSourceDraftsUrl = `${baseUrl}/api/v1/data-source-drafts`

export async function fetchFactorCatalogue(signal: AbortSignal): Promise<FactorCatalogueResponse> {
  const response = await fetch(factorCatalogueUrl, { method: 'GET', signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`目录服务暂不可用（HTTP ${response.status}）`)
  const payload: unknown = await response.json()
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as FactorCatalogueResponse).items)) {
    throw new Error('目录服务返回了无效数据')
  }
  return payload as FactorCatalogueResponse
}

export async function fetchDataCatalogue(signal: AbortSignal): Promise<DataCatalogueResponse> {
  const response = await fetch(dataCatalogueUrl, { method: 'GET', signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`数据目录暂不可用（HTTP ${response.status}）`)
  const payload: unknown = await response.json()
  if (!payload || typeof payload !== 'object' || !('inventory' in payload) || !('quality' in payload)) {
    throw new Error('数据目录返回了无效快照')
  }
  return payload as DataCatalogueResponse
}

export async function searchDailySeries(query: string, signal: AbortSignal): Promise<DailySeries[]> {
  const response = await fetch(`${dailySearchUrl}?query=${encodeURIComponent(query)}&limit=10`, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`日线索引暂不可用（HTTP ${response.status}）`)
  const payload: unknown = await response.json()
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as { items?: unknown }).items)) throw new Error('日线索引返回了无效数据')
  return (payload as { items: DailySeries[] }).items
}

export async function fetchDailyBars(seriesId: string, start: string, end: string, signal: AbortSignal): Promise<DailyBarsResponse> {
  const query = new URLSearchParams({ series_id: seriesId, start, end })
  const response = await fetch(`${dailyBarsUrl}?${query}`, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`原始日线暂不可用（HTTP ${response.status}）`)
  const payload: unknown = await response.json()
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as DailyBarsResponse).bars)) throw new Error('原始日线返回了无效数据')
  return payload as DailyBarsResponse
}

export async function fetchHealth(signal: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(healthUrl, { method: 'GET', signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`只读 API 暂不可用（HTTP ${response.status}）`)
  const payload: unknown = await response.json()
  if (!payload || typeof payload !== 'object' || (payload as HealthResponse).status !== 'ok') {
    throw new Error('只读 API 返回了无效状态')
  }
  return payload as HealthResponse
}

async function fetchCollection<T>(url: string, signal: AbortSignal, label: string): Promise<T[]> {
  const response = await fetch(url, { method: 'GET', signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`${label}暂不可用（HTTP ${response.status}）`)
  const payload: unknown = await response.json()
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as { items?: unknown }).items)) throw new Error(`${label}返回了无效数据`)
  return (payload as { items: T[] }).items
}
export const fetchJobs = (signal: AbortSignal) => fetchCollection<Job>(jobsUrl, signal, '任务状态')
export const fetchExperiments = (signal: AbortSignal) => fetchCollection<Experiment>(experimentsUrl, signal, '实验记录')
export const fetchStrategyTemplates = (signal: AbortSignal) => fetchCollection<StrategyTemplate>(strategyTemplatesUrl, signal, '策略模板')
export const fetchStrategyDrafts = (signal: AbortSignal) => fetchCollection<StrategyDraft>(strategyDraftsUrl, signal, '策略草稿')
export const fetchStrategyDraftHistory = (id: string, signal: AbortSignal) => fetchCollection<StrategyDraft>(`${strategyDraftsUrl}/${id}/history`, signal, '草稿历史')
export async function saveStrategyDraft(body: Record<string, unknown>, id?: string): Promise<StrategyDraft> {
  const response = await fetch(id ? `${strategyDraftsUrl}/${id}` : strategyDraftsUrl, { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `保存失败（HTTP ${response.status}）`)
  return payload as StrategyDraft
}
export const fetchVisualStrategyDrafts = (signal: AbortSignal) => fetchCollection<VisualStrategyDraft>(visualStrategyDraftsUrl, signal, '可视化策略定义')
export const fetchVisualStrategyDraftHistory = (id: string, signal: AbortSignal) => fetchCollection<VisualStrategyDraft>(`${visualStrategyDraftsUrl}/${id}/history`, signal, '策略定义历史')
export async function saveVisualStrategyDraft(body: Record<string, unknown>, id?: string): Promise<VisualStrategyDraft> {
  const response = await fetch(id ? `${visualStrategyDraftsUrl}/${id}` : visualStrategyDraftsUrl, { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `保存失败（HTTP ${response.status}）`)
  return payload as VisualStrategyDraft
}
export const fetchInstrumentDrafts = (signal: AbortSignal) => fetchCollection<InstrumentDraft>(instrumentDraftsUrl, signal, '标的定义草稿')
export const fetchInstrumentDraftHistory = (id: string, signal: AbortSignal) => fetchCollection<InstrumentDraft>(`${instrumentDraftsUrl}/${id}/history`, signal, '标的定义历史')
export async function saveInstrumentDraft(body: Record<string, unknown>, id?: string): Promise<InstrumentDraft> {
  const response = await fetch(id ? `${instrumentDraftsUrl}/${id}` : instrumentDraftsUrl, { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `保存失败（HTTP ${response.status}）`)
  return payload as InstrumentDraft
}
export const fetchAssetPoolDrafts = (signal: AbortSignal) => fetchCollection<AssetPoolDraft>(assetPoolDraftsUrl, signal, '研究资产池草稿')
export const fetchAssetPoolDraftHistory = (id: string, signal: AbortSignal) => fetchCollection<AssetPoolDraft>(`${assetPoolDraftsUrl}/${id}/history`, signal, '研究资产池历史')
export async function saveAssetPoolDraft(body: Record<string, unknown>, id?: string): Promise<AssetPoolDraft> {
  const response = await fetch(id ? `${assetPoolDraftsUrl}/${id}` : assetPoolDraftsUrl, { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `保存失败（HTTP ${response.status}）`)
  return payload as AssetPoolDraft
}
export const fetchRiskPolicyDrafts = (signal: AbortSignal) => fetchCollection<RiskPolicyDraft>(riskPolicyDraftsUrl, signal, '全局风控规则草稿')
export const fetchRiskPolicyDraftHistory = (id: string, signal: AbortSignal) => fetchCollection<RiskPolicyDraft>(`${riskPolicyDraftsUrl}/${id}/history`, signal, '全局风控规则历史')
export async function saveRiskPolicyDraft(body: Record<string, unknown>, id?: string): Promise<RiskPolicyDraft> {
  const response = await fetch(id ? `${riskPolicyDraftsUrl}/${id}` : riskPolicyDraftsUrl, { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `保存失败（HTTP ${response.status}）`)
  return payload as RiskPolicyDraft
}
export const fetchDataSourceDrafts = (signal: AbortSignal) => fetchCollection<DataSourceDraft>(dataSourceDraftsUrl, signal, '数据源接入草稿')
export const fetchDataSourceDraftHistory = (id: string, signal: AbortSignal) => fetchCollection<DataSourceDraft>(`${dataSourceDraftsUrl}/${id}/history`, signal, '数据源接入历史')
export async function saveDataSourceDraft(body: Record<string, unknown>, id?: string): Promise<DataSourceDraft> {
  const response = await fetch(id ? `${dataSourceDraftsUrl}/${id}` : dataSourceDraftsUrl, { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `保存失败（HTTP ${response.status}）`)
  return payload as DataSourceDraft
}
export const fetchPaperAccounts = (signal: AbortSignal) => fetchCollection<PaperAccount>(paperAccountsUrl, signal, '模拟账户')
export async function createPaperAccount(body: Record<string, unknown>): Promise<PaperAccount> {
  const response = await fetch(paperAccountsUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `创建失败（HTTP ${response.status}）`)
  return payload as PaperAccount
}
export async function fetchPaperLedger(id: string, signal: AbortSignal): Promise<LedgerView> {
  const response = await fetch(`${paperAccountsUrl}/${id}/ledger`, { signal, headers: { Accept: 'application/json' } })
  const payload: unknown = await response.json()
  if (!response.ok || !payload || typeof payload !== 'object' || !Array.isArray((payload as LedgerView).items)) throw new Error(`模拟账户台账暂不可用（HTTP ${response.status}）`)
  return payload as LedgerView
}
export async function fetchFactorResearchAvailability(signal: AbortSignal): Promise<FactorResearchAvailability> {
  const response = await fetch(factorResearchAvailabilityUrl, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`研究门禁暂不可用（HTTP ${response.status}）`)
  return response.json() as Promise<FactorResearchAvailability>
}
export async function fetchBacktestAvailability(signal: AbortSignal): Promise<BacktestAvailability> {
  const response = await fetch(backtestAvailabilityUrl, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`回测门禁暂不可用（HTTP ${response.status}）`)
  const payload: unknown = await response.json()
  if (!payload || typeof payload !== 'object' || !('formal_backtest_available' in payload) || !('synthetic_acceptance' in payload)) throw new Error('回测门禁返回了无效数据')
  return payload as BacktestAvailability
}
export async function fetchBacktestReports(signal: AbortSignal): Promise<BacktestReportSummary[]> {
  const response = await fetch(backtestReportsUrl, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`回测报告暂不可用（HTTP ${response.status}）`)
  const payload: unknown = await response.json()
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as { items?: unknown }).items)) throw new Error('回测报告列表返回了无效数据')
  return (payload as { items: BacktestReportSummary[] }).items
}
export async function fetchBacktestReport(jobId: string, signal: AbortSignal): Promise<BacktestReport> {
  const response = await fetch(`${backtestReportsUrl}/${encodeURIComponent(jobId)}`, { signal, headers: { Accept: 'application/json' } })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `报告读取失败（HTTP ${response.status}）`)
  if (!payload || typeof payload !== 'object' || (payload as BacktestReport).available !== true || !('artifact' in payload)) throw new Error('回测报告返回了无效数据')
  return payload as BacktestReport
}
export async function compareBacktestReports(leftJobId: string, rightJobId: string, signal: AbortSignal): Promise<BacktestComparison> {
  const query = new URLSearchParams({ left_job_id: leftJobId, right_job_id: rightJobId })
  const response = await fetch(`${backtestReportsUrl}/compare?${query}`, { signal, headers: { Accept: 'application/json' } })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `结果对比失败（HTTP ${response.status}）`)
  if (!payload || typeof payload !== 'object' || typeof (payload as BacktestComparison).comparable !== 'boolean') throw new Error('结果对比返回了无效数据')
  return payload as BacktestComparison
}
export async function submitBacktest(body: Record<string, unknown>, key: string): Promise<Job> {
  const response = await fetch(jobsUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify(body) })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `提交失败（HTTP ${response.status}）`)
  return (payload as { job: Job }).job
}
export async function submitFactorResearch(body: Record<string, unknown>, key: string): Promise<Job> {
  const response = await fetch(jobsUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify(body) })
  const payload: unknown = await response.json()
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `提交失败（HTTP ${response.status}）`)
  return (payload as { job: Job }).job
}
export async function fetchJob(id: string, signal: AbortSignal): Promise<Job> {
  const response = await fetch(`${jobsUrl}/${id}`, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error('无法读取任务状态')
  return response.json() as Promise<Job>
}
export async function fetchFactorResult(id: string, signal: AbortSignal): Promise<Record<string, unknown>> {
  const response = await fetch(`${jobsUrl}/${id}/result`, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error('结果尚未发布')
  return response.json() as Promise<Record<string, unknown>>
}
