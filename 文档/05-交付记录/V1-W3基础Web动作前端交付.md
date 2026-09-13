# V1-W3 基础 Web 动作前端交付

## 任务包

- 编号 / 修订：`V1-W3 / r1`
- 日期：2026-09-10
- 状态：**执行任务自测通过，待主控验收**
- 范围：WebCase 手工编辑器与录制 AI 建议人工编辑
- 机器可读证据：`.codex-validation/v1-w3/validation-summary.json`
- 正式服务加载：未授权、未执行

## 实施结论

Frontend 已接入 W1/W2 冻结的 13 种基础动作：

`RELOAD`、`BACK`、`FORWARD`、`DOUBLE_CLICK`、`RIGHT_CLICK`、`CLEAR`、
`HOVER`、`CHECK`、`UNCHECK`、`RADIO`、`ENTER`、`TAB`、
`WAIT_NETWORK_IDLE`。

`frontend/src/utils/web-actions.ts` 现在集中维护动作全集、精确工厂、locator 判定和中文摘要，
WebCase 手工编辑与录制 AI 建议编辑共用同一语义来源。类型切换总是重新构造对象，因此从
`GOTO/FILL/PRESS` 切换到其他语义时不会残留 `url/value/key`；不存在未知类型回落为
`WAIT_URL` 的路径。

页面动作 `RELOAD/BACK/FORWARD/WAIT_NETWORK_IDLE` 的资产载荷仅包含
`type/timeout_ms/failure_policy`。其余 9 种新动作必须携带既有 `WebLocator`；
`ENTER/TAB` 没有自由 `key` 输入。全部动作继续使用 `100..600000` 的整数超时和
`STOP/CONTINUE` 策略。

原 7 种动作和 3 种断言保持原配置；本包没有接入 W5 的新增断言，也没有把 Runner
固定七字段执行计划误作 WebCase 资产 DSL。

## 手工编辑与 AI 决策保护

- 手工创建、保存新版本、批准执行仍是三个明确操作；归档历史保持只读。
- 直接 locator 与精确 `ElementVersion` 引用均保留，后者在 AI 编辑器中以只读标签显示，
  不会被转换为直接 locator。
- 录制自然语言步骤、起始 URL 与精确 Session Profile 引用保持；已编辑接受发送完整当前
  `content`，未编辑接受省略 `content`。
- 非法 AI 编辑只阻止接受，不阻止拒绝；拒绝不会隐式保存无效草稿。
- 手工保存与 AI 接受/拒绝均使用身份、项目、资产/录制和版本序列保护。切项目、切版本、
  切身份或卸载组件后，迟到响应不能刷新新上下文、显示旧成功提示或触发后续写入。
- 写失败保留当前编辑内容且不自动重试；重复点击由单次操作锁保护。
- I1F 需求关联入口、反向关系区和既有上下文保护未删除。

## 专项真实 Chrome 验收

专项用例使用独立随机回环 HTTP 服务提供生产构建与合成 `/api/v1/**`，未连接正式
Backend、AI、数据库、消息队列或目标站点。

覆盖结果：

- 13 种动作分别构造、保存、重载并逐项核对精确资产载荷；页面动作无业务字段，元素动作
  只使用 `WebLocator`。
- 实际执行 `GOTO→RELOAD`、`PRESS→ENTER`、`FILL→WAIT_NETWORK_IDLE`，确认旧字段被移除。
- 覆盖超时上下界 `100/600000`、两种 failure policy、缺 locator 前端阻断和
  `ElementVersion #501` 精确引用。
- 首次创建由合成 API 返回 500：编辑内容完整保留、无自动重试；第二次用户显式点击才成功。
- 原 7 动作与 3 断言精确回归；批准仍是独立请求，归档版本不能保存。
- AI 未编辑接受省略 `content`；已编辑接受发送 13 动作、原 3 断言、Session Profile
  `#22` 和精确 ElementVersion；非法编辑仍可拒绝。
- 覆盖版本切换后的迟到手工保存，以及身份变化后的迟到 AI 接受；两者都不会继续刷新、
  提示成功或自动重试。
- 结果：`89` 个请求，手工创建尝试 `2` 次、版本写入 `2` 次、批准 `1` 次、AI 接受
  `3` 次、AI 拒绝 `1` 次；`page_errors=0`、`unexpected_console_errors=0`。
- 唯一浏览器 HTTP 错误来自上述预期 500 负例，脚本单独计数为
  `expected_negative_http_console_events=1`。

