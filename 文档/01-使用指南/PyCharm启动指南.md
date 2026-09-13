# AI 原生智能测试编排平台——PyCharm 启动指南

> 适用版本：完整 V1 本地开发环境  
> 更新日期：2026-09-11  
> 目标：在 PyCharm 和 Docker Desktop 中按顺序启动中间件、后端、前端和 Windows Runner

业务主流程和全部页面用法参见[《V1 项目完整使用说明》](./V1项目完整使用说明.md)。

---

## 1. 已提供的启动配置

项目根目录的 `.run` 文件夹已经提供以下 PyCharm 共享运行配置：

| 配置名称 | 用途 | 默认地址 |
|---|---|---|
| `后端 FastAPI` | 使用 PyCharm 原生 FastAPI 配置、项目模块 SDK 和 `fastapi dev` 启动后端 | `http://127.0.0.1:8000` |
| `前端 Vue` | 启动 Vue 3 + Vite 开发服务器 | `http://127.0.0.1:5173` |
| `后端测试` | 使用项目 `.venv` 显式执行后端 pytest 测试 | 无监听端口 |
| `Runner Worker` | 使用项目 `.venv` 前台运行已注册 Runner，以 API/Web Slot 消费 `API_CASE`、`SCENARIO`、`WEB_CASE` 和 `WEB_RECORDING` | 依赖后端 8000、Redis 6379、RabbitMQ 5672；Web Evidence 经后端写入 MinIO 9000 |
| `开发环境（一键启动）` | 同时启动后端、前端和已注册 Runner Worker | 前端 5173、后端 8000 |

PyCharm 下拉框顶部的“最近的配置”只是历史快捷入口，可能与“所有配置”中的同一项重复显示，不代表项目中存在额外配置。清理配置后，旧的最近记录可能短暂残留；重启 PyCharm，或运行新的配置一次后，列表会逐步更新。

正常情况下，使用 PyCharm 打开项目根目录后，这些配置会自动出现在右上角的运行配置下拉框中。

`后端 FastAPI` 使用项目模块 SDK（`.run` 中为 `IS_MODULE_SDK=true`），`后端测试` 与 `Runner Worker` 则显式绑定 `$PROJECT_DIR$/.venv/Scripts/python.exe`。因此应把项目模块的 Python SDK 设置为同一个 `.venv`；如果 PyCharm 在配置变更前已经打开，请重新打开运行配置窗口。仍显示“无解释器”时，按下一节把该文件注册为 Existing environment，并将其设为项目 SDK。

---

## 2. 第一次打开项目

### 2.1 打开正确目录

在 PyCharm 中选择：

```text
File → Open
```

打开项目根目录 `AI 原生智能测试编排平台`，不要只打开 `backend` 或 `frontend` 子目录，否则复合启动配置可能无法被识别。

### 2.2 配置 Python 解释器

本项目要求 Python 3.12。项目中已创建后端虚拟环境时，优先选择：

```text
.venv\Scripts\python.exe
```

PyCharm 操作：

```text
File → Settings → Project → Python Interpreter
→ Add Interpreter → Add Local Interpreter
→ Existing environment
→ 选择 .venv\Scripts\python.exe
```

如果根目录 `.venv` 不存在：

1. 先安装官方 Python 3.12，并在安装时启用 Add Python to PATH；
2. 在上述页面选择 Virtualenv；
3. Base interpreter 选择 Python 3.12；
4. Location 设置为项目根目录下的 `.venv`；
5. 创建后打开 PyCharm Terminal，在项目根目录执行：

```powershell
.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
.venv\Scripts\python.exe -m pip install -e ".\runner"
```

两条命令分别安装后端开发依赖和 Runner 的 HTTP、RabbitMQ、MySQL、Playwright 依赖；后端、后端测试与 Runner 共用根目录 `.venv`。

解释器验证：

```powershell
.venv\Scripts\python.exe --version
```

应显示 Python 3.12.x。

### 2.3 配置 Node.js

本项目前端需要 Node.js 20 或更高版本，当前已使用 Node.js 22 验证。

PyCharm 操作：

```text
File → Settings → Languages & Frameworks
→ JavaScript Runtime（部分版本显示为 Node.js）
→ Node runtime 选择本机 Node.js
→ Package manager 选择对应 npm
```

如果看不到 JavaScript/Node.js 设置，先在：

```text
File → Settings → Plugins
```

