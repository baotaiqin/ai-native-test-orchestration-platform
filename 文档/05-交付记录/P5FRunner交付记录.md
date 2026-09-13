# P5-F Runner 交付记录

> 交付日期：2026-09-09  
> 范围：Web 执行完成后的 Evidence 异常终态收敛  
> 执行任务：AI-Test-Runner_sol

## 1. 本轮实现

- Web Executor 已返回完整真实 trace 后，如果 Evidence 本地校验失败、上传响应协议错误、Runner 处理错误或有限网络重试耗尽，Runner 会尝试提交固定的安全终态：`FAILED / WEB_EVIDENCE_ERROR / Web 执行证据处理失败`。
- Evidence 校验、哈希、响应字段及关联检查保持严格，不因终态收敛而放宽。
- 特殊失败终态只复用 `WEB_RESULT` 返回且完整覆盖固定节点的真实 trace；允许真实步骤全部成功而 CaseRun/Run 因平台 Evidence 失败变为 FAILED，不伪造失败步骤。
- `TOTAL_TIMEOUT`、父进程 `EXECUTION_ERROR` 或任何无法提供完整真实 trace 的路径，不使用 `_web_terminal_traces` 合成 Evidence 失败 trace。此时保持有限 ACK/NACK/拒绝处置，由后端执行预算超期扫描兜底。
- 取消已先观察到时继续优先提交取消终态；认证失败仍拒绝消息并停止消费，不绕过 Runner credential。
- completion 响应无法确认时不虚报完成：传输失败 NACK 重投，RUNNING Run 重投 claim 收到 409 后拒绝消息，不重新启动浏览器。
- 所有返回路径继续由 `TemporaryDirectory` 和 Slot lease 的 `finally` 边界清理临时 Evidence 文件并释放 WEB Slot。

## 2. 修改文件

- `runner/runner/consumer.py`
- `runner/tests/test_web.py`

未修改 Backend、Frontend、部署文件、正式业务库或其他主控文档。

## 3. 回归覆盖

新增或扩展的回归覆盖：

- Evidence 本地 ValidationError；
- Evidence 永久 ProtocolError、RunnerError；
- multipart 有限网络重试成功与耗尽；
- Evidence 上传及失败 completion 的认证失败；
- Evidence 失败时保留完整真实成功 trace；
- trace 不完整时不构造或提交虚假 completion；
- `TOTAL_TIMEOUT`、父进程 `EXECUTION_ERROR` 没有真实 child trace 时不合成 Evidence 失败 trace；
- completion 响应丢失后的 NACK、重投 claim 409、浏览器不重执行；
- 取消优先；
- 隔离进程关闭、Evidence 临时目录清理和 WEB Slot 恢复。

## 4. 实际验证结果

验证时将 `TEMP`、`TMP` 指向独占目录 `.codex-validation/p5f-runner-20260909`，并设置 `PYTHONDONTWRITEBYTECODE=1`。

```text
.venv/Scripts/python.exe -m ruff check --no-cache runner/runner/consumer.py runner/tests/test_web.py
结果：通过

.venv/Scripts/python.exe -m pytest runner/tests/test_web.py -q -p no:cacheprovider
结果：44 passed

.venv/Scripts/python.exe -m pytest runner/tests -q -p no:cacheprovider
结果：323 passed，2 个既有环境条件跳过

.venv/Scripts/python.exe -m ruff check --no-cache runner/runner runner/tests
结果：通过
```

Runner 全量包含真实 Chrome 本机回环 smoke。未启动或停止正式 Runner Worker，未调用外部 AI，未访问或修改业务数据库。

## 5. 后续验收

Runner 范围内实现与自动化回归已完成。P5-F 仍需由主控结合 Backend 的 Evidence 平台失败契约和执行预算超期收敛完成跨模块审查及真实链路验收。
