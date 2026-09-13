# V1-W7 隐藏与可点击断言 Runner 交付

## 任务包

- 编号 / 修订：`V1-W7 / r2`
- 日期：2026-09-10
- 状态：**通过**
- 范围：Runner `ASSERT_HIDDEN`、`ASSERT_CLICKABLE` 与 Playwright 依赖下限对齐
- 机器可读证据：`.codex-validation/v1-w7/result.json`
- r1 原样归档：`.codex-validation/v1-w7/result-r1.json`
- 正式服务加载：未授权、未执行

r1 已完成源码、专项测试、兼容回归、Runner 全量、Ruff 和机器证据，但因额度限制在
最终报告与 final 前中断，不能把该轮次状态冒充完整交付。主控恢复核查确认没有遗留
验证进程、五项源码/测试指纹与 r1 证据一致，并额外完成 W7 主控 3 项验证。r2 原样
归档 r1 机器证据，修正依赖元数据并补齐本报告。

## 实施结论

继续使用 `schema_version=1` 和既有 `type/timeout_ms/locator/expected` 四字段结构，新增：

- `ASSERT_HIDDEN`：目标不存在或所有候选匹配元素均不可见时通过；任一候选仍有可见
  元素时等待至节点期限。所有 Locator 是同一目标的替代描述，必须完成全候选聚合，
  因此“缺失候选 + 可见候选”不会被第一个缺失项误判为通过。
- `ASSERT_CLICKABLE`：使用 Playwright `Locator.click(trial=True)` 检查可见、稳定、启用
  和未被覆盖等真实 actionability；短周期试探在同一节点期限内公平回退候选。

两者都要求有效 locator、expected 必须为 null，timeout_ms 继续是 `100..600000` 的
严格整数。bool、未知 type、额外字段、缺 locator 和非空 expected 均被协议拒绝。
没有新增模型或 payload 字段。

## HIDDEN 准确语义

- 单候选不存在：立即成功，trace 保留 `NOT_FOUND`，不创建 healing context。
- 单候选不可见：成功，trace 为 `SUCCESS`。
- 候选可见：持续检查，若按期隐藏则成功；期限内始终可见则按原预算超时。
- 多候选缺失 + 隐藏：成功并依序记录 `NOT_FOUND/SUCCESS`。
- 多候选缺失 + 可见：不得通过，依序保留 `NOT_FOUND/ACTION_FAILED`。
- 每个候选可能匹配多个节点；通过 `Locator.filter(visible=True)` 判断是否存在任一可见
  匹配，而不是只看第一个节点。
- 非法选择器和浏览器异常固定受控失败，不被解释为“隐藏”，不输出原始 DOM 或浏览器
  错误。

轮询到期限时保留最近一次完整的全候选审计，避免最后一个不足完整列表的轮次覆盖
已有状态。

## CLICKABLE 准确语义与副作用

试探只传 `trial=True` 与受节点预算限制的 timeout；未传 force、modifiers 或业务点击
参数，也未调用 dispatch_event、fill。试探允许 Playwright 为 actionability 检查滚动，
但不会实际触发业务交互。

真实 Chrome 验证：普通目标、迟到目标及“缺失候选 + 可点击候选”通过；disabled、
aria-disabled、遮挡和隐藏目标均不通过。页面计数保持：click=0、input=0、submit=0、
navigation=0。最后的短 attached probe 不会把已经定位但不可操作的候选从
`ACTION_FAILED` 降级为 `NOT_FOUND`，避免错误产生 locator healing。

## 预算、取消、隐私与清理

- 节点预算实际归类 `WEB_NODE_TIMEOUT`，Run 预算实际归类 `WEB_TOTAL_TIMEOUT`；没有
  创建新浏览器或延长执行期限。
- 隔离子进程在迟到 clickable 检查期间收到取消；当前节点收敛后，下一节点前返回
  `CANCELLED / CANCEL_REQUESTED`。
- 自有 Chrome 后代、Playwright profile 和临时 Evidence 目录残留均为 0；未关闭用户
  浏览器。
