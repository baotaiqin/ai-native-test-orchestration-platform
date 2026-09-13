# V1-P5 AI Performance Analysis 主流程交付

## 交付结论

AI Performance Analysis 主流程已完成。用户可从已产生确定性指标的终态性能 Run 发起结构化分析并查看历史；平台保存来源摘要、Prompt/Schema/AI Call 审计，AI 只提供解释、待验证瓶颈假设和建议，不能覆盖原始指标或 SLA 判定。

## 已实现范围

- 服务端白名单化性能配置，移除原始趋势明细，仅保留聚合指标、确定性趋势摘要、安全错误类型、SLA 明细和平台 verdict，来源快照最大 64KB 并保存 SHA-256。
- 仅接受 `SUCCESS` / `FAILED` 且已有指标的性能 Run；无指标、中间态或跨项目访问会在模型调用前拒绝。
- 系统默认 `SYSTEM_PERFORMANCE_ANALYSIS` Prompt 绑定严格 `PerformanceAnalysisResult` Output Schema。
- 结果包含 verdict、指标发现、瓶颈假设、优化建议、置信度和人工复核标记。
- 领域校验强制 verdict 与平台 SLA 一致；finding 只能引用来源指标且 observed 必须精确相等；假设证据不得引用来源外指标。
- 结构化文本拒绝 URL、凭据模式和超长内容，复用 AI Gateway 的一次有界 Repair、Fallback、成本和调用审计。
- 分析记录关联不可变 Prompt Version、Output Schema、AI Call、实际模型、来源快照摘要及 Fallback/Repair；同 Run 防并发草稿，10 分钟遗留草稿可恢复。
- 性能页新增 AI 分析抽屉，支持选择 Prompt、填写不可信补充关注点、生成并查看历史分析。
- Demo 重置计数纳入分析记录，存在 DRAFT 分析时阻止重置，避免分析过程被清理。

## 数据与接口

- Alembic `20260913_0077` 新增 `performance_analyses`；本地开发 MySQL 已由 `0076` 非破坏性升级到 `0077 (head)`，字段、索引、唯一约束和 CHECK 已核对。
- `POST /api/v1/performance/runs/{run_id}/analyses`：同步生成并持久化一条分析。
- `GET /api/v1/performance/runs/{run_id}/analyses`：按时间倒序读取完成分析。
- 前端 AI 请求使用 180 秒长请求预算，与后端生成预算一致。

## 基本检查

- Backend 性能专项：12 passed。
- 相关 Ruff format/check：通过。
- Frontend type-check：通过。
- Frontend 临时目录 production build：通过，生成独立 `PerformanceView` chunk；默认 `dist` 因现有进程占用未清理或替换。
- OpenAPI：当前应用共 272 条 path，性能分析读写路由存在。
- 系统默认性能分析 Prompt 已在本地开发库创建并核对为启用状态。

## 明确边界

- 本轮没有调用真实模型、重启 Backend/Runner、执行浏览器交互或真实压力测试。
- 真模型结构化输出、一次 Repair、并发生成、草稿恢复和浏览器缩放复核进入《集中验收待办》。
- Prometheus 与 Scenario Flow Editor 是本轮 V1 增强能力合并的剩余开发项；按当时权重合并进度暂记 **86%**。
