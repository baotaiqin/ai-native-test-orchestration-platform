# P5E-H4 / r1 前端完整验收记录

> 日期：2026-09-10  
> 范围：P5-E 成功 manifest 驱动的真实页面完整审计、深链与只读边界  
> 原则：正常开发登录除外，业务 API 只允许 GET / HEAD / OPTIONS；不生成 AI、不 Accept / Reject / Approve、不创建或投递 Run

## 1. 结论

P5E-H4 / r1 **通过**。

独立 Playwright Chrome Context 已逐项读取真实页面并命中固定 ID，而不是用按钮数量或空容器替代业务结果：

- 原失败 Run `run_15c59b0b27964dd8a6fec2fcca840f9c` 为 `FAILED / WEB_LOCATOR_NOT_FOUND`，锁定源 WebCaseVersion `6`，真实失败节点为 `action_1`，页面展示 1 条失败 Trace 与 4 条 Evidence；
- Proposal `2` 为 `REJECTED / AiCall 17`，Proposal `3` 为 `ACCEPTED / AiCall 18`；两者页面审计均显示 `qwen3.7-plus`、PromptVersion `15`、OutputSchema `11`、Fallback/Repair 均为 false，并存在安全来源摘要；
- 已接受 Proposal 页面关联 ElementVersion `3` 与 WebCaseVersion `7`，终态 Proposal 不再显示 Accept / Reject 控件；
- 通过页面纯 GET 操作将已批准 WebCaseVersion `7` 载入运行表单成功，但没有执行校验、创建或投递；
- 源 WebCaseVersion `6 / V1` 与修复 WebCaseVersion `7 / V2` 的深链分别准确定位，版本历史中均为 `APPROVED`，页面明确不会自动批准；
- 修复 Run `run_7852432fb7b241dabcad111664b9621b` 为 `SUCCESS`，锁定 WebCaseVersion `7`，真实节点 `action_1` 成功，页面展示 1 条成功 Trace 与 5 条 Evidence；
- FailureAnalysis `2` 为 `COMPLETED / AiCall 20`，页面审计显示 `qwen3.7-plus`、PromptVersion `16`、OutputSchema `12`、Fallback/Repair 均为 false，并把结构化结果关联到真实失败节点 `action_1`；
- 浏览器业务写请求尝试 `0`，脚本错误 `0`，当前页面 console error `0`，HTTP error `0`。

未发现需要修改 `RunCenterView.vue` 或 Web Case 页面/API 的真实 UI 缺陷。本包仅修复并增强验收脚本，未修改前端业务源码。

## 2. 成功 manifest 契约修复

原 `frontend/tests/p5e_live_readonly_playwright.py` 只读取不存在于当前成功 manifest 的 `accepted_web_case_version_id / created_web_case_version_id`，随后会错误回落到 `source_web_case_version_id=6`，不能证明新版本 `7` 深链。

本轮改为：

1. 强制 `status=PASSED / stage=complete`；
2. 强制读取当前权威键 `healed_web_case_version_id`，并验证其与源版本不同；
3. 成功 manifest 缺失新版本、Proposal、AI Call、FailureAnalysis、成功 Run、Evidence 或相应终态时立即失败；
4. 强制 `result.json` 与 attempt 字节相同、ledger 恰为 5 次；
5. 前后分别计算 result / attempt / ledger SHA256，并要求完全不变；
6. 对真实页面逐 ID 核对 Proposal、版本、Run 和 FailureAnalysis 审计元数据；
7. 生成只包含安全标识/终态的 `ui-audit.json` 与裁剪截图，不记录凭据、Token、Cookie、StorageState 或原始 AI 响应。

新增针对性契约测试验证：真实成功 manifest 必须选择版本 `7`，删除 `healed_web_case_version_id` 后即使仍标记 PASSED 也必须拒绝通过。

## 3. 真实 Chrome 执行结果

执行命令：

```text
cd frontend
..\.venv\Scripts\python.exe -B tests\p5e_live_readonly_playwright.py
```

关键结果：

| 核对项 | 实际结果 |
| --- | --- |
| Manifest | `V1P5E_20260909_F294AC1E / PASSED / complete` |
| 原失败 Run | `run_15c59b0b27964dd8a6fec2fcca840f9c / FAILED / Version 6 / Evidence 4` |
| 失败 Trace | `action_1 / WEB_LOCATOR_NOT_FOUND / FAILED` |
| Reject 审计 | `Proposal 2 / AiCall 17 / REJECTED` |
| Accept 审计 | `Proposal 3 / AiCall 18 / ACCEPTED / ElementVersion 3 / WebCaseVersion 7` |
| 失败分析 | `Analysis 2 / AiCall 20 / COMPLETED / action_1` |
| 源版本深链 | `Version ID 6 / V1 / APPROVED` |
| 修复版本深链 | `Version ID 7 / V2 / APPROVED` |
| 已批准版本载入表单 | `Version ID 7`，只发生 GET 与本地表单选择 |
| 修复 Run | `run_7852432fb7b241dabcad111664b9621b / SUCCESS / Version 7 / Evidence 5` |
| 浏览器请求守卫 | 正常登录 POST `1`；业务写请求尝试 `0` |
| 浏览器错误 | page/script `0`；console `0`；HTTP `0` |