启用 JavaScript and TypeScript、Node.js 等随 PyCharm 提供的相关插件。

首次安装前端依赖，在 PyCharm Terminal 中执行：

```powershell
cd frontend
npm install
```

完成后应存在 `frontend\node_modules` 和 `frontend\package-lock.json`。

---

## 3. 本机开发与可选 Docker

当前阶段采用本机优先方式开发，**启动前后端不需要 Docker**。项目后端默认连接：

```text
MySQL：127.0.0.1:3306
Database：ai_test_platform
User：test_platform
```

Navicat 等数据库工具继续连接本机 MySQL `127.0.0.1:3306`，不连接 Compose 中的 MySQL。日常开机顺序为：Docker Desktop/Engine → 默认 Compose 中间件 → 本机 MySQL → 数据库迁移 → `开发环境（一键启动）`。

请先确保本机 MySQL 8.4 服务正在运行，再执行数据库迁移。Runner Worker 依赖 Redis 和 RabbitMQ；MinIO 已接入 Evidence 业务链路，由后端负责对象存储，Runner 通过后端受保护接口上传 Web Evidence。默认 Compose 应同时保持 Redis、RabbitMQ 和 MinIO 可用。

当前本机开发库已创建并完成初始迁移。验证链路为：

```text
Vue 3 :5173
    ↓ /api 代理
FastAPI :8000
    ├─ MySQL :3306 / ai_test_platform（SQLAlchemy + PyMySQL）
    ├─ Redis :6379（实时状态、事件与 Runner 在线信息）
    ├─ RabbitMQ :5672（任务排队与 Runner 消费）
    └─ MinIO :9000（受保护 Evidence 对象）
```

### 3.1 Docker 作为可选方案

项目仍保留 Docker Compose。当前本机开发默认继续使用现有的 `127.0.0.1:3306` MySQL，Docker Compose 只启动 Redis、RabbitMQ 和 MinIO；只有明确需要容器 MySQL 时才启用 `mysql` profile。只有选择这些容器化中间件时，才需要 Docker Engine 或 Docker Desktop。

在 PyCharm Terminal 的项目根目录执行以下命令，启动默认开发中间件：

```powershell
docker compose -f .\deploy\docker-compose.dev.yml up -d
```

该命令不会启动或占用 Docker MySQL 的 `3306` 端口。本机 FastAPI 仍连接现有的 `127.0.0.1:3306`。

如需临时改用 Compose 提供的 MySQL，显式启用 profile：

> 注意：该 profile 仍使用宿主机 `3306`，启用前必须先停止本机 MySQL，否则会发生端口冲突。

```powershell
docker compose -f .\deploy\docker-compose.dev.yml --profile mysql up -d
```

停止默认开发中间件：

```powershell
docker compose -f .\deploy\docker-compose.dev.yml down
```

查看状态：

```powershell
docker compose -f .\deploy\docker-compose.dev.yml ps
```

默认端口：

| 服务 | 端口 | 用途 |
|---|---:|---|
| MySQL | 3306 | 可选容器数据库，仅 `mysql` profile 启用；默认使用本机 MySQL |
| Redis | 6379 | 实时状态与缓存 |
| RabbitMQ | 5672 | AMQP |
| RabbitMQ 管理台 | 15672 | 浏览器管理界面 |
| MinIO API | 9000 | 对象存储 API |
| MinIO Console | 9001 | 浏览器管理界面 |

开发 Compose 中的默认账号仅用于本地开发。需要自定义时，把 `deploy\.env.example` 复制为 `deploy\.env` 后修改本地值，不要把真实密码写入文档。所有 Compose 端口仅绑定 `127.0.0.1`，不对局域网暴露。

如果提示 `failed to connect to the docker API` 或 `docker daemon is not running`，说明 Docker Engine 尚未启动。先启动 Docker Desktop/Engine，等待其显示 Running，再重新执行上面的默认启动命令。日常不需要在 Windows 上另行安装 Redis、RabbitMQ 或 MinIO。

---

## 4. 执行数据库迁移

确认本机 MySQL 服务正在运行后，在 PyCharm Terminal 中执行：

```powershell
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
```

分别检查代码迁移头和当前数据库版本：

```powershell
..\.venv\Scripts\python.exe -m alembic heads
..\.venv\Scripts\python.exe -m alembic current
```

