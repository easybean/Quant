# 数据验收与运行器声明修正

## 范围与结果

继续真实回测基础开发，不开放实盘、真实数据收益或付费数据。修复当前合成任务报告误称实际运行Nautilus的问题；新增固定证据审核及只读页面，未改变正式回测资格。

- `backtest.py` 新工件补实际实现、参考运行器、未调用声明；保留legacy fixture请求合同。
- `backtest_reports.py` 新报告展示reference-accounting-v1，旧报告展示legacy-unknown/unknown；旧原始报告及哈希不迁移。实现合同不同则不可比较。
- `backtest_readiness.py` 离线核验四文件manifest、原始/normalized SHA及行级OHLCV和实际观察时间、reference、行为响应与事件一致性；写新不可变报告和派生索引，不修改行情或研究资格。
- GET `/api/v1/backtests/data-readiness` 只投影固定报告的公开字段，不接收文件路径。缺失/损坏维持不合格，不在每次页面读取时扫描行情。
- 新建回测页面显示七项检查及版本、时间、固定范围，具有加载/缺失/失败/刷新状态。五股不等于全市场，单项通过不等于数据集合格。

## 实际证据

服务器离线审核2026-09-18T15:48:11Z：AAPL/MSFT/AMZN/GOOGL/TSLA，9/1—9/17，60/60行，零缺口；10/10拆股/分红查询原始证据，1个分红事件。覆盖passed，日历/公司行为partial，可用时点/身份/许可/执行blocked；qualified=false、formal_backtest_available=false。

## 验证与部署

- pytest：`tests/test_backtest_readiness.py tests/test_backtest.py tests/test_backtest_reports.py tests/test_jobs.py`，25 passed，48既有FastAPI弃用警告。
- bundled Node22执行tsc及Vite build通过；保留已有大chunk警告，无新依赖。
- release `/home/davidou/quant/releases/backtest-readiness-20260918-01`，backup `/home/davidou/quant/backups/backtest-readiness-20260918-01`。仅切换四个Python模块及UI，一次重启API；无数据库迁移，不停止采集timer。健康检查、只读七项记录及formal=false通过。
- 浏览器证据：[结果](../acceptance/2026-09-18-backtest-readiness/results.json)。七项显示、503刷新重试、390px无横向溢出、HTTP页面合成提交和新报告、三份旧报告哈希验证通过，无pageerror。验收脚本先因误读API字段失败，核对真实job包装和versions.engine后修正；失败试验产生的隔离合成任务保留，不删除审计记录。
- 回滚：恢复backup中api/backtest/backtest_reports和UI，重启quant-workbench-api.service；新readiness模块及不可变审核记录可保留为未使用工件。部署脚本包含失败回滚，不改原始数据。

## 下一步/仍未完成

P3-03/P3-03A/P3-05继续未勾选。下一步落实证券历史有效期与公司行为适用性证据、事件时间/许可审查，并推进受控真实运行器适配与黄金账务。历史实际下载晚于决策不能通过改时间戳“解决”；无历史vintage的结果不能宣称历史PIT策略收益。完整净值、基准及样本外策略验收仍未完成。
