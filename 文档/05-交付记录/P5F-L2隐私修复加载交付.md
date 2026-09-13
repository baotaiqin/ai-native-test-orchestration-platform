# P5F-L2 隐私修复加载交付

## 1. 任务包与结论

- 任务包：`P5F-L2 / r1`
- 执行结论：`PASSED`
- 加载方式：`reload`，仅精确重载 Backend
- 最终源码确认时间：`2026-09-09T18:19:52.0753714Z`
- 重载完成时间：`2026-09-09T18:20:01.0114827Z`
- 正式加载后核验时间：`2026-09-09T18:20:08.935810+00:00`
- 独立运行时复核时间：`2026-09-09T18:20:55.7185758Z`

已将主控复核通过的 `P5F-L1/r2` 日志与 AI 历史展示级隐私修复加载到正式 Backend。未修改任何产品源码，未重启 Frontend、Demo 或 Worker，未执行业务写请求、AI 生成请求、消息操作、恢复操作或归档操作。

## 2. 加载源码指纹

在停止旧 Backend 前立即重新计算并严格匹配以下 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `backend/app/core/redaction.py` | `458d05f7299b56cd277ec6b63ee3082527c34986b167f7c5c3e9432f7a74ddbb` |
| `backend/app/core/logging.py` | `4736ef0407ba5ba41473ef3d00b17f55f8acaa4dea6b9fad44e031675bebca43` |
| `backend/app/modules/prompt_center/service.py` | `2db4cfbbcc949fdb0ace73f2ce13f4c69b71715e6007ffada65397f5856a2c26` |

新 Backend 全部进程均晚于该确认时间启动，`ready.json` 已记录 `reload_package=P5F-L2/r1` 与以上加载指纹。

## 3. 精确 Backend 重载证据

旧 Backend 在停止前通过 ready、owned、端口监听、PID、父子关系、启动时间、命令身份与命令 SHA-256 联合校验。仅停止以下已确认 PID：`22188`、`10400`、`20860`；独立复核确认三者均已退出。

新 Backend 工作目录为 `backend`，启动命令身份为 `python-uvicorn-app.main-127.0.0.1-8000`，使用 `Start-Process -WindowStyle Hidden` 启动。

| 角色 | PID | 父 PID | 启动时间（UTC） | 命令 SHA-256 |
| --- | ---: | ---: | --- | --- |
| Python 根进程 | `29460` | `58936` | `2026-09-09T18:19:56.1075454Z` | `9cbb5504ee4accdfff5140e25fa883401284743eb3086ec9c927553a8a31878e` |
| 控制台宿主 | `22252` | `29460` | `2026-09-09T18:19:56.1150069Z` | `b4359116b59ef8fd3343f3c7d5a2f144b7f60a36d8c928e85ef8003dcd9f8fc1` |
| Uvicorn 监听进程 | `52512` | `29460` | `2026-09-09T18:19:56.1653374Z` | `c720ba10bdc8b52f7694a2b125892b25a5e3937c62b35105477ac871b0388ab8` |

独立运行时复核确认：端口 `127.0.0.1:8000` 只有 PID `52512` 监听；新进程树实际启动时间、父子关系与命令哈希全部匹配记录；Backend `/health` 返回 `status=ok, service=backend`。

## 4. 非 Backend 隔离与历史

- Frontend：PID `53572`，启动时间 `2026-09-09T16:41:20.2853262Z`，未变化且健康。
- Demo：PID `21264`，启动时间 `2026-09-09T16:41:17.7881556Z`，未变化且健康；`changed_locator=true`。
- Worker：PID `23364/57108`，逻辑 Worker 数 `1`，未变化；Runner 为 `ACTIVE/ONLINE`，WEB capability 为 `READY`，slot 为 `1/1`。
- middleware、database、runner 以及 ready/owned 中全部非 Backend 记录与重载前历史语义一致。

重载前清单已保存：

| 历史文件 | SHA-256 |
| --- | --- |
| `.codex-validation/p5e-services/ready-history-pre-P5F-L2-r1-20260909T181954854Z.json` | `65351ae4a7f520828f2ead7bc2119a51e88af75c6b1029c76499ffa5e39e2076` |
| `.codex-validation/p5e-services/owned-processes-history-pre-P5F-L2-r1-20260909T181954854Z.json` | `728967bf1fddca1946172764e39b1ee66ee884673a5b1481f0f0f6a1116631e6` |

