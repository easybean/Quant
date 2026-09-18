# P3-03A 有限美股研究规范

2026-09-18：用户批准先限定可核验研究池与区间；全量美股行情同步不缩减。

## 范围与声明

第一批固定 AAPL、MSFT、AMZN、GOOGL、TSLA，仅为数据与账务链路验收，不代表美股全市场、不据此推断因子有效。选择依据是现有普通股身份记录及覆盖可检查性，不参考回测收益。供应商 UUID 仅作为该供应商当前身份引用，不能自动证明历史生命周期。

历史诊断窗口为 2026-09-01 至 2026-09-17：检查原始价、缺口、下载时点、来源隔离与公司行为证据。不计算或宣称已通过 PIT 的历史策略收益；后来下载的价格不倒填当年可用时间。

前瞻验收从本规范和成员证据实际冻结后开始，观察随后 20 个 XNYS 交易日。起点取实际冻结时刻后的首个交易日，不回溯到文档日期。成员锁定，后续停牌、退市或缺失不能直接删掉以美化结果。20 日仅为工程验收，不是策略样本外有效性的充分证据。

首个可证伪命题是：固定成员在决策前是否具备可审计、时间一致、来源明确的数据。成功标准不是收益，而是行级时点检查、交易日覆盖、身份有效期及公司行为/退市适用性证据可逐项核查。经济 alpha 假设尚未提出，不调参、不生成 IC/Sharpe。

## 必需证据与门禁

- 保存输入原始文件版本及 SHA-256，记录读取前后是否变更；当前可变行情文件只能作为盘点输入，不能直接注册研究快照。
- 可用时刻只接受实际采集观察时间；未知字段保留未知。日线日期不等于收盘事件时间，不能凭日期补一个虚构 event_time。
- 当前身份来源：`provider-asset-reference-v1`，观察时刻 `2026-09-17T16:49:16.090962+00:00`，原始资产响应 SHA-256 `3dbf1afad1791b9f918ab99ced5445bcd18fa4460bcc8025f3bfa4848c2bd2d0`。需冻结成员及再次检查生命周期，不能仅凭 active/tradable 声称可交易。
- 公司行为端点空响应、Yahoo action=0 均不是完整无事件证明。分红、拆分、并购及退市适用性未核实前禁止总回报与正式回测。
- 日历需固定版本及逐日 session close；下一决策只能使用在该决策前已观察的数据。当前下载的旧价格可作为未来决策输入候选，但不得伪装历史 vintage；其作为训练输入的修订偏差也需明确记录。
- 数据许可/账户授权、成本、成交、账务黄金样例与独立审查均需验收；不得仅设 qualification flag 放行。

行级 PIT 检查与 manifest readiness 分开；两者通过均不自动授予 qualified。正式回测入口保持阻断，直到独立证据与受控运行器验收都通过。

## 实施记录

本轮落实只读盘点脚本与行级 PIT 检查器。全量同步是用户级 systemd 服务，不能用系统级 service 查询误判停机。

- `scripts/capture_bounded_research_inventory.py`：来源隔离冻结原始字段、输入 SHA-256 和当前身份引用；哈希变化、空盘点、缺成员、身份来源不匹配均阻断。不凭日期生成收盘 event_time，也不补写历史 available_at。
- `src/quant_data/research_row_audit.py`：UTC 时点次序、决策可用性、成员、重复日线、OHLCV 检查，空样本/非有限数/超大整数/非 UTC/无效日期 fail-closed；`qualified` 永远 false。
- 测试：盘点专项 3 passed；行级检查专项 24 passed；此前该实现与 manifest 检查组合 30 passed（后续增加两个测试，不能把两次结果累加为独立覆盖）。`git diff --check` 通过。
- 真实冻结 v3：`/home/davidou/quant/data/reference/bounded-research/20260918-five-stock-inventory-v3`，15 个来源文件、79 条诊断记录（跨来源，非 79 个唯一股票日期），其中只有 12 行存在 available_at。旧历史来源未知可用时点仍保留未知。
- 首次 v1 因未使用项目带哈希的 symbol_key，得到空盘点；该结果保留且不作为证据。已修正键路径并补空盘点拒绝测试。v2/v3 保留版本审计，生产定时脚本为 v4。
- 部署：脚本及检查器放入 `/home/davidou/quant/releases/us-research-progress-20260918-03`，不替换 API 包、不重启网页、不停止全量任务。新增用户级 `quant-bounded-research-inventory.timer` 每日 08:10 Asia/Shanghai 执行；service 实际退出 `0/SUCCESS`，每日输出目录 `reference/bounded-research/daily/<实际UTC时刻>`。它只保存数据证据，不调用模型，前瞻观察不可提前宣称完成。
- 回滚：`systemctl --user disable --now quant-bounded-research-inventory.timer` 即可停止新增留存；保留脚本和所有历史证据，不影响既有同步任务。
- 同步核验：API、全量同步 timer 均 active，后台 sync_scheduler 正在运行；盘点时目标日 2026-09-17，15161 个代码中 1004 endpoint_current、14083 needs_update、74 missing_sessions_candidate。目标已前移到新交易日，不能与此前 9 月 16 日达标数直接比较或宣称全量补齐。

下一阶段仍需真实事件时间与固定日历、生命周期/历史身份、公司行为及退市适用性证据；不能以这些诊断检查替代研究资格或真实运行器验证。20 个未来交易日的观察需实际时间经过，本轮没有收益/策略有效性结果。
