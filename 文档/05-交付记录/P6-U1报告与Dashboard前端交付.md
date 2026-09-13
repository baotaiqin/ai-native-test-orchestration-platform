# P6-U1 报告与 Dashboard 前端交付

## 交付结论

任务包 `P6-U1/r3` 已完成。前端现已提供只读测试报告目录、报告深链详情、Evidence Center 与真实 Dashboard，并在运行中心补充只读报告入口。实现按 `P6-R1/r2` 冻结契约读取服务端数据，不重新执行、不重算历史、不补造缺失结果，也没有新增业务写请求。

`r2` 集中修复报告详情刷新与 CaseRun、StepRun、Evidence 子请求之间的竞态。详情刷新、Run 参数变化和组件卸载都会开启新的统一详情上下文并使旧子请求失效；子请求的 success、catch、finally 同时核对详情上下文、本区请求序号、Run ID 及筛选快照。刷新还会统一清空旧分页、筛选、错误和 loading 状态，再采用新详情首屏响应。

`r3` 将 StepRun/Evidence 筛选输入拆分为草稿值与已应用值。编辑或 blur 只改变草稿，不改变当前请求的身份；点击“应用过滤”或“清除”才提交新的已应用筛选并发起请求。分页与 success、catch、finally 全部以已应用筛选为准，因此未提交的草稿不会让当前请求 loading 悬空，同时保留 `r2` 的上下文与请求序号保护。

本交付仅完成前端与受控合成 API 验证。验证期间没有加载真实后端、Runner、AI、资产服务或正式凭据，因此不构成正式联调或 V1 门禁结论。

## 功能范围

### 测试报告目录

- 新增 `/reports`，按有权访问的项目读取报告；归档项目仍可只读查看历史报告。
- 支持运行类型、状态、环境 ID、`Asia/Shanghai` 创建日期范围和服务端分页。
- 同时显示 Run 原始计数与 CaseRun 实际分组计数，避免把两种口径混为一谈。
- 每个 Run 可进入 `/reports/:runId` 深链；不存在或无权访问时显示统一只读错误与重试入口。
- 使用请求序号和筛选快照丢弃晚到响应，项目快速切换不会被旧请求覆盖。

### 报告详情

- 保留平台 Run 状态与 CaseRun 状态的层级差异；平台失败但已记录 CaseRun 均成功时明确提示，不自行改判。
- 展示执行锁定 Version ID、当前资产 Version ID、版本关系及“当前元数据”边界。
- 展示类型化 API Case、Scenario、Web Case 已存结果；`NOT_RECORDED` 与 `NOT_APPLICABLE` 不伪装为空成功。
- CaseRun、StepRun、Evidence 都支持服务端后续分页；StepRun 支持 CaseRun 过滤，Evidence 支持 CaseRun/StepRun 过滤。
- 结果、错误、Trace 与元数据仅以 Vue 文本插值或 `<pre>` 渲染，不使用 `v-html`。
- Evidence 下载忽略响应中的 `download_path`，仅以已知 Evidence ID 构造同源鉴权下载请求；Blob 形式的 JSON 错误体会安全提取 `message`。
- AI 区域仅展示失败分析/Healing 的安全引用和既有运行中心审计入口，不暴露原始 AI 内容。

### Evidence Center

- 新增 `/evidence`，按项目及可选 Run ID 读取鉴权后的元数据，包含归档项目历史。
- 展示大小、完整 SHA256、Run/CaseRun/StepRun 关联、安全元数据、生成时间和服务端分页。
- 不自动读取、打开或执行证据正文；只有用户明确点击“下载”才发起 ID 下载请求。
- 提供报告深链、空状态、403/404 权限状态、下载成功与失败反馈，并保护快速筛选切换的晚到响应。

### Dashboard 与导航

- 工作台改为读取 `/dashboard` 的真实聚合结果，可查看全部活动项目或单个活动项目范围；归档项目不进入 Dashboard 选择与统计。
- 明确展示活动项目、API/Web Case 与单列 Scenario、今日 Run、成功率、FAILED/TIMEOUT/CANCELLED 独立计数、四类待审核、最近运行。
- 成功率 `null` 显示“暂无已评估结果”；取消不进入分母。Runner 对非管理员显示不可见，对心跳不可用显示“状态未知”，不会把未知写成 0。
- 显示服务端返回的统计生成时间、`Asia/Shanghai` 自然日及三类定义口径；所有时间统一按 `Asia/Shanghai` 渲染。
- 主导航启用“测试报告”和“证据中心”；运行中心列表及详情只新增报告跳转，不改变原运行操作。

