# 免费美股日线源接入与 SIP 故障隔离

2026-09-18：用户批准验证 Massive 免费全市场日线，并修复既有同步故障。全量采集范围仍为 15161 个代码；本轮验收小批量不替代全量目标。

## 已修复与真实验收

Alpaca 队列中 5 个不符合本地 SIP 请求格式的代码导致批量下载器在首次 HTTP 前抛出 ValueError，整阶段失败并进入一小时冷却。实际代码为 BC/PB、BC/PC、CAPTW(EXP20260807)、NXT(EXP20091224)、NYCB- PR-U。

`sip_batch.py` 保留日期、重复请求与映射一对一约束，单独隔离不合法请求格式；不猜测代码别名、不删除证券或缺口。`recovery_sync.py` 明确记录 `invalid_sip_symbol_format` 和 `http_response_received=false`，不因此熔断供应商。

专项组合首次 42 passed；新增“连续三个本地失败仍继续正常证券”的回归后，恢复模块 20 passed。两次集合重叠，不累加为独立测试覆盖。部署前隔离 compile/import、健康检查通过；`git diff --check` 通过。

真实验收：8 个任务，一次 SIP 批量 HTTP、3 个成功（TSLA/MSFT/SUPX）、5 个本地隔离、0 个 deferred、circuit_open=false。三只股票原始价独立文件末日均到 2026-09-17，网页消费的 search API 返回 TSLA last_date=2026-09-17。该批次 status=failed 是保留 5 个未解决代码，不表示正常证券未采集；不能把隔离当删除缺口或全量成功。覆盖统计随既有刷新任务更新，瞬间旧统计不等于文件未落盘。

部署包切换：`/home/davidou/quant/releases/us-sync-isolation-20260918-01`；完整备份及原包：`/home/davidou/quant/backups/us-sync-isolation-20260918-01`。脚本 `scripts/deploy_sync_isolation_20260918.sh` 无论成功或失败均恢复 API、同步 timer 和全量任务；失败恢复完整原包。只短暂停止服务，不动原始行情/队列/审计记录。

部署后原 ValueError 一小时冷却尚未到期时，全量 scheduler 会继续 Yahoo 等源；本地故障修复不绕过 HTTP 429/授权等供应商限制。验收脚本使用独立记录的有界真实请求，不缩减后台全量计划。

## Massive：接入与验证边界

官方 [Daily Market Summary](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary) 明确免费 Stocks Basic 可用，一次按日查询全市场 OHLCV，近两年历史；[套餐](https://www.massive.com/stocks) 为 5 请求/分钟。文档能力不等于本项目账户已实测。

新 `massive_daily.py` 仅请求固定 HTTPS grouped endpoint，Bearer 凭证只进入 header；明确 adjusted=false/include_otc=false，无重试，无券商订单/账户接口。不改变现有浏览器优先级、原价文件或研究门禁。返回原始响应、诊断、有效行及校验和存入独立不可变 `reference/massive-daily-v1/<UTC-uuid>`。

必须先核验 9 月 17 日响应权限、实际股票数量、缺失/拒绝行、TSLA/SUPX 与日历时间对齐及原始价口径。不同源对盘前盘后、成交筛选及成交量定义可能不同，不能通过强制混源“抹平”差异。OTC 不在默认请求内，不能把该接口响应当成15161代码必然全部覆盖。

目前未发现明确命名的 Massive/Polygon 凭证文件，已经请用户将免费 API key 存入 `/home/davidou/massivekey`（权限600，不进聊天/仓库）。没有 key 时不创建演示全市场结果，不启用 Massive 每日源、不宣称真实下载通过。

新源接入专项 8 passed（仅 mock 传输合同测试，不代表供应商实测）、py_compile 通过。集中 Review 修正超大整数、重复代码所有行隔离、URL 小写 false 及派生/诊断文件哈希血缘。完整包暂存 `/home/davidou/quant/releases/massive-daily-validation-20260918-01/quant_data`，服务器实际 CLI --help 成功；没有替换线上包。

凭证准备后的真实验证命令：

```sh
PYTHONPATH=/home/davidou/quant/releases/massive-daily-validation-20260918-01 /home/davidou/quant/venv/bin/python -m quant_data.massive_daily --date 2026-09-17 --data-root /home/davidou/quant/data --credential-file /home/davidou/massivekey
```

验证通过后才推进独立全市场每日源及浏览器索引消费；近两年外历史仍由既有源接手。数据许可仅按用户自用进行，免费计划不等于允许再分发；不上传用户凭证、不购买套餐。
