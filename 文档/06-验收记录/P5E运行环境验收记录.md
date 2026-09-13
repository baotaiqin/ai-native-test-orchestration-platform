# P5-E 运行环境验收记录

## 验收结论

- 最新运行清单时间：2026-09-09T14:55:17.4487291Z。
- P5-E 真实验收环境已就绪，Backend、Frontend、V1 Demo、正式 MySQL、Redis、RabbitMQ、MinIO 与唯一逻辑 Runner Worker 均通过实际检查。
- 无敏感信息的机器可读清单位于 `.codex-validation/p5e-services/ready.json`。
- 本轮启动的进程继续保留，供 Backend 与 Frontend 完成真实 Chrome、模型调用、证据链和 UI 闭环验收；在主控统一通知前不得停止。

## 服务状态

| 服务 | 地址 | 健康检查与项目指纹 | 所有权 | PID | 启动时间（UTC） |
| --- | --- | --- | --- | ---: | --- |
| Backend | `http://127.0.0.1:8000` | `/health` 返回本项目 Backend 标识 | P5-E-owned | 38960 | 2026-09-09T14:55:12.4731308Z |
| Frontend | `http://127.0.0.1:5173` | 首页包含本项目标题 | P5-E-owned | 15404 | 2026-09-09T13:40:49.1336320Z |
| V1 Demo | `http://127.0.0.1:8765` | `/health` 返回 `v1-demo` 标识 | P5-E-owned | 23956 | 2026-09-09T13:24:10.0704210Z |

Backend 以无 reload 的本地 Uvicorn 进程运行；Frontend 直接使用项目内 Vite，并禁止自动打开浏览器；Demo 按 `python -m demo.server --port 8765` 运行。三个端口在启动前均确认空闲，因此没有替换或停止用户已有服务。

## 数据库与中间件

| 依赖 | 实测结果 |
| --- | --- |
| 正式 MySQL | Alembic `current` 为 `20260909_0038 (head)` |
| RabbitMQ | 既有 `ai-test-rabbitmq` 容器为 `running/healthy`，本机端口 5672 可用 |
| Redis | 既有 `ai-test-redis` 容器为 `running/healthy`，本机端口 6379 可用 |
| MinIO | 既有 `ai-test-minio` 容器为 `running/healthy`，本机端口 9000 可用 |

上述既有容器仅做只读健康检查，没有重启、重建或修改。

## 唯一 Runner Worker

- Runner ID：`1282a04dfd894f85877384cb31af5c16`。
- 控制面状态：`ACTIVE`；Redis 在线状态：`ONLINE`。
- WEB capability：`READY`。
- WEB slots：`total=1`、`available=1`。
- 最近一次实测心跳：2026-09-09T14:09:28.0000000Z，晚于本次 Worker 启动时间。
- RabbitMQ 专属队列 `ai_test.runner.1282a04dfd894f85877384cb31af5c16.v1` 已实际存在。
- Windows 虚拟环境启动器形成一个父子进程树：PID 58120（2026-09-09T14:06:05.7456266Z）与 PID 59460（2026-09-09T14:06:05.8059540Z）。两者属于同一个逻辑 Worker，不是两个 Worker；逻辑 Worker 数严格为 1。
- 控制面实测活动 Run 数为 0；重启前 WEB slot 为 `1/1`，没有中断本轮执行。
- 复用了已注册的本地 Runner 身份，没有注册或覆盖 Runner 状态。

Runner Locator 最终修复保存后，旧 P5-E-owned Worker PID 10392/43084 已先完整停止，再启动上述新进程树；重启过程中逻辑 Worker 从 1 降为 0 后才重新升为 1，从未并存。旧所有权清单保存在 `.codex-validation/p5e-services/owned-processes-history-20260909T135602Z.json`，当前 ledger 只记录仍存活的新进程。

Runner 的连接配置只在启动进程内从现有运行配置读取并校验为本机 RabbitMQ；未写入验收清单、日志或本文档。

## 验证方法与交付物

- `deploy/p5e_prepare_services.ps1`：幂等核对依赖、识别既有项目服务、只补启动缺失服务、按进程树强制单逻辑 Worker、持久化 PID 所有权并生成就绪清单。
- `deploy/p5e_runner_readiness.py`：只读加载现有 Runner ID，交叉检查 MySQL、Redis 中的真实心跳、WEB capability 和实际 slots；输出不含连接配置。
- `.codex-validation/p5e-services/owned-processes.json`：记录 P5-E-owned PID、用途和精确启动时间，供后续主控安全收尾。
- `.codex-validation/p5e-services/ready.json`：供 Backend/Frontend 读取的最终运行状态；已执行敏感字段与带认证信息 URL 扫描，结果为无匹配。

工具已通过 PowerShell 语法解析、Python `py_compile` 与 Ruff 检查，并在当前环境实际运行成功。Backend 与 Frontend 长期任务已收到就绪路径、服务地址、Runner ID 与单 Worker 约束。

## 追加诊断：Run 发布后未 claim

诊断时间：2026-09-09T14:45:07.1711635Z。

固定 Run `run_15c59b0b27964dd8a6fec2fcca840f9c` 曾长期停留在 `QUEUED`。只读实测确认这不是 Worker 或 RabbitMQ topology 丢失：