`heads` 表示当前源码迁移图的最新版本，`current` 表示当前连接数据库实际应用的版本。两者都应显示：

```text
20260912_0055 (head)
```

若 `current` 低于 `heads`，先确认连接的是预期本机开发库，再执行 `upgrade head`；不要对正式业务库执行降级或用手写 SQL 修改迁移终态。迁移输出只用于核对版本，不要在截图、日志或文档中暴露数据库连接凭据。

---

## 5. 分别启动后端

### 5.1 使用已有配置

1. 在 PyCharm 右上角运行配置下拉框选择 `后端 FastAPI`；
2. 点击绿色运行按钮，调试时点击 Debug；
3. 控制台出现以下含义的日志即为成功：

```text
Application startup complete
Uvicorn running on http://127.0.0.1:8000
```

验证地址：

- 健康检查：`http://127.0.0.1:8000/health`
- Swagger UI：`http://127.0.0.1:8000/docs`
- V1 健康接口：`http://127.0.0.1:8000/api/v1/system/health`
- 应用与本地数据库就绪检查：`http://127.0.0.1:8000/api/v1/system/readiness`

### 5.2 配置未自动出现时

打开：

```text
Run → Edit Configurations → + → FastAPI
```

填写：

| 配置项 | 值 |
|---|---|
| Name | `后端 FastAPI` |
| Application file | 项目下的 `backend\app\main.py` |
| Application name | `app` |
| ASGI server | `FastAPI Dev` |
| Additional options | `--host 127.0.0.1 --port 8000` |
| Working directory | 项目根目录下的 `backend` |
| Python interpreter | 项目模块 SDK（建议设为项目根目录 `.venv\Scripts\python.exe`） |

保存后运行。共享配置实际使用 PyCharm 模块 SDK，而不是在该运行配置中写死解释器路径；`后端测试` 配置仍显式绑定项目 `.venv`。该配置执行官方 `fastapi dev` 命令；FastAPI CLI 内部仍使用 Uvicorn 提供 ASGI 服务，这是 FastAPI 的正常运行方式。

---

## 6. 分别启动前端

### 6.1 使用已有配置

1. 确保已经执行 `npm install`；
2. 在 PyCharm 右上角选择 `前端 Vue`；
3. 点击运行按钮；
4. 控制台出现 `Local: http://127.0.0.1:5173/` 即为成功；
5. Vite 会自动使用系统默认浏览器打开 `http://127.0.0.1:5173`。

前端开发服务器会把 `/api` 和 `/health` 请求代理到 `http://127.0.0.1:8000`，因此登录和工作台健康状态需要后端同时运行。

自动打开浏览器已经由 `frontend/vite.config.ts` 统一控制。PyCharm 的 npm 配置中不需要再启用“浏览器 / Live Edit”；如果该选项已勾选但 URL 为空，请取消勾选，以免 PyCharm 在运行前报“未指定 URL”。

`frontend/package.json` 的实际开发脚本为 `vite --configLoader runner --host 127.0.0.1 --port 5173`；`frontend/vite.config.ts` 同时启用 `strictPort: true`、`open: true`，并将 `/api`、`/health` 代理到后端 8000。端口被占用时 Vite 会直接失败，不会自动换端口再打开一个页面。

### 6.2 配置未自动出现时

打开：

```text
Run → Edit Configurations → + → npm
```

填写：

| 配置项 | 值 |
|---|---|
| Name | `前端 Vue` |
| package.json | 项目下的 `frontend\package.json` |
| Command | `run` |
| Scripts | `dev` |
| Node interpreter | 项目配置的 Node.js |

保存后运行。

---

## 7. 开发环境（一键启动）

这是日常开发推荐方式。

### 7.1 使用已有复合配置

1. 确保 Python 和 Node.js 已按前文配置；
2. 确保后端依赖、前端依赖已安装；
3. 在 PyCharm 右上角选择 `开发环境（一键启动）`；
4. 点击 Run 或 Debug；
5. PyCharm 会同时打开 `后端 FastAPI`、`前端 Vue` 和 `Runner Worker` 三个运行控制台；
6. 等待后端、前端和 Runner Worker 都显示启动成功；
7. 前端就绪后会自动打开 `http://127.0.0.1:5173`。

### 7.2 手工创建复合配置

如果已有配置未显示：

```text
Run → Edit Configurations → + → Compound
```

填写：

