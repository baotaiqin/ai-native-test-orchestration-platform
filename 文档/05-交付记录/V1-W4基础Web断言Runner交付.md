# V1-W4 基础 Web 断言 Runner 交付

## 任务包

- 编号 / 修订：`V1-W4 / r4`
- 日期：2026-09-10
- 状态：**通过**
- 范围：Runner Web 断言执行器、动作/断言自愈脱敏与相关测试
- 机器可读证据：`.codex-validation/v1-w4/result.json`
- r1 归档证据：`.codex-validation/v1-w4/result-r1.json`
- r2 归档证据：`.codex-validation/v1-w4/result-r2.json`
- r3 归档证据：`.codex-validation/v1-w4/result-r3.json`
- 服务加载：未授权、未执行

## 实施结论

在既有 `schema_version=1` 和 `type/timeout_ms/locator/expected` 四字段结构不变的
前提下，Runner 已加入 5 种固定断言：

- `ASSERT_EXISTS`：Locator 至少匹配一个已附着 DOM 节点；隐藏节点也通过。
- `ASSERT_ENABLED`：使用 Playwright 原生 `is_enabled`，disabled 与 aria-disabled
  按浏览器原生状态判定。
- `ASSERT_TEXT_EQUAL`：读取 `inner_text`，不 trim，区分大小写，完整相等。
- `ASSERT_INPUT_VALUE`：使用原生 `input_value`，区分大小写，完整相等；非输入控件
  受控失败。
- `ASSERT_TITLE`：读取当前页 title 并完整相等；不使用 URL 或正文替代。

没有新增 payload 字段、模型字段、断言 retry、动作重放或表单/导航副作用，也没有
扩大到 Hidden、Clickable、Attribute、Count、Download、Network、图像或 AI 断言。

## 协议与兼容边界

- `ASSERT_EXISTS/ASSERT_ENABLED` 必须有 locator 且 expected 必须为 null。
- `ASSERT_TEXT_EQUAL/ASSERT_INPUT_VALUE` 必须有 locator，expected 必须是字符串，
  空字符串合法。
- `ASSERT_TITLE` locator 必须为 null，expected 必须是字符串，空字符串合法。
- timeout 继续为 `100..600000` 的严格整数；bool、未知 type、额外字段、缺 locator、
  缺 expected 及新类型无关参数均明确拒绝。
- expected 继续沿用模板解析和 10000 字符上限，没有用 expected JSON 偷渡参数。
- 旧 `ASSERT_TEXT` 仍为包含比较；测试用实际正文 `Hello World` 和 expected `Hello`
  证明通过。
- 旧 `ASSERT_VISIBLE/ASSERT_URL` 原载荷未收紧：兼容测试保留前者非空 expected、
  后者非空 locator 并成功解析。
- W1 的 13 个动作、原 7 个动作以及既有 3 个断言全部随回归通过。

## 执行、预算与隐私

4 个元素级新断言复用既有 `_assert_locator_candidates`，因此继续共享节点截止时间、
Locator priority、锁定 `element_version_id`、fallback trace 和 healing 边界。
`ASSERT_TITLE` 是 page 级断言，失败 trace 仍归属正确的 `assertion_n`。

断言只调用只读方法 `count/is_enabled/inner_text/input_value/title`，不会通过 click、
fill 或导航探测。失败统一输出固定 `Web 断言未通过`，实际值、期望值、模板结果及
Playwright 原始异常不进入结果。

Evidence 隐私路径在 r2 完成截断顺序修订：

- 计划中的断言 expected 在内存解析后加入 Trace 禁止值；
- 执行器读取到的 title、inner text、input value 只加入本次执行的内存脱敏集合；
- title、Console 与 Network message 最多暂存 10000 字符，先合并 late-observed
  禁止值并完整脱敏，最后才截断为对外 256 字符；
- `finish` 与异常 `abort` 共用同一 forbidden 刷新入口，不允许 fallback 写回旧前缀；
- Trace sanitize 使用同一集合，截图继续沿用 input 和 `[data-sensitive]` 遮罩；
- observed 集合上限为 100 个、单值上限为 10000 字符；超出任一上限时，title 使用
  `[REDACTED]`，并安全省略可能携带页面内容的 Screenshot、Console/Network 明细和
  Trace，仍保留不含页面内容的 Web Summary 公共状态字段；
- 集合与 overflow sentinel 不进入 wire result、日志、manifest 或报告。

真实 Chrome 同时验证成功匹配和“实际敏感 input 值与非敏感错误 expected 不同”的
失败场景。随机长 title/input/text 均不存在于结果和任一 Evidence 原始字节；正常
finish、异常 abort、长度溢出与 101 个 observed 值的数量溢出均已由真实 Chrome 验证。

### r3 自愈结果脱敏

r2 的 Evidence 文件已安全，但后续 Locator 失败生成的 `healing_context` 属于执行
结果 trace，不经过 Evidence collector。r3 将整个计划中已解析的 expected 和截至失败
节点已读取的 title/text/input 值传入自愈采集：

