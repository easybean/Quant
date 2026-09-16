# Quant

个人使用的量化研究与模拟交易项目（初始化阶段）。当前第一阶段建立可复现的美股日线数据与回测链路，不连接真实下单。

## 第一阶段数据管道

目标是下载 2016 年至今的美股日 K，并把当前上市与已退市证券都纳入证券清单。行情来自 yfinance；证券清单可从 Alpha Vantage `LISTING_STATUS` 下载，也可导入已经下载好的 CSV。

需要特别说明：退市证券的最后一根日 K 不一定代表投资者最终收到的退市收益。管道会保存缺失和失败信息，但暂时不会猜测现金收购、换股、破产或转 OTC 的最终价值；这部分需要后续的退市事件数据补全。

### 安装

服务器支持 Python 3.10 及以上版本，建议使用独立虚拟环境：

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
cp config/pipeline.example.toml config/pipeline.toml
```

`config/pipeline.toml` 和 `data/` 已加入 `.gitignore`，API 密钥不会写进配置或仓库。

### 内网量化工作台

工作台只提供三个中文页面：**回测、结果、模拟盘**。在数据清洗、复权及退市事件处理完成前，回测页面被明确锁定，只能保存策略草稿；不会用当前 raw/unadjusted 数据运行或展示正式回测。模拟盘页面不读取密钥、不下单。

```bash
.venv/bin/pip install -e '.[ui]'
export QUANT_DATA_ROOT=/home/davidou/quant/data
export QUANT_REPORTS_ROOT=/home/davidou/quant/reports
export ALPACA_CREDENTIAL_FILE=/home/davidou/alpacakey
.venv/bin/streamlit run src/quant_data/workbench.py --server.address 0.0.0.0 --server.port 8501
```

仅在内网访问端口 `8501`。报告完成后写入 `QUANT_REPORTS_ROOT`，结果页会自动列出 HTML、JSON、CSV、Parquet 或 PNG 文件。草稿保存在该目录下的 `drafts/`，与正式报告分开。

### 建立证券清单

如果已有 Alpha Vantage CSV，直接导入 active 和 delisted 两份文件：

```bash
quant-data listing import \
  data/raw/listings/active.csv \
  data/raw/listings/delisted.csv \
  --as-of 2026-08-31 \
  --output data/metadata/security_master.parquet
```

增量更新时可以合并已有主表：

```bash
quant-data listing import data/raw/listings/active-new.csv \
  --existing data/metadata/security_master.parquet \
  --as-of 2026-09-01 \
  --output data/metadata/security_master.parquet
```

也可以从接口下载。先把个人 API key 放入环境变量，不要写入命令历史或配置文件：

```bash
export ALPHAVANTAGE_API_KEY='你的个人key'
quant-data listing fetch --state active --output data/raw/listings/active.parquet
quant-data listing fetch --state delisted --output data/raw/listings/delisted.parquet
```

`fetch` 同时写 Parquet 和同名 CSV。然后按上面的 `listing import` 命令合并两个 CSV。没有 API key 时，下载外部 CSV 后直接走 `listing import` 即可。

服务器使用 Nasdaq Trader Symbol Directory 时，直接导入两份管道文本：

```bash
quant-data listing import-nasdaq-dir \
  --nasdaq-listed data/raw/listings/nasdaqlisted.txt \
  --other-listed data/raw/listings/otherlisted.txt \
  --as-of 2026-08-31 \
  --output data/metadata/security_master.parquet
```

该命令过滤 `Test Issue=Y` 和文件尾部创建时间行，映射主要美股交易所，按 `ETF` 字段区分 `ETF` 与 `Stock`，并保留 `raw_symbol`、来源和快照日期。这份目录只代表当前证券，不包含历史退市证券；后续仍需与退市清单增量合并。

跨源合并采用生命周期规则：当前 `active` 记录按 `symbol` 合并为一条，`sources` 和 JSON `provenance` 保留所有来源；`delisted` 按 `symbol + ipo_date + delisting_date` 保留独立生命周期，因此不会与当前 active 公司混为一条，也不会误删代码复用产生的历史公司。重新执行同一增量导入是幂等的。

已有的重复主表可以先输出到新文件检查，不覆盖正在使用的旧文件：

```bash
quant-data listing consolidate \
  --input data/metadata/security_master.parquet \
  --output data/metadata/security_master.consolidated.parquet
