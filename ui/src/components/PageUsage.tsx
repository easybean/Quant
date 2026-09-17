import type { NavChild } from '../navigation'

export function PageUsage({ page, onNavigate }: { page: NavChild; onNavigate: (id: string) => void }) {
  return <section className="page-usage" aria-label="页面使用说明">
    <p><span className="status-tag">{{ ready: '已开放', limited: '受限', planned: '待开发' }[page.availability]}</span> {page.reason}</p>
    <details><summary>这页做什么、什么时候用？</summary><p>{page.description}</p><p>股票池是研究范围，不是资金账户；证券档案是证券资料，不是行情数据；选股指标（因子）是衡量股票的指标，不是已验证的策略。</p><p>页面能保存配置，不代表能运行策略。缺少合格数据、成交模型或风控时，系统仍会阻止正式回测与交易。</p></details>
    {page.id === 'workbench' && <div className="research-start-guide"><strong>从这里开始</strong><p>先看数据，再定义研究范围与策略，最后验证结果。下列是使用顺序，不代表每一步当前都已开放。</p><div>{[
      ['market-browser', '1 看股票行情'], ['data-catalog', '2 检查数据质量'], ['datasets', '3 设置研究股票池'], ['my-strategies', '4 配置策略草稿'], ['new-backtest', '5 检查回测条件'], ['experiments', '6 查看研究记录'],
    ].map(([id, label]) => <button key={id} type="button" onClick={() => onNavigate(id)}>{label}</button>)}</div><p>当前真实回测和自动模拟执行尚未开放；证券自动建档与旧股票池的对接仍待完成。</p></div>}
  </section>
}
