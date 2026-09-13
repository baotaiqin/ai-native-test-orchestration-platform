# 贡献指南

感谢参与 AI 原生智能测试编排平台的开发。提交变更前，请先阅读根目录 `AGENTS.md`、[当前开发状态](文档/当前开发状态.md)和与改动相关的产品/实施文档。

## 开发约定

- 使用 Python 3.12、Node.js 20+，文本文件保持 UTF-8 与 LF。
- 不提交 `.env`、真实凭据、Runner 身份、数据库、日志、Evidence、截图或本地验收产物。
- Alembic 迁移只允许向前新增，不修改已交付迁移的历史语义。
- Backend、Runner 与 `contracts` 的消息/API 契约必须同步演进。
- AI 输出必须经过结构化校验；涉及资产变更或执行的流程必须保留人工确认和审计边界。
- 不在无关文件上执行全项目格式化或批量换行转换。

## 提交前检查

根据改动范围运行最小相关检查：

```powershell
# Backend
Set-Location backend
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m ruff check app tests

# Runner
Set-Location ..\runner
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m ruff check runner tests

# Frontend
Set-Location ..\frontend
npm run type-check
npm run build
```

涉及数据库、RabbitMQ、Redis、MinIO、真实浏览器或服务器部署的修改，还应在对应环境完成专项验证，并明确记录未执行的现场验收项。

## Pull Request 说明

PR 描述至少包含：

- 问题背景和变更目标；
- 实际修改范围及契约/迁移影响；
- 已运行的检查与结果；
- 未完成的验收、环境限制和已知风险；
- 涉及 UI 时的关键页面截图（截图前清除账号、Token、URL 参数和业务数据）。

不要把“代码已实现”“基本检查通过”和“完整环境验收通过”混为同一结论。