```

命令会同时生成 `security_master.consolidated.csv`。核对行数、active 唯一性和 provenance 后，再由运维切换配置指向新文件。

### 下载日线

先用极小样本验证路径和网络：

```bash
quant-data download --config config/pipeline.toml --limit 5
```

确认后启动全部清单：

```bash
quant-data download --config config/pipeline.toml
```

如果服务器出口被 Yahoo 限流（常见为 HTTP 403/429），可以显式改用 Nasdaq 网站的当前股票日线接口：

```bash
quant-data download --config config/pipeline.toml --provider nasdaq
```

Nasdaq fallback 只提供当前接口仍能识别的证券，且是**未复权 OHLCV**，没有分红和拆股事件。管道会在 Parquet、run metadata 和 manifest 中写入 `source=nasdaq_web_unadjusted`、`adjustment_status=unadjusted` 和 `actions_status=not_available`；它适合今天先启动 active universe，不等价于完整的退市与复权数据集。Nasdaq 查不到的退市代码会进入 `failures.csv`，不能当成零收益或有效退市价格。

Alpaca Stock Historical Data v2 可用于补拉 Nasdaq 失败清单。免费账户通常使用 `iex`，只有订阅授权后才选择 `sip`：

```bash
export APCA_API_KEY_ID='...'
export APCA_API_SECRET_KEY='...'
quant-data download --config config/pipeline.toml --provider alpaca --feed iex \
  --retry-failures-from nasdaq
```

也可以使用 `--alpaca-credential-file /绝对路径/credentials.env`。文件支持标准的 `APCA_API_KEY_ID=...` 与 `APCA_API_SECRET_KEY=...`，或依次两行 key id、secret；单行值会被拒绝，因为无法组成凭证对。文件路径和凭证内容都不会写入 manifest 或日志。Alpaca 数据按 `1Day`、`adjustment=raw` 分页请求，保存 `source=alpaca_stock_historical_v2`、所选 feed 和 `adjustment_status=raw`。同一 Alpaca 日期范围已成功的证券会由既有断点机制跳过。

状态、资产类型和失败来源过滤可以组合。例如只用 Alpaca 补拉 Nasdaq 失败清单中的退市普通股：

```bash
quant-data download --config config/pipeline.toml \
  --security-master data/metadata/security_master.consolidated.parquet \
  --provider alpaca --feed iex \
  --retry-failures-from nasdaq \
  --status delisted --asset-type Stock
```

`--status` 和 `--asset-type` 都可重复传入。显式选择 active 时，如果主表仍有重复 active symbol，程序会要求先运行 `listing consolidate`。退市主表允许同一 symbol 存在多个生命周期：每个生命周期都会保存到本次运行的 `manifests/universe-*.parquet` 审计快照，但网络下载按 symbol 去重一次；相应的生命周期行数、唯一代码数和折叠数写入 run metadata。`--retry-failures-from` 与状态、资产类型过滤取交集，不会扩张下载范围。

并发运行时必须给每个实例指定不同的 state namespace：

```bash
quant-data download --config config/pipeline.toml \
  --provider alpaca --feed iex --state-namespace alpaca-retry \
  --retry-failures-from nasdaq

quant-data download --config config/pipeline.toml \
  --provider alpaca --feed iex --state-namespace alpaca-delisted \
  --status delisted
```

这样两组 `download.jsonl`、`latest.csv`、`failures.csv`、run JSON 和 universe Parquet 分别位于 `data/manifests/alpaca-retry/` 与 `data/manifests/alpaca-delisted/`。行情也按 `provider=<provider>/namespace=<namespace>/symbol=...` 隔离，不会与仍在运行的 Nasdaq 或另一个 Alpaca 实例覆盖同一 Parquet。

`--retry-failures-from nasdaq` 默认读取兼容旧服务的 `data/manifests/download.jsonl`。如果 Nasdaq 已经使用命名空间，例如 `nasdaq-main`，再加 `--retry-failures-state-namespace nasdaq-main`，就会显式读取 `data/manifests/nasdaq-main/download.jsonl`。凭证、凭证路径仍不会进入任何 state 文件。

程序默认逐只下载、每 100 只为一批、每只间隔 0.5 秒、失败重试 3 次并指数退避。Nasdaq 明确返回 `400 / Symbol not exists` 时会立即记录为永久失败，不再浪费重试；后续断点续传也会跳过已经成功或永久失败的证券。每只证券完成、失败或跳过后，程序会向标准输出写一行 JSON 并立即刷新，便于 systemd 日志持续显示进度。`--force` 会强制重新获取并按日期去重合并。点号形式的美股类别代码会自动映射为 Yahoo 使用的连字符形式，同时在 manifest 保留原代码和供应商代码。

输出布局：

```text
data/
  metadata/security_master.parquet  # 活跃和退市证券主表
  bars/daily/symbol=.../bars.parquet
  manifests/download.jsonl          # 只追加的完整下载审计记录
  manifests/latest.csv              # 每个代码的最新状态
  manifests/failures.csv            # 当前仍失败的代码
  manifests/run-*.json               # 每次运行参数