Evidence 表里存在 Runner 在真实执行时保存的“控制台错误”类型证据，这是被审计 Run 的历史 Evidence 类型；它不等于本次 H4 浏览器控制台报错。本次浏览器 console error 实测为 `0`。

## 4. 自动检查与构建

执行并通过：

```text
cd frontend
..\.venv\Scripts\python.exe -B tests\test_p5e_live_readonly_manifest.py
npm run type-check
npm run build -- --outDir ../.codex-validation/p5e-frontend-h4/dist
```

- manifest 契约测试：`2` 项通过；
- TypeScript / Vue 类型检查：通过；
- Vite 生产构建：`1750` modules，退出码 `0`；
- 构建产物位于 `.codex-validation/p5e-frontend-h4/dist`，未覆盖 `frontend/dist`；
- 只有既有的 Rollup PURE 注释与大 chunk 警告，无构建错误。

既有 10 项超时/重复提交 mock 回归未重跑：本包没有修改其覆盖的 `RunCenterView.vue` 或请求状态源码，按任务包只运行新增/修复行为的针对性测试。

## 5. 截图与布局检查

截图均为页面安全区域的裁剪图，未包含登录表单、凭据、Token、Cookie、StorageState、原始 AI 响应或 Evidence 正文。人工检查确认 ID、状态和列布局可读，无遮挡导致的审计信息丢失。

| 截图 | SHA256 |
| --- | --- |
| `failed-run-header.png` | `f76bfe258d2f561aba94e5f4e9e610f25f48d0896c03f58da24af137f1d66af1` |
| `failed-run-trace.png` | `fea6b90ac9c035d5e53401182f2c3afa272d9ef81b3a23dd2326e68b137f95f9` |
| `healing-proposal-history.png` | `788d1a24a554534ebdc277b3b87e11d64706946c7d016b8aa1b959548c25cd08` |
| `accepted-proposal-audit.png` | `1cf34a64b0a57e7daca1b58a495ae1953175cff95529bc0f6f84298b2269486b` |
| `failure-analysis-history.png` | `2c8ebdf5b140df1c01006ca2eedf05c62f5e675879d34b70070b237031d38a02` |
| `failure-analysis-audit.png` | `e855d95fe49e2361260d137c92e5a9ea791a764893d2e4226490a5a15c6255bd` |
| `source-version-deep-link.png` | `791f4595deb40d186481bead228fb51ca14ba2ff97ac017d906378b5fae75543` |
| `healed-version-deep-link.png` | `506c1c8be6f7f1b35c3a4f92b6070118d07b158b67265fb5c8bcc5cd983646b3` |
| `healed-run-header.png` | `e7b12b9732cc31e28642d6dfa8055d9f0808edbcfa1531f84885fe50f709999c` |
| `healed-run-trace.png` | `80f60a1afbe5194fe2e4bafa1d12b168b6b0f43bed4f8e453d428399be1cec46` |
| `healed-run-evidence.png` | `58b3d5c08f1f4e82c7da8d141bb5b52f5cb9b851107139c0da4c731049b1ed37` |

## 6. 文件与指纹

| 文件 | SHA256 |
| --- | --- |
| `frontend/tests/p5e_live_readonly_playwright.py` | `fdda779a2701fbc7e6230588ade3393ad49fe1864e5c902f126d46b26ce36fea` |
| `frontend/tests/test_p5e_live_readonly_manifest.py` | `5e5f38c7390fd29a8c6ca560780cee432e4ded0f96a407483cc7ff8d262f701f` |
| `.codex-validation/p5e-frontend-h4/ui-audit.json` | `e87c7fce8a5eb604f8762f64285c025c4bc8d4aaf626c61963d756e45f26d938` |
| `.codex-validation/p5e-frontend-h4/dist/index.html` | `c65ea36600610cceba5fb4e5f03a8ba922e17a1ad78a7a23ffb47b381de5df3f` |
| `.codex-validation/p5e-live/result.json`（前后） | `2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176` |
| attempt manifest（前后） | `2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176` |
| `.codex-validation/p5e-live/ai-call-ledger.json`（前后） | `eb9cbf3fd6a43b5be90d74a658d01ae73126b6f3f82e6ca1444b21c1e19f8b43` |

## 7. V1 范围声明

本次真实闭环使用的固定合成 Web Case 只有空登录表单的一次 `CLICK`，已证明 Locator Healing、人工拒绝/接受、DRAFT 后人工批准、批准版本载入、修复 Run 与失败分析的真实门禁及前端审计链路。

它不等于完整 V1 业务场景验收，尚未覆盖真实登录、Session 恢复、业务 CRUD、完整场景 D 或其数据清理。后续阶段不能用本 H4 结果替代这些验收。

本包没有遗留浏览器、服务或测试进程；没有修改共享 Demo、服务、真实业务资产、AI ledger、manifest、主控台账或其他任务报告。
