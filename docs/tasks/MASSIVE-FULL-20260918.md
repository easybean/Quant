# Massive 全码验证、历史缺口补齐与主源切换

## 2026-09-18最终本轮部署验收（任务继续后台执行）

真实current-reference抓取13211个代码，467个官方CQS/NASDAQ别名与精确MIC唯一匹配。严格重新核对原始名单及reference/hash；不是模糊名称匹配或历史身份证明。只允许最近31日的当前采集辅助，禁止套用旧历史；保留unknown identity/research_qualified=false。未采用“无数据即退市”。

07:33:57Z别名发布465证券、3761股票日，0失败；417个别名在9/17 grouped真实返回中，另有近期返回但目标日未返回的证券。不保证467都在目标日有成交。07:35:18Z逐码统计：总15161，tested12577，remaining2584；grouped_returned12139、aliased_grouped_returned417、single returned10、empty_unknown11，目标日返回12556。旧exact-query结果保留追加日志，新官方别名重新测试，不删失败证据。

07:35Z冻结历史计划已处理12/501日，剩489，精确原代码新增79股票日、未返回13217、发布失败0；别名3761另列，不相加成独立证券数。历史起点未知975、免费外未评估9917和其他供应商无返回仍保留未完成，TODO不打完成勾。

每日主源Massive已部署（10/11/12点）；current-reference/别名审计09点自动刷新。两个批任务机械续跑，无模型heartbeat；历史timer每批3日，逐码每批60、请求持久间隔16秒且共享network锁。已有原历史和免费外备用采集保留，不购买套餐。新别名namespace独立，价格冲突阻断不覆盖，固定输入SHA/真实observed时间/不可变publication manifest；成功重跑可修复索引。

发布release `/home/davidou/quant/releases/massive-alias-20260918-01`，备份 `/home/davidou/quant/backups/massive-alias-20260918-01`；部署失败恢复原Python/units/UI，旧raw保留。API健康通过；四个Massive timers启用。UI typecheck与bundled Node22生产build通过（保留chunk大小警告；不升级系统Node）。真实浏览器ZTS及ABR$D单证券、9/17最新、日周月与无pageerror通过。集中63项专项通过，8个既有FastAPI弃用警告；后来新增future-session/evidence保护测试另测。见验收脚本 `docs/acceptance/2026-09-18-massive/verify.cjs`。

用户授权：补齐库内每只美股已有行情至当前的缺口，逐代码测试Massive，以Massive作为之后主源。不购买套餐、不交易、不丢弃旧历史或静默推断退市。

## 已验证

既有清单15161；9月17日真实grouped响应精确匹配12139，逐匹配代码已有真实返回证据和独立落盘。未返回3022个代码不能直接说供应商失败。逐码single-range GET验证器已部署并实际取得HTTP200；截至07:05Z，grouped_returned12139、single returned2、empty_unknown4、剩余3016。单股窗口为目标日前30天，空仅代表该窗口无返回，不代表全历史或退市。

代码测试队列固定目标9月17日，按每批60个、结束3分钟后续跑，保存每个代码原始响应、SHA、HTTP、观察时间和结果。代码添加不会使旧同码同目标证据失效。所有Massive任务共享网络锁和持久16秒间隔（留出免费限流余量），不创建模型heartbeat。首次单股结果AACB/AACBR各2行末日8月19日，证明接口可用但未返回9月17日；不伪造最新bar。

浏览器原始历史base保留，base末日之后同日可用新行情优先Massive；其他源暂作历史缺口备用。新增重叠测试证明原历史不被替换而新增日期Massive优先。

## 补数计划和边界

`massive_backfill.py` 读取实际交易日、冻结按日缺口清单并通过grouped请求批量补齐；限定发布计划成员避免全表重复写入，不覆盖日更完成指针/摘要。每个symbol/session均有返回、缺失或失败报告。免费两年外保留未覆盖报告；旧身份、公司行为、PIT和退市门禁不变。

尚未宣称全量补齐/全部代码可返回。execution status complete只能表示冻结计划的查询处理结束；coverage_status只要有missing/failed/未知历史起点/免费外未评估就保持unresolved。缺失不能被状态complete或timer active掩盖。

真实冻结计划 `1781a4ac9fc4db3c137f90bcb00eb672794dfc0ddcc0ecc60c7dd6d7952f055a`：15161代码、501交易日、399871股票日缺口，读取错误0，免费边界2024-09-18，目标2026-09-17。975代码无本地历史起点，不假造上市前缺口；9917代码有免费范围外未评估记录，此数不是9917个已证明缺口，也不否定旧历史已存在。

07:19:03Z首批9/17、9/16、9/15执行完成：HTTP新请求2（9/17复用）、returned并发布16股票日、missing3666、failed0；pending498。systemd Result=success、ExecMainStatus=0，historical timer已启用，后续复用函数真实返回reuse=True，不重建/回放首批。07:19:05Z覆盖末日当前12694、待更新2467；这个总变化同时包含旧SIP队列推进和交易日校验修复，不能全部归因于16条Massive回补。

周末桥接修复：下一必需日期取XNYS下一个交易日，而不是calendar day+1，避免8/28后将8/29周末当作必需价格。coverage仍只是末日/请求窗口指标，不替代实际股票日审计。

## 部署与证据

逐码发布目录 `/home/davidou/quant/releases/massive-audit-20260918-01`，旧浏览器/adapter/daily unit备份 `/home/davidou/quant/backups/massive-audit-20260918-01`。逐码service已真实运行，timer已启用，原完整历史队列保留。API健康通过。

专项：浏览器+probe13 passed（含新增主源重叠测试）；adapter+probe11 passed（集合重叠，不累加）；共享限流2 passed；状态7 passed。既有FastAPI弃用警告保留。

后续最终影响范围：probe4+状态7=11 passed；coverage3 passed；backfill最后进度落盘修改专项9 passed；backfill/publish/daily集中自测21 passed；reference5 mock、alias audit3 mock分别通过，未累加重叠集合。生产UI回归（ZTS9/17、单证券、日周月、无pageerror）通过。

历史回补目录 `/home/davidou/quant/releases/massive-backfill-20260918-01`；旧publish/probe/rate/status/coverage备份 `/home/davidou/quant/backups/massive-backfill-20260918-01`。服务器逐码完成或三次同源失败后停本次测试timer；历史计划处理结束后停本次历史timer，日更timer独立保留。任何空数据/未确认历史身份不解除研究和实盘门禁。
