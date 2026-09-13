# P5-F 启动指南核对记录

> 核对日期：2026-09-09  
> 核对范围：`文档/01-使用指南/PyCharm启动指南.md` 与其直接依赖的实际运行配置、包脚本和只读源码入口  
> 写入边界：本轮仅修改启动指南并新增本记录；未修改源码、运行配置或其他项目文档

## 1. 核对依据

- `.run/后端 FastAPI.run.xml`：使用 PyCharm FastAPI 配置和项目模块 SDK，工作目录为 `backend`，监听 `127.0.0.1:8000`；
- `.run/后端测试.run.xml`：显式绑定根目录 `.venv/Scripts/python.exe`，以模块方式执行 pytest；
- `.run/前端 Vue.run.xml`、`frontend/package.json`、`frontend/vite.config.ts`：执行 `npm run dev`，实际使用 `vite --configLoader runner --host 127.0.0.1 --port 5173`，启用 `strictPort`、自动打开页面，并代理 `/api`、`/health` 到后端；
- `.run/Runner Worker.run.xml`、`runner/runner/cli.py`、`runner/runner/consumer.py`：Runner 显式绑定根目录 `.venv`，共享配置参数为 `worker --web-slots 1`；API_CASE、SCENARIO 使用 API Slot，WEB_CASE、WEB_RECORDING 使用 Web Slot；
- `backend/app/modules/evidence/`、`frontend/src/api/evidence.ts`、Runner Evidence 实现：MinIO 已进入受保护 Evidence 业务链路，不再只是空闲中间件；
- `backend/migrations/versions/20260909_0038_model_category.py` 与 Alembic 只读命令：当前源码迁移头和开发库当前版本均为 `20260909_0038`；
- `frontend/src/views/dashboard/DashboardView.vue`：健康卡片可核对后端、前端和当前环境，但阶段引导仍显示早期 Phase 1/2 文案。

## 2. 启动指南修正

1. 将适用阶段从 Phase 4 更新为 Phase 5 收尾 / V1，并引用当前收尾计划；
2. 明确 FastAPI 共享配置使用模块 SDK，后端测试和 Runner 才是运行配置内显式绑定 `.venv`；
3. 补充 Runner 包依赖安装、API/Web Slot、四类任务，以及手工 Worker 的 `--web-slots 1`；
4. 对齐 Vite 开发命令、5173 严格端口、自动打开和后端代理；
5. 更新 Redis、RabbitMQ、MinIO 的当前职责，保留本机 MySQL 和 Compose 端口、安全边界；
6. 将迁移检查改为同时执行 `alembic heads` 与 `alembic current`，并解释代码头和数据库当前版本的区别；
7. 更新当前后端、前端、Runner 功能目录入口；
8. 将 Dashboard 的真实健康项与项目阶段口径分开，明确早期 Phase 1/2 页面引导尚未替换，不能据此判断当前进度。

## 3. 自检方式与结果

在 `backend` 目录执行：

```powershell
..\.venv\Scripts\python.exe -m alembic heads
..\.venv\Scripts\python.exe -m alembic current
```

结果：两条命令均退出码 0，均显示 `20260909_0038 (head)`；`current` 使用 MySQL 方言完成只读版本核对。

在 `runner` 目录执行：

```powershell
..\.venv\Scripts\python.exe -m runner.main worker --help
..\.venv\Scripts\python.exe -m runner.main register --help
```

结果：两条命令均退出码 0，参数中均包含 `--api-slots`、`--web-slots` 和 `--performance-slots`；未启动 Worker、未读取 Runner 身份。

对启动指南执行 UTF-8 严格解码和换行字节检查。结果：UTF-8 有效、无 BOM、CR 数量为 0，文件保持 LF。

通过只读搜索逐项核对 `.run` 字段、Vite/package 脚本、Runner 分流、Evidence 入口、功能目录及 Dashboard 当前文案。未运行前端构建或全量测试，未启动或停止服务，未访问业务数据和敏感内容。

## 4. 当前遗留

- Dashboard 的阶段引导仍显示早期 Phase 1/2 文案；本轮只在指南中明确其非权威性，未越过文件边界修改页面源码；
- Runner CLI 的部分帮助描述仍只写 API_CASE，实际 Consumer 已支持四类任务；本轮未修改 Runner 源码；
- 上述两项不影响本轮启动指南与实际命令、端口、解释器、迁移版本和运行能力的对齐。
