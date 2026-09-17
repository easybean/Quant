const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')

const source = fs.readFileSync(require.resolve('../src/navigation.ts'), 'utf8')
const ids = [
  'workbench', 'run-overview', 'market-browser', 'instruments', 'data-catalog', 'corporate-actions', 'index-constituents', 'derivatives-reference',
  'factor-catalogue', 'factor-research', 'datasets', 'experiments', 'strategy-templates', 'my-strategies', 'strategy-details', 'new-backtest',
  'backtest-jobs', 'backtest-reports', 'result-comparison', 'robustness', 'portfolio-config', 'paper-accounts', 'positions-cash', 'cash-ledger',
  'run-instances', 'trade-monitoring', 'orders-fills', 'reconciliation', 'risk-rules', 'risk-dashboard', 'risk-events', 'returns-risk', 'cost-analysis',
  'attribution', 'connections', 'resources', 'security', 'maintenance',
]

test('navigation keeps every route and makes availability explicit', () => {
  assert.match(source, /export type NavAvailability = 'ready' \| 'limited' \| 'planned'/)
  assert.equal(ids.filter((id) => new RegExp(`id: '${id}'`).test(source)).length, ids.length)
  assert.equal((source.match(/availability: '(?:ready|limited|planned)'/g) || []).length, ids.length)
})

test('plain-language labels preserve the agreed destinations', () => {
  for (const label of ['研究工作台', '证券档案', '数据覆盖与质量', '选股指标（因子）', '指标检验', '研究股票池', '研究记录', '数据来源配置', '后台任务']) {
    assert.match(source, new RegExp(`label: '${label}'`))
  }
  assert.match(source, /label: '行情与资料'/)
  assert.match(source, /label: '研究工具'/)
  assert.match(source, /label: '策略配置'/)
  assert.match(source, /label: '历史回测'/)
  assert.match(source, /label: '风险控制'/)
  assert.match(source, /label: '系统维护'/)
  assert.match(source, /label: '模拟运行'/)
  assert.match(source, /label: '收益分析'/)
})
