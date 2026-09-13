# V1-P4 CI/CD API 与 Webhook 主流程交付

## 交付结论

CI/CD 与通用 Webhook 主流程已完成：外部流水线可使用最小权限 CI Token 幂等触发既有 Test Plan，查询本次 Plan Run 聚合状态；测试终态进入持久化 Outbox 后，由后台协调器完成可签名、可重试、可追溯的 HTTPS JSON 投递。

## 已实现范围

- 项目 Owner 可创建、查看元数据和撤销 CI Token；明文只展示一次，服务端只保存 HMAC 摘要和安全前缀。
- Token 支持 1～365 天有效期及最多 50 个 Test Plan 白名单；它不是用户会话，不能访问普通项目 API。
- 外部触发强制 Bearer Token 与 `Idempotency-Key`，保存流水线引用、Commit SHA 和独立调用账本；同 Token 同键只返回原 Plan Run。
- CI 状态接口只返回由该项目 CI 调用成功启动且仍在 Token 计划范围内的 Test Plan Run。
- 通用 Webhook 只接受无内嵌凭据、无 fragment 的 HTTPS URL；URL 和可选签名密钥加密保存，列表仅显示主机/端口提示。
- Test Plan 聚合终态生成唯一 Outbox 事件；后台扫描主动刷新 Plan Run，不依赖前端或 CI 轮询。
- 投递提供 Event、Delivery、Timestamp 头；配置密钥时以 `timestamp.canonical_json` 计算 HMAC-SHA256。
- 2xx 成功；429、5xx、网络与超时按 30 秒起步指数退避，最多由端点配置尝试 1～5 次；最终失败可从页面人工重试。
- `SENDING` 记录使用 90 秒租约恢复，避免进程中断后永久卡住；Outbox 唯一约束避免同端点同事件重复入队。
- 生产发送器不读取系统代理、不跟随重定向，并在请求前解析 DNS、拒绝任一非公网 IP。
- 项目工作区新增“CI/CD 集成”页面，包含 Token、API 使用提示、Webhook、投递历史与恢复入口。

## 数据与运行

- Alembic `20260913_0075` 新增 `ci_access_tokens`、`ci_run_invocations`、`webhook_endpoints`、`webhook_deliveries`。
- 本地开发 MySQL 已从 `0074` 非破坏性升级到 CI/CD 迁移 `0075`，等待期间另一独立 Web MCP 包继续升级到当前总 head `0076`；四张表及 `last_attempt_at` 恢复字段已核对存在。
- FastAPI lifespan 启停独立 Webhook 协调器；可用 `APP_WEBHOOK_SCAN_ENABLED` 和 `APP_WEBHOOK_SCAN_INTERVAL_SECONDS` 控制。
- Backend 正式依赖新增 `httpx`，用于受控 HTTPS 投递。

## 基本检查

- CI/CD、Webhook、Test Plan 与 Scheduler 聚焦组合：17 passed。
- 相关 Ruff lint：通过；CI/CD 新文件 Ruff format：通过。
- Frontend type-check：通过。
- OpenAPI：当前总计 269 条 path，其中 CI/CD 与外部 CI 相关 9 条，关键路由均存在。
- Frontend 隔离目录 production build：通过，并生成独立 `CiCdView` chunk；临时构建目录已安全清理。

## 明确边界

- 本轮没有调用真实 CI 平台或公网 Webhook；外部联调、浏览器缩放和双实例竞争验证进入《集中验收待办》。
- 当前 DNS 安全检查在连接前完成，已拒绝私网和重定向；更强的 DNS rebinding 绑定与企业代理策略属于后续安全加固。
- 本包不实现 Email/站内通知，也不允许 Webhook 回写测试状态。
- AI Performance Analysis、Prometheus 与 Scenario Flow Editor 是本轮 V1 增强能力合并的剩余开发项。