## 变更文件

- `frontend/src/types/reports.ts`
- `frontend/src/types/dashboard.ts`
- `frontend/src/api/reports.ts`
- `frontend/src/api/dashboard.ts`
- `frontend/src/api/evidence.ts`
- `frontend/src/utils/report-display.ts`
- `frontend/src/views/reports/ReportListView.vue`
- `frontend/src/views/reports/ReportDetailView.vue`
- `frontend/src/views/evidence/EvidenceCenterView.vue`
- `frontend/src/views/dashboard/DashboardView.vue`
- `frontend/src/router/index.ts`
- `frontend/src/layouts/AppLayout.vue`
- `frontend/src/views/runs/RunCenterView.vue`（仅只读报告入口）
- `frontend/tests/p6_u1_playwright.py`
- `.codex-validation/p6-u1/`

所有上述源码与测试文件均已核对为 UTF-8 无 BOM、LF 换行。

## 验证结果

### 静态与构建

- `npm run type-check`：通过。
- `npm run build -- --outDir ../.codex-validation/p6-u1/dist --emptyOutDir`：通过，1763 个模块完成转换。
- 构建只有既有依赖 PURE 注释与主包超过 500 kB 的 Rollup 建议，没有编译或类型错误。
- 静态检查确认本包页面无 `v-html`、无业务 `POST/PUT/PATCH/DELETE`，且页面不消费 `download_path`。

### 受控 Chrome 验证

命令：

```text
..\.venv\Scripts\python.exe -B tests\p6_u1_playwright.py --result-name playwright-result-r3.json
```

结果：`r2` 的 28 项场景全部保留，并新增 2 项筛选生命周期验证，共 30 项通过；`business_mutations=0`、`page_errors=0`、`unexpected_console_errors=0`。记录到 8 个预期负向 HTTP 控制台事件，分别来自合成的 403、404 与 500 场景。

覆盖内容：

- API Case、Scenario、Web Case 三类报告；归档项目；类型/状态/环境/上海日期筛选；第二页及后续分页；
- 空列表、403 权限拒绝、404 报告深链、重试入口、项目快速切换竞态；
- 平台失败与 CaseRun 成功并存、锁定版本与当前版本差异、未记录字段、Cancelled/Timeout；
- HTML/脚本样本文本安全渲染，页面未生成样本中的 `img`/`script` 节点且未执行注入标记；
- Step/Evidence 过滤与分页、AI 审计安全引用；
- CaseRun、StepRun、Evidence 页 2 晚到成功响应不会覆盖刷新后的页 1；晚到 500 不会注入旧错误；
- A→B→A 同一组件路由复用时，旧 A 子响应不能覆盖新 A；旧请求 finally 不能提前结束同区新请求的 loading；
- StepRun/Evidence 页 2 请求挂起期间编辑 CaseRun ID 草稿但不应用，原响应返回后 loading 正常结束；
- 点击应用后请求携带 `case_run_id=500`，继续翻页仍沿用该已应用参数；点击清除后第 1 页请求恢复为无筛选；
- Evidence 下载使用内部 ID 的成功路径、后端拒绝反馈，以及恶意外部 `download_path` 未被访问；
- Dashboard 的空成功率、Runner 隐藏、Runner unknown、不把 unknown 显示为 0、最近运行与终态独立计数。

机器可读结果：`.codex-validation/p6-u1/playwright-result-r3.json`。`r1` 原始结果保留为 `playwright-result.json` 和副本 `playwright-result-r1.json`，`r2` 保留为 `playwright-result-r2.json`。

两套主控复现探针均已原样运行且总体 `PASSED`：刷新探针的 CaseRun、StepRun、Evidence 三项 `stale_response_ignored=true`；筛选草稿探针的 StepRun/Evidence 两项 `loading_cleared_after_response=true`。没有修改主控探针源码或其目录内容来规避失败。

### 资源清理

- 三个受控脚本都在 `finally` 中关闭 loopback HTTP server 并回收服务线程。
- Playwright context 与 Chrome browser 均在脚本结束前关闭；下载仅存在于受控浏览器临时上下文。
- 三个验证脚本均已返回，脚本创建的本地服务线程与 Chrome context/browser 已按清理路径结束；本轮没有启动正式项目服务。

