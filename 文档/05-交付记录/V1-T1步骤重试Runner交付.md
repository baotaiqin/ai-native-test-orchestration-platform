# V1-T1 步骤重试 Runner 交付

## 任务包

- 编号 / 修订：`V1-T1 / r1`
- 日期：2026-09-10
- 状态：**通过**
- 实施范围：Runner 磁盘代码与隔离测试
- 服务加载：未授权、未执行
- 机器可读结果：`.codex-validation/v1-t1/result.json`

## 实施结论

Runner 的 API Step 自动重试策略已从原 `0～3` 收口为严格 `0/1`：

- `0`：不自动重试，最多 1 次目标请求；
- `1`：首次请求之外最多再请求 1 次，总尝试最多 2 次；
- `bool`、负数、`2`、`3` 及更大值全部明确拒绝，不做静默 clamp；
- 超范围计划在目标请求之前拒绝，不会先执行部分请求再失败；
- `retry_count` 仍按真正开始的额外尝试计数，合法新执行最大为 `1`；
- 已有取消、Force Stop、退避取消、Run 总预算与默认断言不重试语义保持。

## 代码实现

### `runner/runner/executors/api.py`

- 增加固定边界 `MAX_STEP_RETRIES = 1`。
- `RetryPolicy.__post_init__` 校验全部构造入口，手工构造
  `RetryPolicy(max_retries=2/3)` 不能绕过 V1 边界。
- 增加 `validate_retry_policy`，可在执行入口重新校验已反序列化或被异常篡改的
  frozen value object。
- `_parse_retry_policy` 只接受 `max_retries` 为整数 `0/1`；错误信息固定且不包含
  plan 内容。
- `ApiExecutor.execute` 在构造目标请求参数和发起 HTTP 前再次校验策略，作为最终
  fail-closed 防线。

### `runner/runner/consumer.py`

- Consumer 在取得 execution plan 后、调用 `execution_start` 和目标 Executor 前校验
  `RetryPolicy`；非法策略直接 `requeue=false` reject。
- `_execute_with_retries` 入口保留第二道防线；即使内部对象在构造后被异常篡改，
  也不会发起目标请求。
- 原 retry loop、Run deadline、控制状态检查、completion 与 ACK/claim 协议未改写；
  合法 `max_retries=1` 时只会进入一次额外尝试。

未修改 `runner/runner/protocol.py` 与 `runner/runner/scenario.py`。

## 真实回环 HTTP 请求计数

新增 `runner/tests/test_v1_step_retry_limit.py`，每项使用随机回环端口上的独立
`ThreadingHTTPServer` 和真实 `ApiExecutor/httpx`；测试结束后 shutdown、close 并
join 自有线程。

| 场景 | 最终目标请求数 | 实际 retry_count | 结果 |
| --- | ---: | ---: | --- |
| 持续断开 TCP、不返回 HTTP | 2 | 1 | 第二次失败后停止 |
| 持续超过 request timeout | 2 | 1 | `TIMEOUT`，不发起第三次 |
| 首次 503、第二次 200 | 2 | 1 | 第二次成功 |
| 200 但正文供 Backend 默认断言判失败 | 1 | 0 | Runner 不因断言语义重试 |
| 首次失败后立即取消 | 1 | 0 | `CANCELLED` |
| 首次失败、退避结束前取消 | 1 | 0 | `CANCELLED` |
| 首次 503 后 Run 总预算在退避中耗尽 | 1 | 0 | `TOTAL_TIMEOUT`，无第二次请求 |
| `max_retries = true/-1/2/3` | 0 | 不适用 | 请求前 `ProtocolError` |

此外新增异常篡改测试：先合法构造 `RetryPolicy(1)`，再以测试专用方式将其改为
`2`。Consumer 在 plan 后、start/target 前 reject；ApiExecutor 独立入口也在 HTTP
前拒绝，两条路径的目标调用计数均为 `0`。

## 原测试调整与兼容边界

- 原允许 `max_retries=2/3` 的 retry loop 测试没有删除：
  - “超时三次耗尽”调整为“两次耗尽”，`retry_count` 从 `2` 改为 `1`；
  - “持续网络错误三次”调整为“两次”，仍验证最后一次失败及实际计数；
  - 只验证非重试错误/取消的用例改用合法 `max_retries=1`，原行为断言保留。
- execution plan 显式合法策略测试改为 `1`；非法策略参数组新增 `bool/-1/2/3`。
- `RunnerClient.execution_complete` 的 `retry_count=2` 既有精确 payload/response
  测试保留并通过；本包没有收窄历史/既有结果计数字段，也没有改写历史 DSL、Run
  或报告。