- 合成随机敏感标题先被成功观察，随后新 `ASSERT_CLICKABLE` 的锁定 Locator 失败并
  生成 healing context。最终 result、Evidence 和 healing 候选均不含原值，标题为
  `[REDACTED]`，安全 id/data-testid 与 `element_version_id=701` 保留。
- W4/r4 的动作与断言 planned/observed 敏感值传递、先脱敏后截断和溢出策略未改变。

## r2 Playwright 依赖下限

执行器使用的 `Locator.filter(visible=True)` 中 `visible` 选项自 Playwright 1.51 加入，
而 r1 前的 Runner 元数据声明为 `playwright>=1.50,<2`。本机 1.62 测试通过不能证明
1.50 兼容，因此 r2 将唯一 Runner 声明修正为：

```toml
"playwright>=1.51,<2"
```

依据为 [Playwright Python Locator.filter visible](https://playwright.dev/python/docs/api/class-locator#locator-filter-option-visible)。
仓库中没有第二份 Runner requirements/lock 声明需要同步，也未修改虚拟环境、浏览器
安装或其他依赖。

使用 `tomllib` 解析项目元数据，并以 `packaging.requirements.Requirement` / 标准
SpecifierSet 验证全部 4 项依赖：最终 specifier 为 `<2,>=1.51`；1.50 不允许、1.51
允许、本机已测 1.62 允许、2.0 不允许，退出码为 0。

## 测试与复用证据

r1 最终专项及真实 Chrome：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  runner/tests/test_v1_web_hidden_clickable.py -q
```

结果：`10 passed in 15.11s`。

W4 专项：`33 passed in 27.68s`；主控 W4：`10 passed in 25.25s`；W1 13 动作专项：
`72 passed in 27.03s`；主控 W1：`5 passed in 15.46s`。

Runner 全量：收集 473 项，`471 passed, 2 skipped in 125.42s`。完整 Ruff：
`All checks passed!`。主控恢复审计另有 W7 `3 passed in 9.52s`，记录在
`.codex-validation/20260910-worker-recovery-review.json`。

r2 只修改 `runner/pyproject.toml` 与本包证据/报告；协议、执行器、models 和两份测试
的五项指纹与 r1 完全相同，故按任务包授权复用上述测试证据，不重复 Runner 全量。
r2 修改后再次执行完整 Ruff，仍为 `All checks passed!`。

## 修改与指纹

- `runner/pyproject.toml`  
  `0F9CDF53E6454267AC2D7185197DDB9916730619B0EA508E498ACF829F23EE08`
- `runner/runner/protocol.py`  
  `1BA7F9A84BB21885A4FE7DE2BAFD875757D93170099394386836292268E67E6E`
- `runner/runner/executors/web.py`  
  `15EAA09DEB6E35E3E7ABFAB8D19BB2427D0410294526A73981C1D1D2AFD2DB7B`
- `runner/runner/models.py`（未修改）  
  `F33405453276D19BAC96748BBF69AA93DF55BFCC48B75E40A2DBD67C0880A16A`
- `runner/tests/test_v1_web_assertions.py`  
  `18AC658F40A217585BEA57E200082A8EB37BFFD23B3D275B3E669B9927BDEDE6`
- `runner/tests/test_v1_web_hidden_clickable.py`  
  `432519139060CB472D25B3876244583A41B3164B1CD3058896F8F5581E366381`
- `.codex-validation/v1-w7/result-r1.json`（原样归档）  
  `9CE4359A97F15C2070996114393AC467AACE32E1BF7CE3BD0A9BA922507F4E1B`

## 安全与最终结论

未启动或访问正式 Backend、Frontend、Worker、RabbitMQ、数据库或业务 Run；未调用
真实 AI、Git、Claude、内部 Agent 或跨任务消息工具。

`V1-W7/r2` 的 Runner 范围已完成。隐藏与可点击断言的协议、真实语义、多候选、
actionability、预算、取消、隐私和资源清理均通过；依赖下限已与实际 API 要求对齐。
本交付仅代表 Runner 扩展，不代表 Backend/Frontend 接入或完整 V1 已完成。
