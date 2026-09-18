# 策略信号驱动日线闭环

用户明确要求实现“策略信号→下一交易日订单→成交与成本→净值”。本次完成受控合成闭环及已有网页任务/报告集成，不购买数据、不连接券商、不放行未合格真实行情。

## 合同与实现

- 新增 `signal_backtest.py`，operation `synthetic_signal_daily`、dataset `synthetic-signal-daily-v1`、fixture/运行器 `reference-signal-daily-v1`，单ACME/USD/整股/long-only。仅模块内不可变7根2024-01-02—01-10合成OHLCV；任务拒绝bars、文件路径、任意代码、真实快照及未知输入。
- 收盘后实际合成观察时刻21:05UTC计算目标权重，仅使用已经可用的close。上一日净值/close决定目标整股及差额，上一close±固定buffer确定限价，不能读取次日open来设限价或size。订单严格早于下一session open，最后信号不补未来成交。
- 复用 `strategies.target_weights`、`daily_execution.execute_daily_order`、`accounting.apply_cash_fills`。限价、参与率、整股、费用、现金与库存约束；DAY部分成交/不成交余量取消，下一日重新计算而不是暗中续单。
- 每日原始close标记净值，初始权益归一1；每日及终值满足现金+持仓市值=初始+已实现+未实现−费用。PIT迟到、成本负值/非有限数、非法配置fail-closed。金额用Decimal，无融资/做空/分红/拆股/退市/FX/盘中路径能力声明。
- `jobs.py` 新operation验证及持久执行；旧operation与旧工件保留。`backtest.py`只新增静态合成能力登记，formal=false不改。
- `backtest_reports.py`新分支验证任务归属、路径、固定输入确定性重放及完整结果；读取会进行小规模纯计算验证，不创建任务、不读市场文件。公开每日净值/信号/订单，比较必须完整数据/成本/引擎合同一致，旧固定成交路径不可与新路径比较。
- UI独立入口、默认两模板、固定资金/成本配置；必选确认红星/缺项提示/首错聚焦，提交防双击及同键重试、持久任务状态，报告展示每日净值和收盘信号/次日订单。

## 验证与真实部署结果

- 实现者黄金+集成18 passed；主控集成/旧回测/旧报告/jobs/readiness专项31 passed（6项重叠，不累加）。手算黄金包括200初始、次日买2股90、费1、权益199/NAV0.995；未来bar修改不改变此前信号/成交/净值。
- tsc/Vite构建通过，保留原有大chunk警告、FastAPI生命周期弃用警告，无新增依赖。
- 部署后服务器Python环境新运行器专项12 passed，确认实际安装版本的黄金、时点及成本阻断行为。
- release `/home/davidou/quant/releases/signal-backtest-20260919-01`，backup `/home/davidou/quant/backups/signal-backtest-20260919-01`。隔离load新模块验证服务器依赖及账务后安装，切换jobs/backtest/backtest_reports/新增signal_backtest及UI，一次重启API；健康及signal available/formal=false通过。无数据库迁移，不动原始行情、证券成员和采集timers。
- 浏览器 [结果](../acceptance/2026-09-19-signal-backtest/results.json)：实际提交买入持有和2/3双均线、不同成交/收益、报告每日净值、同合同对比、缺确认提示及聚焦、390px无横向溢出，无pageerror。任务ID为03499d3a-b4b7-4bf6-bd92-618fc21f3e8c与ecbd41fa-becf-4eca-bd50-a669462f1419。
- 固定合成结果：买入持有初始1000、终值853、费2、损益−147（−14.7%）；双均线终值958、费2、损益−42（−4.2%），1/8买8股80、1/9限价未成交撤余、1/10卖8股75。差105仅证明策略实际影响订单和账务，不是alpha、真实股票收益或选策略依据。

## 回滚与未完成

恢复backup内旧三个模块及UI，重启用户级quant-workbench-api.service；新增模块与新任务/工件保留作审计记录，不删除数据。旧版本UI/API不会提供新operation，已有新工件仍保存，可再部署本版本读取。部署脚本有失败自动恢复。

P3-03/P3-03A/P3-05仍未完成：真实历史身份、可用时点、公司行为/退市适用性与许可证据、受控真实运行器适配，及真实净值/基准/风险/样本外验收仍待实现。不能把合成闭环改个dataset字符串当作真实回测，也不能把旧价格实际下载时间倒填为历史可用时间。新前瞻留存继续按真实时间采集，不需要为了工程开发空等20天。