- 唯一 Worker PID 58120/59460 持续存活并心跳；PID 59460 到本机 RabbitMQ 5672 的 TCP 连接为 `ESTABLISHED`。
- 同一 RabbitMQ 容器、默认 `/` vhost 中存在一个 `running` 连接和一个 channel。
- Runner 专属队列精确存在，计数为 `messages_ready=0`、`messages_unacknowledged=0`、`consumers=0`；Worker 使用 `basic_get` 轮询，因此 `consumers=0` 属于预期行为。
- API 与 WEB routing bindings 均存在。
- 死信队列为 2 条；安全探查后仍为 2 条，没有 purge 或丢失。
- 固定 Run 的死信 `x-death.reason=rejected`，来源为该 Runner 专属队列和 WEB routing key，死信时间为 2026-09-09T14:21:38Z。
- 该死信的任务信封可由当前 Runner 协议正确解析为 `WEB_CASE`、WEB slot。
- 数据库只读 claim preflight 全部通过：Runner 与身份摘要匹配、Run 为 `QUEUED`、Outbox 为 `PUBLISHED` 且未 claim、dispatch payload 与 message ID 一致。

代码路径揭示发布竞态：Backend 先把 Outbox 置为 `PENDING` 并提交，再发布 RabbitMQ 消息，收到发布确认后才把 Outbox 更新为 `PUBLISHED`。Worker 可能在“消息已可见、数据库仍为 PENDING”的窗口内调用 claim；Backend 因 Outbox 尚未 `PUBLISHED` 返回状态冲突，Runner 将该非鉴权 4xx 映射为 `ProtocolError` 并执行永久 reject，消息因而进入 DLQ。该状态组合与实测完全一致：Outbox 最终为 `PUBLISHED`、Run 仍为 `QUEUED`、从未 claim、主队列为空、死信原因是 `rejected`。

截至上述只读诊断结束时，DevOps 尚未修改或重投该 Run，也未再次重启服务。辅助只读工具为 `deploy/p5e_dead_letter_probe.py` 和 `deploy/p5e_claim_preflight.py`；探查不输出消息正文，并在同一 channel 上将消息全部 requeue 保留。随后 Backend/Runner 完成竞态修复，并按下节记录的主控授权实施一次精确恢复。

## Backend 修复加载与固定死信恢复

Backend 已将发布可见性窗口改为专用暂态响应，Runner 保留既有 5xx 重试和最终 requeue 语义。重启前只读门禁确认：`ASSIGNED/RUNNING/CANCELLING` Run 为 0、活动 Web 录制为 0、Backend 外部 HTTP 连接为 0；唯一 QUEUED Run 是上述固定目标。旧 Backend 进程树 PID 27976/33852 完全退出后，使用无 reload 的隐藏 Uvicorn 启动新进程树 PID 9500/38960；8000 监听 PID 38960，项目健康指纹通过。Worker、Frontend、Demo 与容器均未重启。旧 Backend ownership 快照保存在 `.codex-validation/p5e-services/owned-processes-history-backend-restart-20260909T145215Z.json`。

主控审查并明确放行后，`deploy/p5e_redrive_run_dead_letter.py` 仅对固定 Run `run_15c59b0b27964dd8a6fec2fcca840f9c` 执行了一次精确恢复：

- inspect 确认 Run=`QUEUED`、Outbox=`PUBLISHED`、未 claim，数据库与死信的完整 envelope、body message ID、AMQP property message ID、Runner ID 和 WEB routing 全部一致。
- 固定 message ID 为 `msg_ebb9acdd279149aaaf8cb130f5e01127`；原死信 `reason=rejected`、count=1、时间为 2026-09-09T14:21:38Z。
- 真正 publish 前再次读取数据库并通过全部门禁；保持原 body 与 message ID，经 mandatory publish confirm 成功后才向目标死信发送 ACK。
- journal 最终状态为 `source_acked`；没有重试或确认歧义。主队列恢复后为 0，DLQ 从 2 减为 1，非目标死信完整保留。
- 固定 Run 被真实 claim 并终结为 `FAILED / WEB_LOCATOR_NOT_FOUND`；CaseRun 28 为 `FAILED`，StepRun 58（`action_1`）为 `FAILED / WEB_LOCATOR_NOT_FOUND`。
- Web trace 记录 `css`、priority 1、`NOT_FOUND`；healing context 为 `ALL_LOCATORS_FAILED`、element version 2、页面 `http://127.0.0.1:8765/login`、3 个经过安全裁剪的 DOM candidate 结构。
- 生成 4 件 Evidence：1 个 `CONSOLE_ERROR`、2 个 `SCREENSHOT`、1 个 `WEB_SUMMARY`。
- WEB slot 已回补为 `1/1`，关联 AI call 数为 0，唯一 Worker PID 58120/59460 持续运行。

恢复工具默认仅 inspect，执行模式还包含 20 条死信硬上限、连接/阻塞/心跳超时、发布前二次数据库核验，以及 `intent_written`、`publish_confirmed`、`source_acked` journal。若 publish 或 ACK 发生不确定结果，journal 会阻止盲目重复投递并如实记录已尝试/已确认/ACK 已发送状态。
