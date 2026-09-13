# 安全策略

## 报告安全问题

请通过仓库平台提供的私密漏洞报告或 Security Advisory 功能联系维护者。不要在公开 Issue、讨论、PR、截图或日志中披露可利用细节、真实凭据、Token、Session、数据库连接串或业务数据。

报告中建议包含受影响版本/模块、复现前置条件、最小复现步骤、影响评估及可行的缓解建议。提交复现材料前请先脱敏。

## 敏感数据边界

以下内容不得提交到仓库：

- 任意 `.env` 实际配置、API Key、JWT/Fernet 密钥和 TLS 私钥；
- Runner Registration Token、credential、identity 或本机状态目录；
- MySQL/SQLite 数据、Redis/RabbitMQ/MinIO 持久化数据；
- Evidence 正文、Playwright Trace、截图、录屏、浏览器 Session；
- 包含用户信息、请求头、Cookie、模型调用或内部地址的日志和验收产物。

仓库中的 `.env.example` 只包含本地示例值。生产部署必须使用独立凭据、最小权限账号和受保护的 Secret 文件，并遵循[跨平台 Secret 配置说明](文档/01-使用指南/跨平台Secret配置说明.md)。

## 支持范围

安全修复以当前 V1 代码线为主。历史任务包和验收记录用于追溯，不代表仍受支持的独立版本。