| 配置项 | 值 |
|---|---|
| Name | `开发环境（一键启动）` |
| Run configurations | 添加 `后端 FastAPI`、`前端 Vue` 和 `Runner Worker` |

保存后即可一键启动。

### 7.3 启动 Runner Worker

Runner Worker 只接收已经注册好的本机身份，不会在启动时自动创建 Token 或注册新 Runner。当前本地开发库已于 2026-09-11 重建为空白基线，数据库中没有 Runner 记录；首次使用必须按下述步骤创建新 Token 并重新注册。即使本机状态目录仍有旧身份，它也已经失效，不能直接启动 Worker 复用。

按以下顺序操作：

1. 需要查看环境时，在 PyCharm Terminal 的项目根目录执行以下只读 Inspect 命令；它不写状态目录：

   ```powershell
   cd runner
   ..\.venv\Scripts\python.exe -m runner.main inspect
   ```

2. 需要检查 DPAPI 和状态目录时，在同一 Terminal 执行 Doctor；它不读取真实 credential，也不会回退到明文保存：

   ```powershell
   ..\.venv\Scripts\python.exe -m runner.main doctor
   ```
3. 只有首次使用、credential 被管理员撤销，或本机 Runner 状态目录丢失时，才在 Runner 中心由管理员创建一次性 Registration Token。
4. 在 PyCharm Terminal 中进入 `runner` 目录，交互式执行注册命令；命令会隐藏输入 Token，不要把 Token 写在命令行参数、脚本、截图或日志中：

   ```powershell
   cd runner
   ..\.venv\Scripts\python.exe -m runner.main register --backend-url http://127.0.0.1:8000 --name windows-runner --tag windows --api-slots 1 --web-slots 1
   ```

Token 只在 Runner 中心展示一次。注册成功后，credential 仅以正常 Windows 用户上下文中的 DPAPI 保护形式保存在默认状态目录 `%LOCALAPPDATA%\AI Native Test Platform\runner`；不要编辑、复制、打印或删除该目录中的身份文件。
5. 注册完成或已有身份时，在 Terminal 中执行一次心跳验收：

   ```powershell
   ..\.venv\Scripts\python.exe -m runner.main heartbeat-once
   ```

返回 `ACTIVE` 后，在 PyCharm 运行 `Runner Worker`，或在 Terminal 中先设置本机开发 RabbitMQ URL，再执行：

   ```powershell
   $env:AI_TEST_RABBITMQ_URL = "amqp://test_platform:test_platform@127.0.0.1:5672/"
   ..\.venv\Scripts\python.exe -m runner.main worker --web-slots 1 --performance-slots 1
```

共享 `Runner Worker` 配置建议以模块方式运行 `runner.main`，参数为 `worker --web-slots 1 --performance-slots 1`，并通过 `AI_TEST_RABBITMQ_URL` 注入上述本机开发 URL；该 URL 只在进程环境中使用，不写入 Runner 状态目录。其中账号仅为 Compose 的非生产默认账号，只限本机开发，禁止用于生产。Worker 是前台长期进程，会持续保持 Runner 在线；普通 `API_CASE`、`SCENARIO` 使用 API Slot，`WEB_CASE`、`WEB_RECORDING` 使用 Web Slot，V1 性能 Run 使用独占 PERFORMANCE Slot。可按 `Ctrl+C`，或在 PyCharm 点击 Stop，安全停止领取新任务；正在执行的任务会先完成约定的终态上报与消息处置，再按顺序退出。退出后等待 Redis 心跳 TTL 到期，Runner 中心会显示离线。

6. 新注册成功且心跳返回 `ACTIVE` 后，日常推荐运行 `开发环境（一键启动）`。它不会自动生成 Token 或自动注册。

Runner Worker 在线状态保存在 Redis。Redis 不可用时，后端会将在线状态显示为 `UNKNOWN`，不应据此判断 Runner 已正常离线。RabbitMQ 已承载四类任务的排队与消费；API/Web/录制执行继续走受保护的后端协议。MinIO 已用于 Evidence 对象存储，公开页面只展示安全元数据，下载仍需鉴权；不要直接读取 Bucket、对象路径或 Runner 身份文件。

---

## 8. 登录开发环境

打开前端后使用：

```text
用户名：admin
密码：admin123
```

该账号只用于本机开发与前后端联通验证，不是生产账号。生产环境配置会拒绝使用该默认密码。

