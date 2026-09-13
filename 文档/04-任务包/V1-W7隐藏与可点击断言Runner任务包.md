# V1-W7/r1 隐藏与可点击断言 Runner

主控确认 W4/r4 已 completed/idle 且验收，现派发至既有 AI-Test-Runner_sol，gpt-5.6-sol / xhigh。依据总规格第39节，只实现以下两个缺失规则断言，其他运行中的工作包不受影响。

## 输入与边界

冻结基线：protocol.py 为 dd9f92c0aa7bb63c3682a9eb5d39a25f574128522b4843186fbba464d7ebad83，executors/web.py 为 fb1fa24dd136a4a1604057e06b3f1a78ac108f56abf79b06578bef502a61d90d，models.py 为 f33405453276d19bac96748bbf69aa93df55bfcc48b75e40a2dbd67c0880a16a。保留 W4 所有交付及主控10项证据。

继续 schema_version=1 和既有断言计划 type/locator/expected/timeout_ms 结构；新增 ASSERT_HIDDEN 与 ASSERT_CLICKABLE。两者 locator 必须有效、expected 必须 null、timeout_ms 为100..600000整数，拒绝bool、额外字段和未知类型。models无需新增字段。只允许 runner/runner/protocol.py、runner/runner/executors/web.py 局部实现、必要专项测试；独占 .codex-validation/v1-w7/ 与 文档/05-交付记录/V1-W7隐藏与可点击断言Runner交付.md。不得改 Backend、Frontend、迁移、消费/隔离协议、共享服务或其他证据。

## 准确语义

- ASSERT_HIDDEN：目标不存在或实际不可见时通过；存在且可见时失败，可等待其隐藏至原节点期限。多 Locator 是同一目标的替代描述，不能因为一个旧候选找不到就跳过另一个实际可见的候选并通过。检查全部有效候选：任一匹配到可见元素均不得通过；所有候选均明确不存在或全部匹配元素不可见才可通过。非法选择器、协议错误或浏览器异常不是“隐藏”，应受控失败。保留候选顺序和可审计状态；目标按预期不存在的成功路径不创建自愈上下文。
- ASSERT_CLICKABLE：用浏览器真实可操作性检查，覆盖可见、稳定、启用及未被覆盖。使用 Playwright Locator.click(trial=True) 或同等无实际点击机制；仅 is_visible && is_enabled 不足以证明可点击。禁止实际 click、dispatch_event、fill 或强制 force=True，也不传键盘 modifiers。试探可能滚动到元素，报告可如实说明，不应造成 click/input/submit 或导航。候选定位继续沿现有有界回退，重试检查不重放业务动作。

两者均共享原节点/Run预算和取消、Force Stop；不能另建浏览器/Run延长预算。不改变旧断言等待语义。任何检查失败都沿固定安全错误与节点归属处理，不暴露浏览器原始错误或DOM。保留 W4/r4 在动作与断言自愈两条路径的 planned/observed 敏感值传递及截断前脱敏。

## 验收与交付

独立随机回环站点、所属真实 Chrome：隐藏/缺失/可见/随后隐藏/非法selector、多候选“缺失+可见”与“缺失+隐藏”；可点击、disabled、aria-disabled、遮挡、隐藏与迟到可操作目标。计数 click/input/submit/导航证明没有业务副作用。覆盖两种新协议合法与非法载荷、节点和总预算、取消及合成敏感标记不进入结果/证据/自愈副本。

复用既有主控 .codex-validation/test_v1_w4_controller.py 的10项和 W1相关动作回归，Runner全量及Ruff；勿修改主控探针。记录命令、实际结果、源码指纹及所属资源清理。无需用大量重复结构测试替代真实浏览器行为。

禁止正式网络、AI、数据库、Run、Git、Claude、内部Agent及跨任务工具。先读根 AGENTS.md；完成报告与 final 后结束，超界保存检查点后结束，不等待他人。本包通过仅为 Runner 扩展，Backend/Frontend 后续由主控单独接入。
