# V1-API4 响应提取与 Post Action 交付

状态：已实现，基本检查通过，待集中验收；完整 API 流水线仍未完成。

- Backend 将版本锁定的 Response Extractor 与受支持 Post Action 纳入授权 Runner 执行计划，并继续校验项目、环境、敏感默认值和 Runtime 引用。
- Runner 在收到响应后先执行 JSONPath、Header、Cookie Extractor，再按顺序执行 `SET_VARIABLE` 与 `EXTRACT_RESPONSE` Post Action；必填缺失失败，可选缺失复制默认值。
- API 与 Scenario 复用同一组有界 JSONPath/大小写不敏感 Header/Cookie 提取函数。提取值只存在隔离执行 Context，不写入计划、completion、日志或 Evidence。
- 流水线 Context 保持 64KB 上限。为避免消费层重试重复执行 Pre/Extractor/Post，任何 Runtime 流水线与请求重试组合当前均由 Backend 失败关闭。

基本检查：Backend 计划/门禁/旧协议定向 4 项通过；Runner API pipeline、协议、Consumer 及 Scenario 兼容定向 62 项通过；相关 Ruff 通过。未运行全量、真实目标或报告验收。

后续仍需：动作级持久 Trace、SQL/Script/Token/Resource Action、Cleanup 与资源登记、数据驱动、AI/401、Multipart 和报告扩展。
