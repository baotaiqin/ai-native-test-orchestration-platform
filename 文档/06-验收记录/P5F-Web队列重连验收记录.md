# P5F Web 队列重连验收记录

## 任务包

- 编号 / 修订：`P5F-Q1 / r1`
- 执行时间：2026-09-10（Asia/Shanghai）
- 结论：**通过**
- 产品代码改动：无
- 新增独立验收入口：`deploy/p5f_web_broker_acceptance.py`
- 最终机器证据：`.codex-validation/p5f-web-broker/result.json`
- 最终资源账本：`.codex-validation/p5f-web-broker/ownership.json`

## 验收边界

本轮使用真实 `PikaBroker`、`RunnerWorker`、`RunnerTaskConsumer`、
`RunnerClient`、spawn 隔离进程、Playwright 与本机 Chrome。RabbitMQ 使用本轮
唯一命名临时容器，端口只绑定随机 `127.0.0.1` 端口，RabbitMQ 数据目录使用
容器内 tmpfs，无 Docker volume、无宿主目录挂载。

后端部分是随机回环端口上的受控内存 HTTP 协议夹具，只用于严格响应 Runner
claim / plan / start / cancellation / evidence / complete 协议；它不是正式 Backend
或数据库集成验收。测试站点同样只监听随机回环端口。未访问共享 RabbitMQ、正式
Backend、共享队列、真实 AI 服务或共享验收资产，未注册第二个正式 Runner。

## 最终运行资源

- 标记：`20260909172557-76d1c20b`
- 容器：`p5f-q1-20260909172557-76d1c20b`
- 容器 ID：`7b588581bc7d40b007dc40e8eb3083a9bb0959ec7bf4baef148daf4c76ad7aef`
- 镜像：`rabbitmq:4.1-management-alpine`
- 临时 AMQP 端口：`127.0.0.1:61103`
- Mounts：`[]`
- Runner：`p5fq1_80606344609c`
- Runner queue：`ai_test.runner.p5fq1_80606344609c.v1`

RabbitMQ 用户名和密码均为运行时随机生成，只保存在进程内；报告、结果、账本与
命令输出均未保存认证段或凭证。

## 场景与结果

### 1. 空闲断线、恢复后执行 Web

- 容器内执行真实 `rabbitmqctl close_all_connections`，命令成功；断线前仅有
  1 条 Worker AMQP connection。
- Worker 旧 connection 对象被替换，并重新声明 topology。
- 恢复后发布 `msg-idle-38f3c4db`，broker 首次投递
  `redelivered=false`。
- 后端计数：claim `1`、plan `1`、start `1`、complete `1`。
- 本地站点动作计数：`1`。
- Web 终态：`SUCCESS`；原 delivery ACK 成功。
- Evidence：`SCREENSHOT`、`CONSOLE_ERROR`、`WEB_SUMMARY`、
  `PLAYWRIGHT_TRACE` 各 1 份。

### 2. Web 执行中断线与 ACK 不确定重投递

- Chrome 已发起 `p5fq1-flight-69b7081f/start` 后，站点夹具暂缓响应，确保
  Web spawn / Chrome 正在真实执行；此时观察到 1 个 spawn 根进程和 8 个归属于
  该根进程的 Chrome 后代。
- 在页面动作尚未完成时，容器内再次执行真实
  `rabbitmqctl close_all_connections`；断线前仅有 1 条 Worker connection。
- 随后释放站点响应，浏览器只访问一次 `acted`，动作计数保持 `1`，Web complete
  也只提交 `1` 次并返回 `SUCCESS`。
- 原 channel 的 ACK 实际失败，Worker 重建 connection/topology；同一个 RabbitMQ
  message ID `msg-flight-acae57ec` 第二次投递时由 broker 明确标记
  `redelivered=true`。
- 后端终态幂等边界拒绝第二次 claim：claim 总数 `2`，但 plan / start / complete
  均保持 `1`。Runner 对该终态 delivery 执行 `requeue=false` reject，没有再次启动
  浏览器。
- Runner queue 最终为 `0`；隔离 broker 的 dead queue 为 `1`，对应终态重投递。

