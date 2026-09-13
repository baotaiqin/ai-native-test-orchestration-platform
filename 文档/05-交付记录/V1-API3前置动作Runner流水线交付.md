# V1-API3 前置动作 Runner 流水线交付

状态：已实现，基本检查通过，待集中验收；完整 API 流水线仍未完成。

- Backend 执行计划可携带非敏感初始 Context 与版本锁定的 `SET_VARIABLE`、`FAKER` Pre Action。存在 Pre Action 时，请求模板保持到 Runner，不在服务端提前物化。
- Runner 在 API 隔离子进程内按动作顺序更新 Context，再渲染 URL、非敏感 Header/Query 与 Body，并复用既有 HTTP Executor 执行认证、重定向、超时和响应采集。
- 协议限制动作字段、变量名、Faker 类型与 64KB Context；未知动作、敏感载荷、未定义或畸形模板失败关闭。
- 当前请求重试会由消费层按尝试调用执行单元。为避免每次尝试重复运行动作，本阶段在 Backend 明确拒绝 Runtime 流水线与请求重试组合，待流水线生命周期拆分后再开放。

基本检查：Backend 定向 7 项通过；Runner 流水线/协议 3 项、Consumer 集成与旧请求/重试兼容 5 项通过；相关 Ruff 通过。未运行全量、真实目标、正式环境或报告验收。

后续进展：Response Extractor 与两类确定性 Post Action 已由 API4 接入。仍需 Cleanup/资源登记、SQL/Script/Token 动作、数据驱动与逐行审计、AI 断言、401 刷新、Multipart 和报告扩展。