- page title 先按完整值脱敏，再收口到 healing 的 200 字符上限；
- DOM 属性先读取完整的最多 10000 字符值，再判断 sensitive，最后执行 200 字符约束；
- 超过 10000 字符的属性由浏览器侧整字段省略，不返回截断前缀；
- observed 集合已溢出时，仅省略 healing 的 page title 和 DOM candidates，继续保留
  `schema_version`、trigger、`element_version_id`、安全 URL、节点归属、Locator 尝试
  状态与原错误类别；
- 安全候选的 tag、id 和 data-testid 继续保留，没有全局关闭 healing。

真实跨节点计划依次成功读取随机长 title、input value 和 inner text，再让绑定
`element_version_id=201` 的 `ASSERT_EXISTS` 失败。最终 `result.to_wire()` 不含任何原值，
healing title 为 `[REDACTED]`，敏感/超长候选属性被省略，而安全 id/data-testid 与版本号
仍存在。另测尚未执行但已完成模板解析的 expected，也不会从前一失败节点候选中泄漏。

### r4 动作失败自愈脱敏

r3 已覆盖断言失败入口，但动作的 Locator 全部未命中时，`_perform_locator_action`
仍以空禁止值采集 `healing_context`。因此，后续尚未执行的 `ASSERT_TITLE.expected`
若等于当前页面标题，会从较早失败的 `action_n.healing_context.page_title` 泄漏。

r4 在 `_execute_page` 已有的单次执行上下文中，将整份计划预解析得到的敏感值和适用的
observed 值显式传入 `_perform_action`，再统一传给 14 种 Locator 动作共用的
`_perform_locator_action`。动作和断言两个 `_collect_healing_context` 调用点现均带有界
禁止值；没有修改协议、模型、错误分类、重试或动作执行顺序，也没有全局关闭 healing。

真实 Chrome 新增同一计划验证：`FILL/SELECT` 以 `CONTINUE` 失败后继续，`CLICK` 以
`STOP` 失败并令后续标题断言为 `SKIPPED`。三个 action trace 均保持
`WEB_LOCATOR_NOT_FOUND`、`action_1..3`、锁定 `element_version_id=201` 和
`NOT_FOUND` Locator 尝试；计划 FILL/SELECT 值及未执行标题 expected 均不在
`result.to_wire()`，页面标题为 `[REDACTED]`，敏感候选字段省略，安全 id/data-testid
仍保留。测试失败信息只报告每类泄漏的布尔值，不打印随机原值。

## 真实 Chrome 逐场景事实

环境：Google Chrome `152.0.7977.77`，Playwright `1.62.0`。页面全部来自测试拥有的
随机 `127.0.0.1` 端口。

| 场景 | 实际事实 | 结果 |
| --- | --- | --- |
| EXISTS 隐藏节点 | 节点 attached、`is_visible=false` | 通过 |
| EXISTS 完全缺失 | 固定 `ASSERTION_FAILED` | 通过 |
| ENABLED | 普通按钮通过；disabled 与 aria-disabled 失败 | 通过 |
| TEXT_EQUAL | `Hello World` 完整值通过 | 通过 |
| TEXT_EQUAL 仅包含 | expected=`Hello` 不通过 | 通过 |
| TEXT_EQUAL 大小写 | expected=`hello world` 不通过 | 通过 |
| INPUT_VALUE 空值 | 空 input 与空 expected 完整相等 | 通过 |
| INPUT_VALUE 非空 | `Input Value` 完整相等 | 通过 |
| INPUT_VALUE 部分值/错误目标 | 部分值和 div 目标均受控失败 | 通过 |
| TITLE | 完整标题与空标题通过；仅子串失败 | 通过 |
| 模板 | text/input/title 三类 expected 模板成功解析 | 通过 |
| Locator fallback | priority 1=`NOT_FOUND`，priority 2=`SUCCESS` | 通过 |
| 无交互副作用 | click/input/change 事件计数均为 0 | 通过 |
| 节点/总预算 | 分别归类 `WEB_NODE_TIMEOUT` / `WEB_TOTAL_TIMEOUT` | 通过 |
| page 节点归属 | title 失败 trace 为 `assertion_1` | 通过 |

## 取消、隔离与资源清理

真实隔离测试加载延迟 900ms 才附着的隐藏节点，在第一项 `ASSERT_EXISTS` 等待期间
发出外部取消；第一断言收敛后，下一断言开始前检测取消，结果为
`CANCELLED / CANCEL_REQUESTED`。

- 自有 Chrome 后代进程残留：0；
- 本次新建 Playwright profile 残留：0；
- 自有 Evidence 临时目录残留：0；
- 仅检查隔离根进程后代及运行前后 profile 差集，没有关闭用户浏览器。

## 修改清单

产品代码：

- `runner/runner/protocol.py`
- `runner/runner/executors/web.py`

测试：

- `runner/tests/test_v1_web_assertions.py`（新增）

