# V1-W1/r1 基础 Web 动作 Runner 任务包

主控派发给 AI-Test-Runner_sol，gpt-5.6-sol / xhigh。本包与 Backend P6-I1 需求关联独立，只写 Runner 所属文件，不等待其他任务。主控承担后续 Backend DSL 与 Frontend 接入；本包完成不能计完整 Web 功能或 V1 验收通过。

## 目标和冻结协议

依据总规格第38节，在现有 schema_version=1 WebExecutionActionPlan 结构内，新增13种无需新载荷字段的动作。保留既有7类动作和3类断言及所有原协议字段。

| 新 type | 必填业务字段 | 确定行为 |
|---|---|---|
| RELOAD | 无 | 当前页 reload，遵守当前节点及总预算 |
| BACK | 无 | 当前页浏览器历史后退；没有可后退历史按浏览器原生无操作语义，报告中不得虚构导航目标 |
| FORWARD | 无 | 当前页历史前进，空历史同上 |
| DOUBLE_CLICK | locator | Playwright 双击，保持既有安全候选定位路径 |
| RIGHT_CLICK | locator | 右键点击，不以普通左键替代 |
| CLEAR | locator | 清空可编辑字段，不引入新 value |
| HOVER | locator | 悬停并等待实际动作完成 |
| CHECK | locator | 勾选 checkbox，保持幂等的目标状态 |
| UNCHECK | locator | 取消 checkbox 勾选 |
| RADIO | locator | 选择 radio，非radio目标应失败，不伪装成功 |
| ENTER | locator | 对指定元素 press('Enter')，不可被载荷 key 改为其他按键 |
| TAB | locator | 对指定元素 press('Tab')，保持真实焦点移动 |
| WAIT_NETWORK_IDLE | 无 | 当前页 wait_for_load_state('networkidle')，持续活动应受节点/总预算约束并产生真实超时 |

全部动作沿用 timeout_ms 100..600000 和 STOP/CONTINUE。locator沿用直接候选与锁定element_version_id的计划，字段url/value/key在新动作中不需要，不允许它们偷换新动作的语义。严格拒绝未知type和非法/缺失locator；不得扩大为任意Playwright方法调用。

协议解析、执行器、trace/错误分类及相关隔离路径一并接入。正常操作与失败不得泄露页面正文、输入、Cookie/Storage/凭据或原始浏览器异常。保留现有多Locator尝试/预算、敏感截图遮罩、Console/Trace脱敏、Evidence上传契约、取消/Force Stop和“不重复执行已经发生的浏览器动作”保障。新动作不加整体重试；节点失败是否继续仍按原failure_policy。

## 文件与资源

允许 `runner/runner/protocol.py`、`runner/runner/executors/web.py` 及确有必要的 `runner/runner/models.py` 局部修改；新增/修订 Runner 专项测试。若需修改其它 Runner 文件先在报告说明具体必要性，仅限本包执行/协议集成，不扩大消费者、队列、认证或部署行为。

不得修改 Backend、Frontend、迁移、共享 Demo、全局文档或现有其他任务证据。不重启正式Worker/服务，不连接正式库，不调用真实AI、不注册Runner或创建正式Run。真实Chrome测试只使用本包随机回环服务和独立临时浏览器profile/进程，记录所有所属进程并精确清理，不停止用户浏览器。

独占证据目录 `.codex-validation/v1-w1/`，交付 `文档/05-交付记录/V1-W1基础Web动作Runner交付.md`。测试可在runner/tests新增正式回归；临时验证证据不要覆盖T1或P5F目录。

## 验收与交付

1. 新13种动作的协议合法/非法、缺字段与未知type验证；原7动作/3断言及旧payload兼容，不降低原校验。
2. 真实Chrome逐一证明实际效果：页面reload计数和前后历史、双击/右键事件、清空/悬停、checkbox状态与幂等性、radio选择及错误目标、Enter事件和Tab焦点、网络完成与持续活动超时。不能仅mock调用或断言PASS。
3. 新动作至少覆盖候选回退、节点超时/总预算、CONTINUE后的最终失败、取消/隔离进程结束、敏感内容不进入结果/证据的适用路径。与原失败定位器/截图遮罩机制保持兼容。
4. Runner全量pytest和相关Ruff；保留有界命令输出、实际Chrome版本、各场景结果、源码指纹、临时资源清理证据。未验证能力如实列出。

先读根AGENTS.md主控独占调度规则。禁止跨任务工具、消息、Git、Claude和内部Agent。收到范围外依赖时记录检查点后结束；完成交付后final并结束，不等待主控或Backend。主控主动读取和验收，运行中不会追加普通消息。