`connection_count_after_close_probe` 保留为 `1`，它是服务端发出
`CONNECTION_FORCED` 后、客户端尚未处理关闭帧时的即时 CLI 快照，不作为成功判据。
真实关闭由同一次容器内关闭命令成功、原 channel ACK 失败、connection 重建、
topology 重声明以及 broker `redelivered=true` 的连续证据共同确认。

## Worker 汇总

- `rabbitmq_reconnect_count = 2`
- `ack_count = 1`
- `rejected_count = 1`
- `requeued_count = 0`
- `heartbeat_count = 8`
- `heartbeat_transient_failures = 0`
- `stop_reason = STOP_REQUESTED`
- topology 声明次数：`3`（初始 + 两次重连）

## 资源清理

- 最终容器由脚本按“名称 + ID”双重核对后执行 `docker rm -f -v`，账本
  `container_removed = true`；随后再次执行精确 `docker inspect`，返回
  `no such object`。
- 最终容器 Mounts 为 `[]`；RabbitMQ 数据目录使用 tmpfs，没有创建 volume。
- 2 个 Web spawn 子进程均 `exitcode = 0` 且已退出。
- 8 个归属明确的 Chrome 后代均已退出；相对运行前基线没有新增残留 Chrome PID。
- `ai-test-runner-web-*` 与 Playwright profile 临时目录无残留。
- Slot 最终为 `WEB = 1`，其他 slot 与初始值一致。
- 没有使用按进程名批量终止 Chrome，也没有清理历史容器、历史队列或历史卷。

验收脚本调试阶段曾有 4 次在“CLI 即时连接计数”观测门禁处提前结束；每次均由
`finally` 精确删除本轮容器并回收 Runner/Chrome/临时目录/Slot。首轮发现镜像自带
`VOLUME /var/lib/rabbitmq` 会隐式创建匿名卷
`4241f848498548655a22adb2398dd3ae6cb2b1658f8bfa55cbeaafc99ca50bb8`；该卷已按账本
精确 ID 删除，并再次 inspect 确认 `no such volume`。脚本随后改用 tmpfs，余下轮次
均为 Mounts `[]`。四个中止容器与最终容器均已逐一用精确名称复核不存在。

## 执行命令与结果

相关隔离回归（验收前、交付前各执行一次，均 `4 passed`）：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  runner/tests/test_worker.py::test_worker_reconnects_after_transient_rabbitmq_failure_and_recovers `
  runner/tests/test_worker.py::test_worker_reconnects_after_pika_broker_restart_with_default_classifier `
  runner/tests/test_web.py::test_web_consumer_claims_executes_completes_before_ack `
  runner/tests/test_web.py::test_web_evidence_completion_response_loss_requeues_without_browser_restart -q
```

真实 RabbitMQ + Chrome 验收（最终退出码 `0`，`status = PASSED`）：

```powershell
& '.\.venv\Scripts\python.exe' deploy/p5f_web_broker_acceptance.py
```

静态检查（通过）：

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check `
  deploy/p5f_web_broker_acceptance.py `
  runner/runner/worker.py runner/runner/consumer.py runner/runner/broker.py
```

本轮没有修改 Runner 产品代码，因此按任务包约定未重复执行全量 Runner pytest；
采用相关隔离覆盖、真实 broker/Chrome 验收和目标文件 Ruff 作为交付门禁。

## SHA-256

- `deploy/p5f_web_broker_acceptance.py`  
  `27D690AC71DBC6893C12792FDABA471DFF9AD245470A798179B97720992185A3`
- `.codex-validation/p5f-web-broker/result.json`  
  `CA07AB3A48241E1664BFEEFFA28B77C83C75BCE6AC91D8E770625FDC537C7DBF`
- `.codex-validation/p5f-web-broker/ownership.json`  
  `F5B7AE240BE1C96C32DB691CE096F6CC51A28A9A767AD6B0CE8788CF5904C64F`

## 验收结论

P5F-Q1 / r1 的两条真实 RabbitMQ 断线链路均通过。空闲断线后 Worker 可重连并执行
真实 Web；执行中断线造成完成后 ACK 不确定时，broker 对同一 message ID 进行真实
重投递，Backend 终态 claim 防线使 Runner 不再启动浏览器，动作、plan、start、
complete 均没有重复。所有本轮资源已收口，无共享服务副作用。
