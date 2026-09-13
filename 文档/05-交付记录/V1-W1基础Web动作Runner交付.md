# V1-W1 基础 Web 动作 Runner 交付

## 任务包

- 编号 / 修订：`V1-W1 / r1`
- 日期：2026-09-10
- 状态：**通过**
- 范围：Runner Web 协议、执行器、相关测试、隔离真实 Chrome 验收
- 机器可读证据：`.codex-validation/v1-w1/result.json`
- 服务加载：未授权、未执行

## 实施结论

在既有 `schema_version=1`、既有 action 七字段结构不变的前提下，Runner 已加入
13 个固定 Web 动作：

`RELOAD`、`BACK`、`FORWARD`、`DOUBLE_CLICK`、`RIGHT_CLICK`、`CLEAR`、
`HOVER`、`CHECK`、`UNCHECK`、`RADIO`、`ENTER`、`TAB`、
`WAIT_NETWORK_IDLE`。

未增加 payload 字段，也未开放任意 Playwright 方法调用。新元素动作统一复用原
Locator 候选、priority、锁定 `element_version_id`、共享节点预算和安全 trace；页面
动作只映射到固定白名单方法。`ENTER`/`TAB` 使用固定按键，`RIGHT_CLICK` 使用真实
右键，`RADIO` 在执行 `check` 前以固定表达式验证目标确为 radio，普通 checkbox
会安全失败。

## 协议边界

- 9 个元素动作必须提供 locator；4 个页面动作不接受 locator。
- 13 个新动作都拒绝非空 `url`、`value`、`key`，不能借旧字段偷换语义。
- 未知 type、缺失字段、缺失 locator、非法超时及非法 failure policy 保持 fail-closed。
- 原有 7 个动作 `GOTO/FILL/CLICK/SELECT/PRESS/WAIT_ELEMENT/WAIT_URL` 与 3 个断言
  `ASSERT_VISIBLE/ASSERT_TEXT/ASSERT_URL` 的解析兼容测试全部通过。
- `timeout_ms` 继续限定在 `100..600000`，失败是否继续仍由原 `STOP/CONTINUE`
  决定；没有加入动作整体重试。

## 执行与错误分类

- `RELOAD/BACK/FORWARD` 分别固定映射 `reload/go_back/go_forward`。
- `DOUBLE_CLICK/RIGHT_CLICK/CLEAR/HOVER/CHECK/UNCHECK` 固定映射对应 Locator 操作。
- `RADIO` 先验证原生 input type，再执行幂等 `check`；错误目标归一化为
  `WEB_ACTION_FAILED`，不回传浏览器异常正文。
- `ENTER/TAB` 固定执行 `press("Enter")` / `press("Tab")`，payload 无法改键。
- `WAIT_NETWORK_IDLE` 固定执行 `wait_for_load_state("networkidle")`。
- 节点预算耗尽为 `WEB_NODE_TIMEOUT`；当节点实际受更短 Run 总预算限制且总预算抵达
  时归类为 `WEB_TOTAL_TIMEOUT`。10ms 仅用于吸收传给 Playwright 的整数毫秒截断，
  不扩大实际预算。
- 原 Locator fallback、失败 trace、healing 条件、截图遮罩、Console/Trace 脱敏、
  Evidence manifest、取消和 Force Stop 路径未改写。

## 真实 Chrome 验收

环境：Google Chrome `152.0.7977.77`，Playwright `1.62.0`。所有页面均由测试进程
在随机 `127.0.0.1` 端口提供，不访问外网、不复用用户浏览器。

