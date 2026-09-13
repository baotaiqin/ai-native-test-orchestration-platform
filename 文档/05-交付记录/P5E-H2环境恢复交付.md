# P5E-H2 / r1 环境恢复交付

## 结论

P5E-H2/r1 已完成，本机 Backend、Frontend、Demo 与唯一注册 Runner Worker 均已恢复并保留运行。最终机器可读验证为 `ready=true`，23 项安全检查全部通过。未重启容器或 MySQL，未重新注册 Runner，未停止用户进程，未重投旧消息，未调用 AI，未创建、批准或执行测试资产，也未修改 Demo 定位器开关。

最终证据：

- `.codex-validation/p5e-services/ready.json`，生成时间 `2026-09-09T16:47:58.8643964Z`；
- `.codex-validation/p5e-services/h2-verification.json`，生成时间 `2026-09-09T16:48:13.432608+00:00`，`ready=true`；
- `.codex-validation/p5e-services/owned-processes.json`，仅含本轮确认归属的进程元数据，无凭据或完整配置。

## 旧清单保留与启动前现场

旧清单已在任何覆盖前保存：

| 历史副本 | SHA256 |
|---|---|
| `.codex-validation/p5e-services/ready-history-pre-P5E-H2-r1-20260909T163716Z.json` | `9e7f9163bce827f7501b3f237283fca6b77ee7184964813160d9bca35a7af743` |
| `.codex-validation/p5e-services/owned-processes-history-pre-P5E-H2-r1-20260909T163716Z.json` | `4ad6ece2a51369f07760c1ff633e01c3d52cef363f3c627d77c23123e50d61ef` |

启动前只读核对结果：8000、5173、8765 三个端口均无监听；Backend、Demo、Runner Worker 进程数均为 0。另有一个不监听 5173、也不在旧 owned 清单中的既有 Node 进程 PID 43220，本轮未认领、未停止；它随后自行退出。

中间件启动前后均保持原状：`ai-test-rabbitmq`、`ai-test-redis`、`ai-test-minio` 为 running/healthy；未执行容器重启。正式 MySQL 通过 Alembic 与 ORM 只读查询验证可用。

## H1/r2 与 Runner 源码加载证明

最终启动前确认时间为 `2026-09-09T16:40:59.1906255Z`。下列文件启动前和服务启动后的 SHA256 完全一致，且与 H1/r2 交付记录一致：

| 文件 | 启动前/后 SHA256 | 结果 |
|---|---|---|
| `backend/app/modules/web_healing/service.py` | `b17fa1d82f818f325850558e2621c280c57f4468c69348ce247bc5c12a0b183f` | 一致 |
| `backend/app/modules/web_healing/schemas.py` | `1c8a4d5b1a29fad37ea81a5861c363495247c0d50fc5db5d26187c01af069a11` | 一致 |
| `backend/tests/test_runs.py` | `06884bc572729657e541da1cacb90a14787256cab706bc6bf70f95c1412da8b6` | 一致 |
| `backend/tests/test_p5e_acceptance_resume.py` | `1b3c606a612105cd0837340014ca5079b6aa93aa2da2b2abe621d9add75ce591` | 一致 |
| `deploy/p5e_live_acceptance.py` | `4ec9bce6946cad14691ec4ae3fec10a769f07b5e64cf411800faac26719ec216` | 一致 |
| `runner/runner/isolation.py` | `8dd2184ed0b72f9196ab94e78ecd707e79954ee69879f84211ca81e3b7d336a2` | 一致 |

Backend 根进程启动于 `2026-09-09T16:41:12.9040385Z`，Runner 根进程启动于 `2026-09-09T16:41:25.4271612Z`，均晚于最终哈希确认时间。Backend 的 `/openapi.json` 中 `components.schemas.WebHealingProposalResponse.properties.candidate_locators.maxItems` 实测为 `360`，证明运行进程已加载 H1/r2 Schema。

## 服务、进程树与归属

所有后台进程均由 `Start-Process -WindowStyle Hidden` 启动。最终监听 PID 与归属树均再次核对为存活，且监听 PID 与 ready 清单一致。

| 服务 | HTTP 指纹 | 监听 PID / 启动时间 UTC | H2 归属进程树（PID <- parent PID） |
|---|---|---|---|
| Backend | `/health`: `status=ok, service=backend` | `50392` / `2026-09-09T16:41:12.9893466Z` | `55064 <- 32060`；`31292 <- 55064`；`50392 <- 55064` |
| Demo | `/health`: `status=ok, service=v1-demo` | `21264` / `2026-09-09T16:41:17.7881556Z` | `54536 <- 32060`；`45032 <- 54536`；`21264 <- 54536` |
| Frontend | `<title>AI 原生智能测试编排平台</title>` | `53572` / `2026-09-09T16:41:20.2853262Z` | `53572 <- 32060`；`832 <- 53572`；`44852 <- 53572` |