```

日线字段为 `date, symbol, open, high, low, close, adj_close, volume, dividends, stock_splits`。yfinance 的 `end` 是排他的，CLI 已自动加一天，因此配置中的 `end` 按包含当天理解。

### 独立补充 SPY / QQQ 基准 ETF 日线

SPY 和 QQQ 是**可交易 ETF 基准**，不是 S&P 500 或 Nasdaq-100 指数本身；此命令不会下载或声称提供历史成分股名单。为避免与已下载的股票池混淆，标的和日期范围必须显式给出。Alpaca 只允许 `SPY`、`QQQ`，写入 `adjustment=raw` 数据；其分红/拆股事件不可用，不能把 `close` 当作复权总回报序列。

Nasdaq 公开历史接口还允许一个经验证的、明确不同的指数标的：`NDX`（Nasdaq-100 指数价格）。安全映射固定为 `SPY -> etf`、`QQQ -> etf`、`NDX -> index`，其他 Nasdaq 标的会被拒绝；不会把 ETF 当作普通股票、也不会把指数当作可交易证券。该来源的数据为**未经复权**、**没有公司行为事件**。NDX 返回的 `volume=--` 会保留为缺失值，绝不填成 0。`SPX` 不在可用映射中，因此命令会拒绝它而不是假称取得了 S&P 500 指数价格。

凭证可只通过环境变量或权限受限的凭证文件提供。凭证值、凭证文件路径都不会写入输出、运行清单或日志：

```bash
quant-data benchmark download \
  --data-root /home/davidou/quant/data \
  --symbols SPY QQQ \
  --start 2016-01-01 --end 2026-08-31 \
  --provider alpaca --feed iex \
  --alpaca-credential-file /home/davidou/alpacakey
```

无需凭证的 Nasdaq 公开基准数据（ETF 及 NDX 指数价格分别使用其固定 assetclass）：

```bash
quant-data benchmark download \
  --data-root /home/davidou/quant/data \
  --symbols SPY QQQ NDX \
  --start 2016-01-01 --end 2026-09-01 \
  --provider nasdaq
```

输出严格与原始股票池隔离：

```text
data/bars/daily/provider=alpaca/namespace=benchmarks/symbol=SPY-.../bars.parquet
data/bars/daily/provider=alpaca/namespace=benchmarks/symbol=QQQ-.../bars.parquet
data/manifests/benchmarks/provider=alpaca/run-<run-id>.json
data/manifests/benchmarks/provider=alpaca/download.jsonl
```

每一根 bar 带 `source`、`feed`、`adjustment_status`、`actions_status` 与 `collected_at`；每次运行另存请求标的、日期范围、数据源和不含凭证的运行参数。重复相同 `provider + symbol + 日期范围` 会安全跳过；加 `--force` 才会重新采集并以新采集值覆盖相同交易日的**基准命名空间副本**，不会修改现有 `data/bars` 中的股票数据。

### 历史成分股名单（独立参考数据）

SPY、QQQ、NDX 的价格不包含历史成分。为构建点时（point-in-time）选股池，项目可单独导入 [thuningxu/sp500nq100](https://github.com/thuningxu/sp500nq100) 的 S&P 500 和 Nasdaq-100 快照 CSV：其 S&P 500 历史从 1996 年开始、Nasdaq-100 从 2007 年开始。它是基于公开变更表重建的**社区研究数据**，不是 S&P Dow Jones Indices 或 Nasdaq 的官方/授权成分股数据；上游也明确列出早期 Nasdaq-100 的已知缺口。因此它可以用于当前免费的研究基线，不能被标作“官方完整 PIT 成分股”。

下列命令先把 `main` 解析为一个不可变 Git commit SHA，再下载该 commit 的两份 CSV；不会触碰 `data/bars`、证券主表或已有原始行情：

```bash
quant-data constituents fetch \
  --data-root /home/davidou/quant/data \
  --revision main
```

输出只有新建的独立版本目录；为了让同一个回测可以复现，目录存在时命令会拒绝覆盖。原始 CSV、每个文件的 SHA-256、上游仓库与 commit SHA 都在 `source-manifest.json` 中。`membership_intervals.parquet` 使用半开区间：`effective_date` 当天起纳入，`end_date_exclusive` 当天起移出；空结束日期仅表示“截至该来源的最后快照仍在成分中”，不是无限期事实。

```text
data/reference/constituents-v1/
  raw/sp500_components_history.csv
  raw/nasdaq100_components_history.csv
  membership_intervals.parquet
  source-manifest.json
