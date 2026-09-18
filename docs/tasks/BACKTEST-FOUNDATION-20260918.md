# 真实回测基础推进

2026-09-18；用户批准先完成真实回测闭环，不开展模拟盘/实盘，不购买数据。

## 已交付

- `bounded_backtest_data.py` 从不可变Massive grouped原始快照建立五股独立副本，核验manifest/raw/normalized SHA和当前证券reference；固定XNYS日历及版本、成员、原始价与实际观察时间。同日价格修订冲突拒绝，有缺口不删除成员。
- 真实固定目录 `/home/davidou/quant/data/reference/bounded-backtest/20260918-five-stock-v1`：AAPL/MSFT/AMZN/GOOGL/TSLA，9/1—9/17，12个交易日，应有60条、实际60条，各股缺口0。manifest锁bars/calendar/members/report SHA。不是合格正式回测输入。
- 60行均在历史收盘决策后才被观察，不伪造available_at。日历仅为scheduled open/close reference；event_time=null、未独立验证。当前reference不作为历史生命周期证明，qualified=false/formal_backtest_enabled=false。
- report锁定冻结时刻后首个开盘起的20个XNYS观察日；旧版本不滚动修改，未提前称观察完成。它是工程证据，不是策略有效性或其他开发必须停等20天的理由。
- `bounded_actions_capture.py` 使用新stocks/v1 splits/dividends，对五股限定31天以内窗口保存原始响应、HTTP/SHA/观察时间、原始现金额/ex-date/pay-date；分页/身份/范围/重复ID/非法金额拒绝。空响应不等于独立无事件证明，不自动放行。共享Massive锁和16秒限流，401/403/429结束本次查询。
- `daily_execution.py` 纯Decimal单订单合成黄金原型：买卖限价、开盘优先/严格区间触价、半点差/不利滑点、按股和最低佣金、整股参与率/现金/库存上限。提交必须早于session开盘；成本后突破限价/日线范围不成交、不夹价格；未成交余额及费用明确。无融资/做空/多订单现金冻结/盘中路径。未接入Nautilus或真实回测，不扩大能力登记。
- `dividend_accounting.py` 分红应收黄金原型：除权日由调用方明确提供开盘前有权股数，锁定原始每股金额及支付日，未付金额仅为receivable、不进可用现金；按支付日一次到账，卖出后权利仍按锁定股数，重复事件/支付幂等且冲突拒绝。人工GOOGL示例10股×0.22=2.20，不把供应商后来公布字段倒填为历史策略可知信息。不实现税费、特殊分配、价格复权或自动来源资格。

## 验证与部署

- 数据/行为专项15 passed；回测/报告/snapshot/PIT及新工具集中57 passed（集合重叠不累加），保留20个既有FastAPI弃用警告。
- 实现Agent成交/账务黄金21 passed；review修复开盘文案、小数vendor volume、成本基础/费用不变量。主控把roundtrip卖出改为下一交易日，成交专项14 passed。
- release `/home/davidou/quant/releases/backtest-foundation-20260918-01`：服务器compile后仅安装3个新增工具，不替换API/UI、不重启服务、不改真实回测/因子门禁、不停止全量补数。回滚为停止调用新增工具，保留原数据和证据，无数据库迁移。

最后追加分红应收模块，总共4个新增工具；日线成交及分红结算均限定USD，无FX证据的其他币种拒绝，entitlement book必须不可变tuple、事件ID无前后空白。最后Agent成本/分红/旧账务31 passed，服务器四个新增工具专项39 passed。最终模块原子更新（新增分红文件首次安装），未重启API/UI；固定副本SHA逐项核验通过，全量补数/逐码/日更/前瞻证据留存timer均enabled。首次29 passed和最终39 passed有重叠，不相加为独立覆盖。

## 未完成及下一验收

真实公司行为查询已完成：10/10均captured，返回1条GOOGL现金分红，零查询失败；固定目录`/home/davidou/quant/data/reference/bounded-backtest/20260918-actions-v1`，report SHA `2b1a4662ff0be95d166706b5be8b68e498815232c70b808edd7ecc8a0ba0028b`。供应商记录：每股USD0.22、除权9/4、record9/7、支付9/14，declared7/22。发行人发布在[SEC的2026Q2公告](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000066/googexhibit991q22026.htm)核对了普通股0.22及record/pay-date；这未独立确认ex-date、其他证券无事件、全范围生命周期或PIT vintage。原始response与observed时间继续保留；不因10个HTTP200升级qualification。

服务器隔离测试29 passed（3个新增工具文件）；线上`/api/v1/backtests/availability`真实返回formal_backtest_available=false。曾误用不存在的backtest-capabilities端点得到404，已改正确路由核验；不以404推断服务故障。

1. 公司行为剩余独立核验，将已通过的分红应收黄金语义接入经审核的运行器；现金可用时点仍须实际支付规则，不以除权日提前到账。
2. 历史身份、event time、许可及当时可用性独立证据。缺历史vintage时历史PIT收益保持阻断；新前瞻留存不可倒填ingestion。
3. 资格审核后真实运行器适配、两个模板黄金/同版本重跑、净值与基准报告。cost原型不是Nautilus真实能力，不提供旁路收益入口。

P3-03/P3-03A/P3-05保持未勾选。五股仅工程验收；全量美股采集范围不缩减。

本轮接口依据：[Dividends](https://massive.com/docs/rest/stocks/corporate-actions/dividends)、[Splits](https://massive.com/docs/rest/stocks/corporate-actions/splits)，未使用旧v3接口。