## 证据与 SHA256

### 受控截图

| 文件 | SHA256 |
| --- | --- |
| `.codex-validation/p6-u1/reports-catalog.png` | `4a9b76364af6ed2a5857d1b18806666fa8245e41711943702adab76679ce8902` |
| `.codex-validation/p6-u1/report-detail.png` | `48a05d9619b86cd1a10e8d535786994705f72ffb0b825095ebb6a39c47487f7e` |
| `.codex-validation/p6-u1/report-refresh-context.png` | `3ab4c9d159aae252380efa6fd0f576869b199151687fae8593696b7d3b4fe85a` |
| `.codex-validation/p6-u1/report-filter-lifecycle.png` | `743fff30263d307d6ec773202a3f7121ebbb2355e79fa2aec33d1cebbbfc5aa5` |
| `.codex-validation/p6-u1/evidence-center.png` | `78deed88ebb97dbf1beaf8c0e45c69890e5f0c135bdb0ebe6c7c5b322ee6ace4` |
| `.codex-validation/p6-u1/dashboard.png` | `d4e5bc8a0158c9c06bf1f2f6c93825be81c8483569fbe908db37aee9bf0b5646` |
| `.codex-validation/p6-u1/playwright-result-r1.json` | `e9285ea49de789163f4d2a839b0b14cba5714787e9926164f122c3f256a38945` |
| `.codex-validation/p6-u1/playwright-result-r2.json` | `404c001fd1d027ef9aac3ae7e2dd74da225c283f23e59d19f47fb79be965a713` |
| `.codex-validation/p6-u1/playwright-result-r3.json` | `3b2218faf5926844711dfb271885bde28dfea22896c6e9b6364fc507ce9db669` |
| `.codex-validation/p6-u1/dist/index.html` | `3e1b8b65677611ad204edb6fbc564214cdaaa3e066a0ded1a6531a3d5089426d` |
| `.codex-validation/p6-controller-refresh/result.json` | `19490ab463b2b99081e4e2d9a889474b4130a69a7756edc1b644f328cc25b202` |
| `.codex-validation/p6-controller-filter-draft/result.json` | `66d08aaf19f3919e5473968331b1f9444fc2a00c41fd517bcb109f1f832717ac` |

### 关键实现

| 文件 | SHA256 |
| --- | --- |
| `frontend/src/views/reports/ReportListView.vue` | `5952027ff3f93e727684b44861612b0966a09689530aaaa4ec09d26fae12bd40` |
| `frontend/src/views/reports/ReportDetailView.vue` | `07cb8551637d372c7a680907754e15573207747aa6e10602fe7404b9a87910c8` |
| `frontend/src/views/evidence/EvidenceCenterView.vue` | `c3d1b9186b0145d002635179d2358d325f805f230170642e6cb6016f826025f6` |
| `frontend/src/views/dashboard/DashboardView.vue` | `20692b093e474bc07cfe88c9834b2ecb1e6a8a4fa98ac0b486d7fe321e14d037` |
| `frontend/src/api/evidence.ts` | `5c8dcc06ca6db3aeb9f4bf5b37d6928dc267b9426c2c5081df43f28e9c5b7823` |
| `frontend/src/utils/report-display.ts` | `3342d9e1303266ba855299d668d603289f6209ddbd25bd0272d1bbdc2613a160` |
| `frontend/tests/p6_u1_playwright.py` | `91e104062f0cd348e2f3b73d75a4a98bf283af218730dd81b38f1837e13a5aa4` |

## 已知边界

- 本包是 Phase 6 的首个报告前端包；报告导出、缺陷草稿、归档恢复、需求到执行追溯与演示系统不在 `P6-U1/r3` 范围内。
- 报告只展示后端已持久化的数据，不推断或重建缺失请求、响应、断言、Trace、Evidence 或 AI 原文。
- 受控 Web 样本只覆盖固定空表单 CLICK 类历史结果，不等同于真实浏览器端到端 V1 场景。
- 本轮没有启动或重启服务，没有正式后端联调，没有执行正式 `H4`/V1 门禁，也没有修改后端、Runner、部署、认证、受保护清单或全局状态文档。
