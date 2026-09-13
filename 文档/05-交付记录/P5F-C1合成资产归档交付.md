# P5F-C1 / r1 合成资产归档交付

## 结论

P5F-C1/r1 已完成。仅 Project 23 下两个 P5-E attempt 的 Case 5/6、Element 1/2、Page 1/2 共六项合成资产，通过现有 archive API 按 Case→Element→Page 顺序从 APPROVED/ACTIVE 变为 `ARCHIVED`；这是状态归档，不是删除。

每个业务 POST 都在发送前原子写入本地 journal，六次均收到响应并经后续 GET 确认，未知结果与自动重试均为 0。最终 `verification.json` 为 `PASSED`，SELECT/SHOW-only、无登录、无 API 的独立复核 9 项检查全部通过。

本包未修改产品代码，没有归档 Project、Prompt、模型、Secret 或 Runner，没有操作 MinIO 文件，没有停止/重启服务，没有调用 AI、创建或派发 Run、操作消息/死信，也没有执行恢复入口。

## 固定输入与保护文件

归档计划 `.codex-validation/p5e-archive-plan.json` 在操作前严格要求 `PLANNED_NOT_EXECUTED / project_id=23 / writes_to_formal_state=0`，计划 SHA256 为 `feb58a01181c32d3a030de87b70d635cafc2394a1d5eb384247b226ed3a1588b`。该文件是只读授权输入，归档完成后仍保持原字节；实际结果记录在本包独占目录。

受保护文件在归档前后均完全一致：

| 文件 | SHA256 |
|---|---|
| 首次 attempt `V1P5E_20260909_39E35D46` | `6641011f5e02b7137191bd9ca5ab5a5049eb4099a702db4b1b8c9485da22e42b` |
| 成功 attempt `V1P5E_20260909_F294AC1E` | `2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176` |
| `.codex-validation/p5e-live/result.json` | `2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176` |
| `.codex-validation/p5e-live/ai-call-ledger.json` | `eb9cbf3fd6a43b5be90d74a658d01ae73126b6f3f82e6ca1444b21c1e19f8b43` |
| `.codex-validation/p5e-frontend-h4/ui-audit.json` | `e87c7fce8a5eb604f8762f64285c025c4bc8d4aaf626c61963d756e45f26d938` |

成功 result 与成功 attempt 前后逐字节相同。AI ledger 仍为 `total_attempted=5`、记录数 5；H4 报告仍为通过并引用上述固定 UI 审计指纹。

## 操作前身份与引用门禁

正式数据库连接安装了 `before_cursor_execute` 守卫，只允许 SELECT/SHOW；任何其他语句会在客户端执行前停止。preflight 状态为 `READY_NOT_EXECUTED`，确认：

| Attempt | Case | Page | Element |
|---|---|---|---|
| `V1P5E_20260909_39E35D46` | ID 5，`WC-00005`，名称 `{attempt} Empty Login Click`，APPROVED，current version 5 | ID 1，code `{attempt}_LOGIN`，名称 `{attempt} Login Page`，ACTIVE，固定 description | ID 1，挂 Page 1，名称 `{attempt} Login Button`，ACTIVE，current version 1，固定 description |
| `V1P5E_20260909_F294AC1E` | ID 6，`WC-00006`，名称 `{attempt} Empty Login Click`，APPROVED，current version 7 | ID 2，code `{attempt}_LOGIN`，名称 `{attempt} Login Page`，ACTIVE，固定 description | ID 2，挂 Page 2，名称 `{attempt} Login Button`，ACTIVE，current version 3，固定 description |

表中 `{attempt}` 代表同一行的完整 attempt ID；Page 与 Element 的 description 均精确为 `P5-E live acceptance synthetic asset`。

版本集合与引用也精确匹配：CaseVersion 5→ElementVersion 1、CaseVersion 6→ElementVersion 2、CaseVersion 7→ElementVersion 3。除 Case 5/6 自身版本外，引用 ElementVersion 1/2/3 的 CaseVersion 数为 0；Page 1/2 下除 Element 1/2 外的其他元素数为 0；归档资产关联的在途 Run 数为 0。任一名称、code、description、项目、当前版本、状态、引用或 ID 漂移都会在首个 archive POST 前停止。

## 实际 API 操作与 journal

本轮 HTTP 请求按用途计数：

- 正常开发登录 POST：1；
- 业务 archive POST：6；
- 认证后的资产身份/归档确认 GET：18；
- Backend health GET：1；
- 总请求数：26；无重试。

业务变更 journal 顺序与结果：

| 序号 | API | 原状态 | 响应/GET 确认 | journal 终态 |
|---:|---|---|---|---|
| 1 | `POST /web-cases/5/archive` | APPROVED | ARCHIVED | CONFIRMED_ARCHIVED |
| 2 | `POST /web-cases/6/archive` | APPROVED | ARCHIVED | CONFIRMED_ARCHIVED |
| 3 | `POST /web-elements/1/archive` | ACTIVE | ARCHIVED | CONFIRMED_ARCHIVED |
| 4 | `POST /web-elements/2/archive` | ACTIVE | ARCHIVED | CONFIRMED_ARCHIVED |
| 5 | `POST /web-pages/1/archive` | ACTIVE | ARCHIVED | CONFIRMED_ARCHIVED |
| 6 | `POST /web-pages/2/archive` | ACTIVE | ARCHIVED | CONFIRMED_ARCHIVED |