截图目视检查：手工编辑页的 13 行动作、长类型名、locator、超时及策略均可读，无横向
溢出；AI 抽屉保持编辑态并显示结构化动作区域。截图不是请求体验收依据，载荷与次数由
Playwright 断言验证。

## 验证命令与结果

类型检查：

```powershell
Set-Location frontend
npm run type-check
```

结果：通过，退出码 `0`。

独立生产构建：

```powershell
Set-Location frontend
npm run build -- --outDir ../.codex-validation/v1-w3/dist --emptyOutDir
```

结果：`1775 modules transformed`，退出码 `0`。仅有 `@vueuse/core` PURE 注释位置和
主 chunk 大小两类非阻断构建告警。

W3 专项：

```powershell
& '.\.venv\Scripts\python.exe' `
  'frontend/tests/v1_w3_web_actions_playwright.py' `
  --dist '.codex-validation/v1-w3/dist' `
  --evidence '.codex-validation/v1-w3'
```

结果：`passed`，真实 Chrome，随机端口，正式服务调用为 `false`。

I1F 需求关联回归：

```powershell
& '.\.venv\Scripts\python.exe' `
  'frontend/tests/p6_i1f_requirement_links_playwright.py' `
  --dist '.codex-validation/v1-w3/dist' `
  --evidence '.codex-validation/v1-w3/i1f-regression'
```

结果：`passed`，`205` 请求、`8` 写入；同 ID 跨资产类型、版本历史、反向入口、只读、
失败保留和迟到响应保护全部通过。

T3 重试策略回归：

```powershell
& '.\.venv\Scripts\python.exe' `
  'frontend/tests/v1_t3_retry_policy_playwright.py' `
  --dist '.codex-validation/v1-w3/dist' `
  --out '.codex-validation/v1-w3/t3-regression'
```

结果：`passed`，18 组检查全部通过，`page_errors=0`、
`unexpected_console_errors=0`。

## SHA-256

- `frontend/src/types/web.ts`  
  `49f8ac8fdebe985c3a80b9eac2653bdd672f1fa85ab829e996d50ef78a4a7eeb`
- `frontend/src/utils/web-actions.ts`  
  `bb8ba22dbc5db535fb861276c9e28601b22baeb72deba2fae45f545d3a9d79d1`
- `frontend/src/views/web/WebAssetView.vue`  
  `b7da4841627f75f71865e54140a6b7ad7168a20e048ebad42ca7f1d0b2b9dda7`
- `frontend/src/views/web/WebRecordingWorkbench.vue`  
  `bba0000ecf61d7dad617a388130f2663f8262a014c399be67112bf20c48acdca`
- `frontend/tests/v1_w3_web_actions_playwright.py`  
  `b3b8bf644666b3b00ce28ff03c0bdd35039713d8e20c9d8b0832781b3d7fcb3f`
- 只读依赖 `backend/app/modules/web_cases/schemas.py`  
  `47106d6d748f653cef98ef5bb38497e3a25d93d6b7b51efc1d70b442982d2215`
- 构建 `assets/WebAssetView-DxT3Zf6F.js`  
  `2698cad09679f8cd813f79392cc5652795e3987781a03745ac05ea034227f123`
- 构建 `assets/web-assets-Dry0aibO.js`  
  `809c737d9a873b57eaea2f2598fa7b8107188e1e2a1be63d2f23c82c2504ee00`
- 构建 `assets/index-oAyvacki.js`  
  `b66cf0cca2d1a313d8c992533175748f5ef41880e655f84b2ae1700e6951da5e`

## 修改清单与边界

修改：

- `frontend/src/types/web.ts`
- `frontend/src/utils/web-actions.ts`
- `frontend/src/views/web/WebAssetView.vue`
- `frontend/src/views/web/WebRecordingWorkbench.vue`
- `frontend/tests/v1_w3_web_actions_playwright.py`
- `.codex-validation/v1-w3/`
- `文档/05-交付记录/V1-W3基础Web动作前端交付.md`

未修改 Backend、Runner、迁移、录制事件协议、正式服务、主控文档或其他任务证据；未使用
Git、Claude、内部 Agent 或跨任务消息工具。

## 未验边界与最终结论

本包没有验证正式服务端到端、真实目标站点的完整动作执行、13 种动作的录制事件捕获，
也没有接入 W5 新断言。这些不属于 `V1-W3/r1` 前端范围。

`V1-W3/r1` 执行任务自测通过，等待主控验收。13 种基础动作已进入 WebCase 手工编辑和
录制 AI 建议人工编辑链，并保持已有版本、权限、Session、ElementVersion、I1F 关联与
并发上下文保护。本结论不表示正式完整执行或 V1 全阶段已完成。
