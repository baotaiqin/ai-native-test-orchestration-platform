# V1-API1 正式认证请求计划交付

状态：已实现，基本检查通过，待集中验收；不等于完整 API 执行完成。

- 正式 `API_CASE` 已接入 R1 Runner 的 NONE、Bearer、Basic、API Key（Header/Query）能力，以及显式 Header、Query、Cookie。
- 凭据和敏感请求值在 Case Version 中必须保存为 `{{secret.name}}` 引用。Backend 在创建 Run 前校验 Secret 的项目、环境和启用状态，只在已认证 Runner 的执行计划响应中解密；执行请求模型的 `repr` 隐藏 URL、认证值、请求值和 Body。
- 明文认证、普通 Cookie 值、敏感 Header/Query 明文、缺失/跨环境 Secret、认证与显式请求项同名冲突继续拒绝。URL、Body 和非 Secret 的 Runtime 模板仍保持未开放，避免把预览解析误当现场执行。

基本检查：Backend 相关 5 项通过；Runner R1 认证/重定向专项 23 项通过；修改文件 Ruff 通过。

后续仍需：Runner 现场 Pre→Request→Extractor→Post→Assertions→Cleanup、有界失败清理；普通 Runtime Context；逐行数据驱动；AI 断言与最多一次 401 刷新；Multipart、前端与报告完整接入。
