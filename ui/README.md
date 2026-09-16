# 量化工作台前端原型

一个本地运行的 React + TypeScript + Vite 工作台壳，专注于研究和模拟环境的产品交互。

## 本地启动

```bash
npm install
npm run dev
```

## 构建

```bash
npm run build
```

## 当前边界

- 因子百科只读请求 `GET /api/v1/factors`，展示本地真实因子注册表；不加载行情、不计算因子、不创建任务、订单或交易。
- 总览中的数字、图表和实验记录均明确标记为演示数据。
- 因子目录中的“可计算”只表示当前存在计算定义，不表示预测能力或收益已经验证。

## 目录 API 配置

默认 API 地址是 `http://192.168.1.132:8511`。部署时用 `VITE_API_BASE_URL` 配置到 API 服务根地址，例如：

```bash
VITE_API_BASE_URL=http://192.168.1.132:8511 npm run build
```

开发服务器可选通过 `VITE_DEV_API_PROXY` 将 `/api` 代理到本机 API；生产构建仍使用明确的 `VITE_API_BASE_URL`。后端可选依赖通过 `pip install -e '.[api]'` 安装，允许的浏览器来源通过逗号分隔的 `QUANT_UI_ORIGINS` 配置；未配置时仅允许 `http://127.0.0.1:5173` 和 `http://192.168.1.132:8510`。

## 后续 FastAPI 接口边界

前端未来只通过应用 API 读取和提交受控对象：`/api/catalog/factors`、`/api/experiments`、`/api/data-health`、`/api/backtests` 和 `/api/runs`。提交回测或模拟实例前应由服务端完成能力与风险检查；浏览器不直接访问数据文件、凭证或交易连接。