Runner 复用既有注册身份 `1282a04dfd894f85877384cb31af5c16`，未重新注册。最终状态：

- `ACTIVE / ONLINE`；
- `WEB READY`，容量 `1/1` 可用；
- 一个逻辑 Worker、两个 Windows venv 进程：`23364 <- 32060`（`2026-09-09T16:41:25.4271612Z`）与 `57108 <- 23364`（`2026-09-09T16:41:25.4898850Z`）；
- 专属队列 `ai_test.runner.1282a04dfd894f85877384cb31af5c16.v1` 存在，最终消息数为 0；Worker 使用轮询方式，因此 RabbitMQ 的瞬时 consumer 数不作为在线判据，在线性由 Redis 心跳、数据库状态与 WEB 容量共同验证。

## 正式库与恢复保护

只读验证结果：

| 项目 | 结果 |
|---|---|
| Alembic revision | `20260909_0038 (head)` |
| 执行中 Run（ASSIGNED/RUNNING/CANCELLING） | `0` |
| QUEUED Run | `0` |
| 活动录制（RUNNING/STOP_REQUESTED） | `0` |
| 固定 Run `run_15c59b0b27964dd8a6fec2fcca840f9c` | `FAILED / WEB_LOCATOR_NOT_FOUND`，终态，Proposal 数 `0` |
| Call16 | 精确 ID 命中数 `1`；Project 23、`LOCATOR_HEALING`、success=true、fallback/repair=false、retry=0、error=null、validation error 数=0 |
| P5-E 验收调用账本 | `total_attempted=1`，记录数 `1` |
| Demo `/control/state` | `changed_locator=false`，只读记录，未修改 |

Project 23 历史 `LOCATOR_HEALING` 数据库记录总数为 2，这是信息性全项目历史口径；H1 恢复门禁要求的是 Call16 精确身份与本次验收 ledger 的唯一记录，两者均通过。H2 没有产生新的 AiCallLog。

旧重投 journal `.codex-validation/p5e-services/redrive-run_15c59b0b27964dd8a6fec2fcca840f9c.json` 仍为 `state=source_acked`。RabbitMQ `ai_test.tasks.dead.v1` 仍有 1 条历史死信；本轮未读取消息体、未重投、未 ack、未 purge。

## 工具修订与验证

`deploy/p5e_prepare_services.ps1` 增强了以下安全行为：

- 空 owned 清单可安全落盘；
- 为 Backend、Demo、Frontend 和 Runner 记录 PID、父 PID、进程名、UTC 启动时间及完整 H2 归属树；
- 冷启动时最多等待 30 秒完成 Runner 心跳与专属队列就绪，避免服务刚启动后的瞬时竞态；
- 仍只复用现有端口/身份，凭据仅从既有运行配置读入内存且不输出。

新增 `deploy/p5e_h2_readiness.py`，仅执行 localhost HTTP、正式库/Redis 只读查询、源码哈希和安全清单核对。静态验证结果：

- `deploy/p5e_prepare_services.ps1` PowerShell Parser：通过；
- `deploy/p5e_h2_readiness.py` Ruff：`All checks passed!`；
- `deploy/p5e_h2_readiness.py` `py_compile`：通过；
- 修订后的 `p5e_prepare_services.ps1` 在复用现有进程时返回 READY；
- 最终 `p5e_h2_readiness.py`：`ready=true`，无失败检查。

关键交付 SHA256：

| 文件 | SHA256 |
|---|---|
| `deploy/p5e_prepare_services.ps1` | `be019b3cf1aefc8920b357d71f58e765b02e7903725e3486cbb70fa65a95e460` |
| `deploy/p5e_h2_readiness.py` | `a7ed8b2190b605ea924e009bbbcb05b7669bc425678876848a47462d79d0ca3b` |
| `.codex-validation/p5e-services/ready.json` | `752cf9adb9dc7f7fdef5031727ed2f837a2dcd75d84eb0a01c97324e7cdadf3a` |
| `.codex-validation/p5e-services/owned-processes.json` | `b8b5eeb3fbafa889cb6d27e1108e8a58ef43f6623465037a8dd3d0036b9e0690` |
| `.codex-validation/p5e-services/h2-verification.json` | `7af6a77269c1045c46afd57a9d771141600da4b3af45fd5508a2c0eb17f553bf` |

## 后续边界

服务按任务包要求保持运行。本包未执行 H3：Demo 仍为 `changed_locator=false`。必须由主控审查 H2 后另行派发 H3，并在 H3 前按固定合成 attempt 的授权将 Demo 重构造为 `changed_locator=true`；不得将本轮新启动的 Demo 误认为原进程延续，也不得再次重投旧失败消息。
