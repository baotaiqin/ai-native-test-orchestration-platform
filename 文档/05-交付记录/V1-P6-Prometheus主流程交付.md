# V1 P6 Prometheus 主流程交付

> 交付日期：2026-09-13  
> 状态：已实现、基本检查通过、待集中验收  
> 数据库迁移头：`20260913_0077`（本包无新迁移）

## 1. 交付范围

- Backend 提供可通过 `APP_METRICS_ENABLED` 关闭的 `/metrics`；端点不进入 OpenAPI。
- HTTP 指标覆盖请求量、延迟直方图和在途量。标签只使用方法、FastAPI 规范化路由模板和状态码；未知路径统一记为 `unmatched`。
- 资源指标覆盖进程 CPU 累计时间、进程 RSS、整机 CPU/内存利用率以及 1/5/15 分钟 Load，Windows 与 Linux 使用相同指标名。
- 业务指标覆盖 Run 创建、性能 Run 完成与 SLA verdict、AI 调用状态/Fallback/Repair、输入输出 Token、估算成本和 Webhook 投递状态。
- 开发 Compose 抓取宿主机 Backend；服务器 Compose 在内部网络抓取 Backend。两者均持久化时序数据并设置有界保留期，Prometheus 默认只绑定本机地址。

## 2. 安全与基数边界

- 指标标签禁止项目 ID、用户 ID、Run ID、请求 URL 参数、模型返回文本、错误正文和任何凭据。
- 未匹配路由不使用原始 Path，避免攻击者制造无限标签基数。
- 服务器镜像必须通过 `PROMETHEUS_IMAGE` 显式提供已审查版本；示例只给出配置位，不携带 Secret。
- Prometheus 抓取失败不改变业务请求结果；本包未新增指标写库或数据库迁移。

## 3. 配置

开发环境默认使用 `prom/prometheus:v2.55.1`、`127.0.0.1:9090` 和 7 天保留期。服务器环境支持：

- `PROMETHEUS_IMAGE`：必填的显式版本镜像；
- `PROMETHEUS_BIND_ADDRESS`：默认 `127.0.0.1`；
- `PROMETHEUS_PORT`：默认 `9090`；
- `PROMETHEUS_RETENTION`：默认 `15d`。

## 4. 基本检查证据

- Ruff format/check：通过。
- 指标、健康、AI Gateway、性能与 CI/CD/Webhook 组合回归：`35 passed`。
- Run 创建关键路径：`1 passed`。
- Windows 本地实际生成进程 CPU/RSS、整机 CPU/内存与三档 Load 指标：通过。
- `docker compose -f deploy/docker-compose.dev.yml config`：通过。
- `docker compose -f deploy/server/compose.yml config --no-interpolate`：通过。
- OpenAPI 路径数仍为 272，`/metrics` 未进入 Schema。

## 5. 待集中验收

本轮未拉取或启动 Prometheus、未重启 Backend。真实容器 target、Linux 指标、抓取中断恢复、持久化与保留期、生产入口暴露、实际基数和告警/面板均按《集中验收待办》执行。

## 6. 完成度影响

Prometheus 主流程记为完成；本轮 V1 增强能力合并进度暂由 86% 提升为 93%。剩余开发范围为 Scenario Flow Editor。