## 5. 正式 readiness 与隐私核验

加载后 17 项检查全部为 `true`：

- 数据库迁移版本为 `20260909_0038`，无进行中的 Run 或 Recording。
- 固定失败 Run `run_15c59b0b27964dd8a6fec2fcca840f9c` 保持 `FAILED`；修复后 Run `run_7852432fb7b241dabcad111664b9621b` 保持 `SUCCESS`。
- Analysis `2` 保持 `COMPLETED` 并关联 AiCall `20`；其 prompt version `16`、output schema `12`、成功/回退/修复/重试审计值均符合预期。
- Project `23` 的授权 AI 历史读取共 `9` 条，ID 精确为 `12..20`；审计字段逐项与只读数据库结果一致。
- API 返回的 `raw_response`、`repair_response`、`parsed_result`、`validation_errors` 四类内容字段存在且通过展示级脱敏幂等检查；验证产物只记录安全布尔结果，未序列化或打印这些内容。
- Case `5/6`、Element `1/2`、Page `1/2` 均保持 `ARCHIVED`；Project `23` 与既有验收历史未改变。
- 三项 HTTP 服务均健康，Demo 控制状态保持 `changed_locator=true`。

验证期间只执行一次正常登录 POST 与一次授权 AI 历史 GET；业务 POST 为 `0`，AI 生成请求为 `0`，消息操作为 `0`。未打印凭据或 AI 内容字段。

## 6. 受保护资产复核

| 资产 | SHA-256 |
| --- | --- |
| P5-E 成功 result | `2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176` |
| P5-E 成功 attempt | `2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176` |
| P5-E AI 调用账本 | `eb9cbf3fd6a43b5be90d74a658d01ae73126b6f3f82e6ca1444b21c1e19f8b43` |
| P5F-C1 verification | `58f35fc46abe70bdaaa10be738e0e9504420239ad3704a14f44e06d63d492597` |

P5-E 账本仍为 `total_attempted=5`；P5F-C1 历史摘要前后仍为 `1d4b2a85d48bc286db7475fb3ebc42eceb689d9a56095e66c3f1270a036dfc78`。

## 7. 本任务包实际写入范围

- `deploy/p5f_l2_readiness.py`
- `deploy/p5f_l2_backend_reload.ps1`
- `.codex-validation/p5f-l2/preflight.json`
- `.codex-validation/p5f-l2/backend-reload.json`
- `.codex-validation/p5f-l2/verification.json`
- `.codex-validation/p5e-services/ready.json`（仅 Backend 记录变化）
- `.codex-validation/p5e-services/owned-processes.json`（仅 Backend 记录变化）
- 上述两份 `pre-P5F-L2-r1` 历史快照
- `文档/05-交付记录/P5F-L2隐私修复加载交付.md`

工具与证据 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `deploy/p5f_l2_readiness.py` | `307c133bb4fd2eda53be72fd98ba2ae4e54031f08ef8312de40c2a2571b75673` |
| `deploy/p5f_l2_backend_reload.ps1` | `43bc3c4e137865abff747b737655d12fa70231f0bc85d2609f12d6446efe8925` |
| `.codex-validation/p5f-l2/preflight.json` | `e6bd9eb22933c0fca3900e55afcdfcd299b1068cded702d4b8f959c9aeb059e2` |
| `.codex-validation/p5f-l2/backend-reload.json` | `caffd61e0b1223c8d1110ea42ca99d0b2696f04c265d34f919552d8a59002373` |
| `.codex-validation/p5f-l2/verification.json` | `db286e04e784a1eab512987560c891c43be61e516f36a856b878199f6bcff53a` |

静态校验：Python `py_compile` 通过，Ruff 通过，PowerShell AST 解析通过。按任务包约束未运行全量测试。

## 8. 剩余事项

本任务包无已知阻塞或待修复项。后续仅需主控读取证据并完成 `P5F-L2/r1` 验收；本执行任务不自行扩大到下一任务包。