```

如果服务器已用其他方式下载好了**同一个 commit**的两份 CSV，可离线导入；`--source-revision` 必须是完整的 40 位 commit SHA，不能用不稳定的 `main` 标签：

```bash
quant-data constituents import \
  --data-root /home/davidou/quant/data \
  --sp500-csv /path/sp500_components_history.csv \
  --nasdaq100-csv /path/nasdaq100_components_history.csv \
  --source-revision <40位commit-sha>
```

### 原始日线的版本化结构清洗

下载完成后，先执行**结构清洗**。它只读取 `data/bars/daily` 下的原始 Parquet，绝不修改、覆盖或删除原始文件；每个 `provider/namespace/symbol` 文件独立处理，不会拼接不同供应商的收益序列。输出必须是一个与原始目录分离的版本化派生目录，例如 `data/derived/v1`。

先做只读预检（无 `--output` 时默认 dry-run）：

```bash
quant-data clean --raw-root data/bars/daily --dry-run \
  --audit-output data/audit/structural-v1-preview
```

确认预检后写入派生数据和可机器读取的审计报告：

```bash
quant-data clean --raw-root data/bars/daily \
  --output data/derived/v1 \
  --audit-output data/audit/structural-v1
```

清洗过程稳定排序日期，并对同一原始文件内的重复日期采用 `keep=last`（原始输入顺序）的透明策略；无效日期会被丢弃并写入 anomaly。OHLC 关系、负价格/成交量、缺失值和缺列都会在报告中保留。每份派生文件保留/补足 `source`、`feed`、`adjustment_status`、`actions_status`、分红和拆股字段，并增加原文件相对路径、SHA-256 与 `cleaning_status`。

审计目录包含 `quality-summary.json`、`run-metadata.json`、`coverage.parquet` 与 `anomalies.jsonl`。该阶段明确**没有**计算复权价格，也**没有**处理退市收益、并购换股或 OTC 结局；在这两项完成前，派生数据仍不能作为严谨收益回测的最终输入。

## 当前选型结论

- **Qlib**：优先作为因子、机器学习、组合研究与模型实验层候选，不把它当作完整交易平台。
- **LEAN**：优先作为回测、事件驱动策略和模拟撮合引擎候选；是否采用取决于目标市场、行情供应商及券商接口的 PoC。
- **平台能力**：行情接入、数据质量、账户与风控、任务调度、监控告警、权限审计和管理界面仍需自行建设或集成。

详细分析见 [docs/0001-qlib-vs-lean.md](docs/0001-qlib-vs-lean.md)，需求确认清单见 [docs/requirements.md](docs/requirements.md)。

## 建议架构

```text
授权行情源 -> 采集/标准化 -> 历史库 + 实时总线
                         |             |
                         v             v
                    Qlib 研究层 -> 信号/模型注册
                                      |
                                      v
                         LEAN 或自研执行适配层
                                      |
                                      v
                         模拟券商/撮合 -> 风控/账户
                                      |
                                      v
                              API / 监控 / 审计
```

核心原则：研究信号和交易执行通过稳定的数据契约解耦；同一份策略配置、费用模型和交易规则应尽量贯穿回测与模拟盘。

## 因子实验室目录口径

因子实验室按测量对象提供工程导航：收益与动量、趋势与均线、价格位置与通道、震荡与强弱、波动与风险、成交量、量价关系、K线结构、统计与回归。它不是唯一的学术分类；相关指标不等于重复，也不构成交易建议。目录在没有行情文件时仍可浏览，运行检验时才只读 `derived/structural-v1`。

Alpha360 是联合机器学习输入，故不在单因子目录中展示。Alpha158 保留稳定的本地兼容 ID，但本地实现（9 个 K-bar、25 个价格滞后、5 个成交量滞后、119 个滚动特征）不应表述为严格官方 Alpha158；详情页以本地 `_qlib_series` 的实际公式为准。VWAP 缺失时保持不可用，绝不以其他价格代理。

## 实施顺序

1. 明确市场、品种、频率、行情供应商、并发策略数和撮合精度。
2. 用一个简单策略完成“历史回放 -> 实时行情 -> 模拟成交 -> 持仓/盈亏 -> 重启恢复”的纵向 PoC。
3. 针对目标市场验证交易日历、复权、停牌、涨跌停、手续费、滑点和成交规则。
4. PoC 通过后，再建设多租户、权限、调度、监控和 Web 管理面。

## 仓库状态

当前只建立了选型与需求基线，尚未锁定技术栈或引入 Qlib/LEAN 源码，避免在行情与市场范围未确定前形成错误耦合。