每项在 POST 前先持久化 `PLANNED_BEFORE_POST`，再持久化 `POST_STARTING`；收到响应后记录 `RESPONSE_RECEIVED`，最后只读 GET 确认后才写 `CONFIRMED_ARCHIVED`。没有 DELETE 请求，没有 UNKNOWN_RESULT，也没有将已归档状态恢复后重试。

## 历史保留证明

归档前后历史总指纹均为 `1d4b2a85d48bc286db7475fb3ebc42eceb689d9a56095e66c3f1270a036dfc78`。每个行级指纹覆盖该表映射的全部列；JSON、Locator、Evidence 路径及 AI 原始内容只在内存中参与 SHA256，不写入安全报告。

| 历史集合 | 数量 | 前后集合 SHA256 |
|---|---:|---|
| WebCaseVersion | 3 | `e0ab5c7125a78b0f807ad3873eb5881f1d7de8e302a0bda0a3f25810f89be4d0` |
| WebElementVersion | 3 | `3fd55956c7b920f9022a4ca3966d3080eda5970bfc9c62aee6ad3b58c2a1ca14` |
| ElementLocator | 4 | `a586a0f33c4ab77f78fef476fa0d3378dd2e708e6a40a9736cbb2661ded28cdf` |
| Run | 4 | `975483836277fc3e9d316bef1cd7e8ad19b573ae9b7952467a176bd3d2d8f3fc` |
| CaseRun | 4 | `2fe168ae066e1d17d27a6f956b98916717353fc2b5423b48a50ff2cf4c6eac75` |
| StepRun | 4 | `c472239b22b1001af86a5b17dc5c9f8c53e6fe975667edd3070ddbb0822cd69a` |
| RunWebExecutionResult | 4 | `b354453064626ebb73630a5850af0324c09eb25df60cda380c79a2544d07f9ea` |
| Evidence | 19 | `d115721e61f2228280950b5ff4d2b22389f5ea279ea71309022f88a35f938a83` |
| Project 23 AiCallLog | 9 | `108d39f33f7753a924b213de5b4c8f6f06501d0e35aee4444695884f5955a889` |
| HealingProposal | 2 | `a0636caad16f1c342a984252be84a3c55ca87febf555e07cf3fe91517e768a54` |
| FailureAnalysis | 1 | `aed754d08c59d6540609585abce2259960727b4728297f225f1c784e3892fe12` |

Project 23 自身行指纹前后相同且状态仍为 ACTIVE。所有版本、批准信息、Run/CaseRun/StepRun、Web 执行结果、Evidence、AiCall、Proposal、FailureAnalysis 的精确 ID 集、数量和全列行指纹均未变化；因此没有历史删除或隐式级联删除。

## 工具与验证文件

新增 `deploy/p5f_archive_acceptance_assets.py`，包含：

- `--preflight-only`：正式库 SELECT/SHOW-only 身份、引用和历史快照；
- `--execute`：单次登录、POST 前 journal、一次 archive POST、GET 确认；
- `--verify-only`：无登录、无 API、SELECT/SHOW-only 最终重建验证。

静态验证：`py_compile` 通过，Ruff `All checks passed!`。本包不改产品代码，按任务要求未运行全量测试。

一次初始 `verify-only` 因 JSON 对象键从整数序列化为字符串而安全停止，发生在六项归档完成后的纯只读阶段，没有 API 或正式写入。统一引用映射键格式后复验通过；原停止记录保留为 `verification-attempt-key-normalization-stopped.json`，当前没有活动 `failure.json`。

| 交付文件 | SHA256 |
|---|---|
| `deploy/p5f_archive_acceptance_assets.py` | `4b2a1e1af69d57e801150632c2072957d02da365a923889698fea4bc5ed76a03` |
| `.codex-validation/p5f-archive/preflight.json` | `00f57b1a56b9f096f5a7026cd7d5c01c790ad489cbeab2afa47535bb8c206eee` |
| `.codex-validation/p5f-archive/operations.json` | `7f9d927838bdb9524e701adf77e914cd92a71edfc4333f4f61639ca04ccf79a7` |
| `.codex-validation/p5f-archive/verification.json` | `58f35fc46abe70bdaaa10be738e0e9504420239ad3704a14f44e06d63d492597` |
| `.codex-validation/p5f-archive/verification-attempt-key-normalization-stopped.json` | `f1f57640044f7d4849c95d8973b71c33ee635cd10489e4c612ebd4c6d23f9bfc` |

## 遗留项与边界

本包无未确认 archive 操作、无遗留进程、无活动失败 journal。六项资产已归档但所有历史仍可审计。归档计划保持只读原样；如未来需要恢复任何资产，必须由主控另行明确授权，本包不包含恢复权限。
