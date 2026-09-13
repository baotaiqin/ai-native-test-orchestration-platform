# P5-E 前端验收记录

> 日期：2026-09-09  
> 范围：运行中心 Locator Healing、AI Web 失败分析、Healing 新版本深链与人工决策边界  
> 原则：前端只读取后端生成的 Run / Proposal / Version 标识；不创建真实验收数据，不点击 Accept / Approve / Dispatch / Generate AI

## 1. 规格与源码核对

核对依据：

- `文档/02-产品规格与计划/AI原生智能测试平台_产品需求系统架构与开发规格说明书.md` 第 43、45、110、112 节；
- `文档/02-产品规格与计划/V1收尾开发与验收计划.md`；
- `文档/当前开发状态.md`；
- `frontend/src/views/runs/RunCenterView.vue`；
- `frontend/src/views/web/WebAssetView.vue`（仓库不存在 `WebCaseView.vue`，该文件是实际的 Web Case / Version 管理页）；
- `frontend/src/api/http.ts`、`frontend/src/api/web-healing.ts`、`frontend/src/api/web-failure-analysis.ts`。

结论：

1. Locator 候选可供本次 Run 使用，但永久资产变更必须由人工 Accept；Accept 只创建 DRAFT，不自动 Approve、不自动重跑。
2. AI Web 失败分析保持只读，不创建缺陷、不修改资产、不自动重跑。
3. 全局 HTTP 超时实际为 15 秒。Healing 和 Failure Analysis 生成接口此前未覆盖该值，而录制 AI 已使用专用 90 秒超时，存在慢模型仍在服务端处理时前端提前报错的风险。
4. `WebAssetView.vue` 的 Healing 深链会校验项目、Web Case、Version 归属，定位到目标版本后仍要求人工检查和批准；未发现需要修改的前端缺陷。

## 2. 本轮修复

- `frontend/src/api/web-healing.ts`
  - Healing 生成请求使用专用、有限的 90 秒超时；列表与 Accept / Reject 请求继续使用全局超时。
- `frontend/src/api/web-failure-analysis.ts`
  - Failure Analysis 生成请求使用专用、有限的 90 秒超时；历史读取继续使用全局超时。
- `frontend/src/views/runs/RunCenterView.vue`
  - 识别 Axios 客户端超时以及 HTTP 408 / 504；
  - 超时后明确提示“服务端可能仍在处理、结果可能已经落库”，并要求先刷新对应历史；
  - 超时未知状态下锁住再次生成；历史刷新失败不解锁，显式刷新成功后才恢复；
  - Failure Analysis 与 Healing 均提供常驻的只读“刷新历史”入口；
  - 保留原有人工 Accept / Reject 二次确认、只建 DRAFT、不自动批准、不自动重跑边界。
- `frontend/tests/p5e_ai_request_state_playwright.py`
  - 新增隔离 Chrome 状态机验证；全部 API 为浏览器内合成响应，不访问真实后端、不调用模型、不触发人工决策接口。
- `frontend/tests/p5e_live_readonly_playwright.py`
  - 新增真实环境独立 Chrome 只读验证；除开发环境登录外拦截全部非读取 API，请求守卫确保脚本不会生成 AI 结果、派发 Run、批准资产或接受 / 拒绝提案。

## 3. 静态与构建验证

执行：

```text
cd frontend
npm run type-check
npm run build -- --outDir ../.codex-validation/p5e-frontend-dist
```

结果：

- TypeScript / Vue 类型检查通过；
- Vite 生产构建通过，产物写入 `.codex-validation/p5e-frontend-dist`，未覆盖 `frontend/dist`；
- 构建仅有既有的 Rollup PURE 注释和大 chunk 警告，无构建错误。

## 4. 隔离 Chrome 受控响应验证

执行：

```text
cd frontend
..\.venv\Scripts\python.exe -B tests\p5e_ai_request_state_playwright.py
```

结果：10 项通过，`pageerror = 0`。

| 场景 | Failure Analysis | Healing |
| --- | --- | --- |
| 超时后锁住重复生成 | 通过 | 通过 |
| 历史刷新失败不解锁 | 通过 | 通过 |
| 显式刷新成功后解锁 | 通过 | 通过 |
| 普通双击只发送 1 个 POST | 通过 | 通过 |
| 切换 Run 后旧响应不污染当前详情 | 通过 | 通过 |

该验证只证明前端请求状态与竞态边界，不代表真实模型或真实人工决策闭环通过。

## 5. 真实环境只读验收

环境：

- Frontend `http://127.0.0.1:5173`：HTTP 200；
- Backend `http://127.0.0.1:8000`：OpenAPI 文档 HTTP 200；
- DevOps 就绪清单：Backend / Frontend / Demo healthy，唯一 Runner `ACTIVE / ONLINE`、Web capability `READY`、Web slot `1 / 1`。

浏览器：先以 Codex 独立 In-app Browser 会话人工只读核验，再以独立 Playwright Chrome Context 复验。未点击创建、投递、取消、生成 AI、Accept、Reject、Approve 或重跑。

当前证据：

- 首次执行曾因 Backend Outbox 的 `PENDING → publish → PUBLISHED` 提交窗口竞态进入 DLQ；DevOps 已安全恢复该唯一消息，未启动第二个 Runner；
- 权威 failed Run `run_15c59b0b27964dd8a6fec2fcca840f9c` 已到终态 `FAILED / WEB_LOCATOR_NOT_FOUND`，失败后 Runner 仍为 `ONLINE`、Web slot 为 `READY`；
- Run 深链准确定位 Project 23 与目标 Run；页面呈现 1 个失败 CaseRun、失败步骤、1 条失败 Web Trace 和 4 条运行证据，Locator 值未在 Trace 表中展示；
- 失败分析区与 Healing 审核区均显示常驻“刷新历史”和生成入口；Healing 区明确“仅从后端候选中选择”“接受只创建 DRAFT、不自动批准或重跑”“Accept 不覆盖旧版本”；
- 当前失败分析历史和 Healing 提案历史均为 0，因此 Accept / Reject 按钮不出现，前端没有伪造人工决策状态；
- 使用 `result.json` 的 `project_id / web_case_id / source_web_case_version_id / run_id` 打开 Web 资产深链，页面准确定位目标 Case / Version，显示“页面不会自动批准”，并提供“返回原 Run”；
- Playwright 真实环境脚本报告 `blocked_mutations=0`、`script_errors=0`、`console_errors=0`，证据数与原子清单一致；
- `result.json` 当前为 `status=FAILED`、`stage=generate_and_reject_healing`，最新错误码为 `HTTP_409_POST_/runs/.../web-healing-proposals`；真实 AI 业务调用累计为 1，但尚未生成可审计的 Proposal / Version 标识。

真实环境复验命令：

```text
cd frontend
..\.venv\Scripts\python.exe -B tests\p5e_live_readonly_playwright.py
```

## 6. 当前结论

- 前端专用超时、重复提交保护、显式刷新恢复、双击与旧响应隔离：**通过**。
- 真实 RunCenter 终态、失败 Trace、证据、Run 深链，以及源 Web Case / Version 深链和人工批准边界：**通过**。
- Healing Proposal 审计、Reject 历史、Accept 后新 DRAFT Version、人工 Approve 状态、批准版本回到运行入口：**待 Backend 修复当前 Healing POST 409 并产出完整成功 `result.json` 后继续只读验收**。

因此，本记录当前不能判定 P5-E 真实闭环通过，也不会用隔离 mock 结果替代真实验收。