登录成功后，Dashboard 当前可以直接用于启动核对的页面项为：

- 后端服务：运行正常；
- 前端应用：运行正常；
- 当前环境：development；

当前完整 V1 功能实现与本地可执行发布门禁已经完成，性能测试及其增强能力均已纳入 V1；代码迁移 head 与本地开发库均为 `20260913_0077`。工作台统计会随着重新创建项目、资产、Runner 和 Run 逐步产生数据。常驻 Backend/Runner 需在后续验收时重新加载当前代码；本次未擅自重启。真实发布环境、真实 Provider/密钥与压力联调属于 V1 发布前待验；V2 不在当前范围。

---

## 9. 在 PyCharm 中编辑和调试

### 后端断点

1. 在 Python 代码行号左侧点击添加断点；
2. 选择 `后端 FastAPI`；
3. 点击 Debug；
4. 从前端或 Swagger UI 发起请求。

推荐首先在以下位置熟悉项目：

```text
backend/app/main.py
backend/app/api/v1/router.py
backend/app/modules/runs/
backend/app/modules/evidence/
backend/app/modules/web_cases/
backend/app/modules/web_recordings/
backend/app/modules/web_recording_ai/
backend/app/modules/web_healing/
backend/app/modules/web_failure_analysis/
backend/app/modules/model_center/
backend/app/modules/ai_gateway/
```

### 前端断点

前端日常开发可直接修改 `.vue` 或 `.ts` 文件，Vite 会自动热更新。推荐从以下位置开始：

```text
frontend/src/views/runs/
frontend/src/views/web/
frontend/src/layouts/
frontend/src/api/
frontend/src/types/
frontend/src/stores/
```

Runner 的当前执行入口与主要实现位于：

```text
runner/runner/main.py
runner/runner/cli.py
runner/runner/consumer.py
runner/runner/scenario.py
runner/runner/executors/api.py
runner/runner/executors/web.py
runner/runner/executors/web_recording.py
runner/runner/evidence.py
```

浏览器级 TypeScript 断点可以使用 PyCharm 的 JavaScript Debug 配置，URL 设置为 `http://127.0.0.1:5173`；普通界面开发也可以使用浏览器开发者工具。

### 复合调试

选择 `开发环境（一键启动）` 后点击 Debug，可同时保留后端 Python、前端热更新和 Runner Worker 进程。若 PyCharm 版本不能在 Compound 中调试 npm，后端使用 Debug、前端使用 Run，Runner Worker 单独使用 Run 即可。

---

## 10. 运行检查

后端测试：

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
```

后端静态检查：

```powershell
cd backend
..\.venv\Scripts\python.exe -m ruff check --no-cache app tests migrations
```

前端类型检查：

```powershell
cd frontend
npm run type-check
```

前端生产构建：

```powershell
cd frontend
npm run build
```

Runner 测试：

```powershell
cd runner
..\.venv\Scripts\python.exe -m pytest
```

Runner 静态检查：

```powershell
cd runner
..\.venv\Scripts\python.exe -m ruff check --no-cache runner tests
```

---

## 11. 常见问题

### Python 命中了 Microsoft Store 占位程序

现象：运行 `python` 时提示找不到 Python，或指向 `WindowsApps\python.exe`。

处理：不要使用该占位程序。在 PyCharm 中明确选择 Python 3.12 或项目的 `.venv\Scripts\python.exe`。

### `No module named fastapi` 或无法执行 `fastapi dev`

原因：运行配置选错解释器，或依赖未安装。

处理：选择 `.venv\Scripts\python.exe`，然后重新安装 `backend` 的开发依赖。项目使用 `fastapi[standard]`，其中包含 FastAPI CLI。

### 5173 或 8000 端口被占用

先停止 PyCharm 中旧的运行实例，再重新启动。不要同时运行多个相同配置。若确需修改端口，应同时修改 `.run` 配置、Vite 代理和本指南。

### 前端能打开但登录失败

检查：

1. `后端 FastAPI` 是否正在运行；
2. `http://127.0.0.1:8000/health` 是否返回 `status: ok`；
3. 前端控制台是否显示代理或 401 错误；
4. 账号是否为 `admin / admin123`。

### Docker 命令存在但无法启动容器

只有 Docker CLI 不够，还需要正在运行的 Docker Engine。启动 Docker Desktop/Engine 后，确认 `docker info` 能正常返回，再执行 Compose 命令。