- Scenario 显式 `RETRY_ONCE` 既有用例继续通过：HTTP 503、目标网络错误、目标超时
  仍最多两次；Scenario 断言失败仍不重试。
- 控制面 HTTP 重连、RabbitMQ 重连、completion 幂等重送、Evidence 上传次数以及
  ACK/claim 安全协议均不属于 Step retry，本包未修改。
- 尚未实现的登录刷新、自愈、MCP retry 没有在本包提前增加行为。

## 修改清单

产品代码：

- `runner/runner/executors/api.py`
- `runner/runner/consumer.py`

测试：

- `runner/tests/test_executor.py`
- `runner/tests/test_protocol.py`
- `runner/tests/test_consumer.py`
- `runner/tests/test_v1_step_retry_limit.py`（新增）

验收证据与报告：

- `.codex-validation/v1-t1/result.json`（新增）
- `文档/05-交付记录/V1-T1步骤重试Runner交付.md`（本文件）

未修改 Backend、Frontend、部署配置、共享服务或全局状态文档；未使用 Git、未调用
AI、未创建真实业务 Run、未重启正式 Worker 或中间件。

## 测试命令与结果

实施前相关基线：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  runner/tests/test_protocol.py runner/tests/test_consumer.py `
  runner/tests/test_scenario_c21.py runner/tests/test_scenario_c23.py -q
```

结果：全部通过。

聚焦 V1 契约、历史计数兼容与 Scenario `RETRY_ONCE`：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  runner/tests/test_v1_step_retry_limit.py `
  runner/tests/test_executor.py::test_retry_policy_constructor_rejects_non_v1_retry_limits `
  runner/tests/test_executor.py::test_api_executor_revalidates_tampered_policy_before_target_request `
  runner/tests/test_consumer.py::test_tampered_retry_policy_is_rejected_before_start_or_target_request `
  runner/tests/test_protocol.py::test_execution_plan_parses_explicit_retry_policy `
  runner/tests/test_protocol.py::test_execution_plan_rejects_invalid_retry_policy `
  runner/tests/test_protocol.py::test_execution_start_and_complete_use_exact_payloads `
  runner/tests/test_scenario_c21.py::test_scenario_assertion_failure_is_safe_and_not_retryable `
  runner/tests/test_scenario_c21.py::test_scenario_http_5xx_retries_once_and_aggregates_trace `
  runner/tests/test_scenario_c21.py::test_scenario_http_target_failure_is_safe_and_retries_once -q
```

结果：`35 passed`。

Runner 全量：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest runner/tests -q
```

结果：收集 `358` 项，`356 passed, 2 skipped`，退出码 `0`。

静态检查：

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check runner/runner runner/tests
```

结果：`All checks passed!`

## SHA-256

- `runner/runner/executors/api.py`  
  `8D68B72661A3EDEC2E46B90B64C5E0054D349E27CAF8A9A79A27F1C71968F9E8`
- `runner/runner/consumer.py`  
  `4E6CCFFA03B698B371F71D344044EC35C167BA962E97FD700BB85248D32821AC`
- `runner/tests/test_executor.py`  
  `F19972C06D75D52810F9A78C28A2E9E0B4BD067E8A6242153FBD04BABCC0558B`
- `runner/tests/test_protocol.py`  
  `3EFD6903874C6D8B0F0C0C2EEC853F8FA042447A9FD5B66B3AC620B8C815DF03`
- `runner/tests/test_consumer.py`  
  `3E4F9F6A115EBF96FC8B705F42C098BDA08CEA66B4904F78D91BF8408DAFABEF`
- `runner/tests/test_v1_step_retry_limit.py`  
  `3AD71F10015DB913434A39A4E6F4EE752BA0CF62C280BA78DFACCA6240550298`
- `.codex-validation/v1-t1/result.json`  
  `007B8DFB101D28C98BE4E83811C9451A9F2787531748BBE70D00B034558BB10E`

## 最终结论

V1-T1 / r1 Runner 实现满足“Step 自动重试最多一次”的统一契约。合法网络错误、
目标超时和 HTTP 5xx 最多产生两个真实目标请求；取消、退避取消、总预算耗尽和默认
断言均不会额外发起请求；超范围或异常构造策略在目标请求前明确拒绝。历史计数读取
兼容、Scenario `RETRY_ONCE` 以及非 Step 传输重试边界保持不变。