| 验收项 | 实际观测 | 结果 |
| --- | --- | --- |
| RELOAD | 初次导航 + 一次 reload，请求总数严格为 2 | 通过 |
| BACK / FORWARD | 分别回到 `/a`、前进至 `/b`，由真实 URL 断言确认 | 通过 |
| DOUBLE_CLICK | 页面 `dblclick` 事件发生 | 通过 |
| RIGHT_CLICK | 页面 `contextmenu` 事件发生 | 通过 |
| CLEAR / HOVER | 空值 input 事件、`mouseenter` 事件发生 | 通过 |
| CHECK | 连续执行两次，最终 checked=true，change 次数=1 | 通过 |
| UNCHECK | 连续执行两次，最终 checked=false，change 次数=1 | 通过 |
| RADIO | 连续执行两次，最终 checked=true，change 次数=1 | 通过 |
| RADIO 错误目标 | checkbox 被拒绝，错误为 `WEB_ACTION_FAILED` | 通过 |
| ENTER / TAB | Enter keydown 发生；焦点转移到下一控件 | 通过 |
| WAIT_NETWORK_IDLE | 静态页成功；持续挂起请求产生真实超时 | 通过 |
| Locator fallback | priority 1=`NOT_FOUND`，priority 2=`SUCCESS` | 通过 |
| CONTINUE | 首动作失败后后续动作与断言成功，最终仍为 FAILED | 通过 |
| 隐私 | 随机 secret 不存在于结果及任何 Evidence 原始字节，Trace 审计通过 | 通过 |

## 隔离、取消和清理

隔离子进程在真实 `RELOAD` 第二个 HTTP 请求进行中接收取消。放行自有回环请求后：

- 结果为 `CANCELLED / CANCEL_REQUESTED`；
- `/slow-reload` 请求严格为 2，已经发生的 reload 没有重复执行；
- 记录并检查的自有 Chrome 后代进程残留数为 0；
- 本次新建 Playwright profile 目录残留数为 0；
- 只检查隔离根进程后代和运行前后 profile 差集，没有终止用户浏览器。

现有真实 Force Stop、槽位释放、Evidence 临时目录清理用例也随 Runner 全量回归通过。

## 修改清单

产品代码：

- `runner/runner/protocol.py`
- `runner/runner/executors/web.py`

测试：

- `runner/tests/test_v1_web_actions.py`（新增）
- `runner/tests/test_web.py`（把原伪节点超时场景改为节点预算确实短于总预算）

证据与报告：

- `.codex-validation/v1-w1/result.json`（新增）
- `文档/05-交付记录/V1-W1基础Web动作Runner交付.md`（本文件）

未修改 `runner/runner/models.py`，现有模型已能承载全部新动作；未修改 Backend、
Frontend、迁移、共享演示或全局状态文档。

## 测试结果

专项协议矩阵与真实 Chrome 验收：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  runner/tests/test_v1_web_actions.py -q
```

结果：`72 passed in 17.10s`。

最终源码 Runner 全量：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' runner/tests -q
```

结果：收集 `430` 项，`428 passed, 2 skipped in 98.08s`，退出码 `0`。

静态检查：

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check runner/runner runner/tests
```

结果：`All checks passed!`

## SHA-256

- `runner/runner/protocol.py`  
  `1941AF58B67B8EC2483E16274B24B04C73908DC7ED83761D1F5C11AA938032F4`
- `runner/runner/executors/web.py`  
  `9CF7C6C2E32A923F34711700EAEE12CC747855CFF8172C9B1CA3C2A807A67DDC`
- `runner/tests/test_web.py`  
  `46CC5138139DB69FCE0CC371364141101F81A2F6C2B56D25B09CA03EA860F1E4`
- `runner/tests/test_v1_web_actions.py`  
  `039BADAA97B74A6805FD4CE784D673112BD8CE766B964EC6737B431062D8ED48`

## 安全与副作用

未启动正式 Backend、Frontend、Worker、RabbitMQ 或数据库；未创建/注册/运行真实
业务数据；未调用 AI；未使用 Git、Claude、内部 Agent 或跨任务消息工具。真实验收
仅使用随机回环 HTTP、隔离子进程及其自有 Chrome/profile，均已清理。

## 最终结论

`V1-W1/r1` 的 Runner 范围已完成。13 个基础 Web 动作在协议、执行、错误分类、
Locator/预算、隐私证据和隔离取消路径中全部接入，并由真实 Chrome 逐项证明实际
效果；现有 7 个动作、3 个断言及 Runner 全量回归保持通过。本交付不代表 Backend
DSL、Frontend 接入或完整 V1 已完成。