证据与报告：

- `.codex-validation/v1-w4/result.json`（新增）
- `.codex-validation/v1-w4/result-r1.json`（r1 原结果归档）
- `.codex-validation/v1-w4/result-r2.json`（r2 原结果归档）
- `.codex-validation/v1-w4/result-r3.json`（r3 原结果按字节归档）
- `文档/05-交付记录/V1-W4基础Web断言Runner交付.md`（本文件）

`runner/runner/models.py` 指纹保持冻结值，未修改。未修改 Backend、Frontend、迁移、
Consumer、队列、部署、正式 Worker、共享 Demo、W1 证据或全局状态文档。

## 测试命令与结果

W4 专项协议、真实 Chrome、隐私、预算和取消：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  runner/tests/test_v1_web_assertions.py -q
```

结果：`33 passed in 22.46s`。

主控 W4 独立真实 Chrome 探针（r4 新增动作先失败、后续标题 expected 不泄漏后共
10 项；本任务未修改该探针）：

```powershell
Push-Location runner
& '..\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  '..\.codex-validation\test_v1_w4_controller.py' -q
Pop-Location
```

结果：`10 passed in 22.50s`。r1 的长 title/long input、r2 的 101 observed 值上限、
r3 的跨节点断言 healing result 和 r4 的动作 healing result 均通过。

主控 W1 五项真实 Chrome（该脚本依赖 `runner/pyproject.toml` 的 pythonpath，故以
`runner` 为工作目录）：

```powershell
Push-Location runner
& '..\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  '..\.codex-validation\test_v1_w1_controller.py' -q
Pop-Location
```

结果：`5 passed in 13.39s`。

W1 13 动作专项：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  runner/tests/test_v1_web_actions.py -q
```

结果：`72 passed in 22.40s`。

最终源码 Runner 全量：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' runner/tests -q
```

结果：收集 `463` 项，`461 passed, 2 skipped in 113.47s`，退出码 `0`。W4 的
33 项均真实执行，未包含在 skip 中。

说明：r2 验收中的第一次全量为 `458 passed, 2 skipped, 1 failed in 124.10s`：W1 既有 networkidle
测试正确得到 `WEB_NODE_TIMEOUT`，但全量 Chrome 负载令从浏览器启动计时的总耗时
6.64 秒，超过该测试固定的 5 秒护栏。未越权修改 W1 测试；单项立即复验通过，随后
相同最终源码第二次全量全通过。

静态检查：

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check runner/runner runner/tests
```

结果：`All checks passed!`

说明：曾从仓库根目录单独调用隐藏主控脚本，因未加载 `runner/pyproject.toml` 的
pythonpath 而在 collection 阶段报 `ModuleNotFoundError`；未执行任何测试或产品代码。
随后使用上方正确工作目录单独复验为 5 项全通过。组合回归中该脚本也曾通过。

r4 新增真实 Chrome 单项首次执行时，产品脱敏及主控第 10 项均已通过，但自有测试把
既有 Locator attempt 误写成仅含 priority/status，漏掉冻结字段 strategy，故夹具断言
失败。修正为既有三字段 trace 契约后，W4 专项、主控探针与全量均通过；未为此修改
产品 trace。

## SHA-256

- `runner/runner/protocol.py`  
  `DD9F92C0AA7BB63C3682A9EB5D39A25F574128522B4843186FBBA464D7EBAD83`
- `runner/runner/executors/web.py`  
  `FB1FA24DD136A4A1604057E06B3F1A78AC108F56ABF79B06578BEF502A61D90D`
- `runner/runner/models.py`（未修改）  
  `F33405453276D19BAC96748BBF69AA93DF55BFCC48B75E40A2DBD67C0880A16A`
- `runner/tests/test_v1_web_assertions.py`  
  `3A60B0776FCEEDEB48F87E445575EDDFBB7FAB74D7CDBA1104A7271EA29F9769`
- `.codex-validation/v1-w4/result-r3.json`（r3 原样归档）  
  `E5E97E574254C5CAC38FFFA9C1405893A145EC9F910F125AD1505BAAACB0A533`

## 安全与副作用

未启动正式 Backend、Frontend、Worker、RabbitMQ 或数据库；未注册 Runner、创建正式
Run、调用真实 AI 或访问正式网络服务；未使用 Git、Claude、内部 Agent 或跨任务消息
工具。真实验收只使用独立回环 HTTP、测试所属隔离进程和 Chrome/profile，均已清理。

## 最终结论

`V1-W4/r4` 的 Runner 范围已完成。动作与断言的全部 Locator 自愈采集入口现共享同一
有界禁止值集合；计划 FILL/SELECT 值、未执行断言 expected 和适用 observed 值均不会
从 action/assertion healing result 回流。5 种基础 Web 断言、W1 13 动作、原 7 动作、
原 3 断言与 Runner 全量回归保持通过。本交付不代表 Backend DSL、Frontend 接入或
完整 V1 已完成。
