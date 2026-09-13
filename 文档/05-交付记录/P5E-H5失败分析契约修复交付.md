# P5E-H5 / r1 失败分析契约修复交付

## 结论

P5E-H5/r1 已完成并通过后端全量回归。失败分析现在会在 AI Gateway 既有的“最多一次 Repair”链路内同时执行 JSON Schema 与调用方领域校验；`WebFailureAnalysisResult` 的全部 Pydantic 约束和失败节点动态子集约束都被纳入首轮与 Repair 轮的同一验证管线。

实现没有新增第二套重试或循环，没有放松失败分析服务保存前的最终校验，没有删除、去重或替换模型输出，也没有在业务代码中硬编码 `action_1` 伪造成功。通用 Gateway 不依赖 Web 模块，公共 HTTP 请求 Schema 没有新增可覆盖验证器的字段，无回调调用者仍使用原 JSON Schema Repair 提示与原失败语义。

Call19 后的单次 FailureAnalysis 恢复入口已完成代码和隔离测试，但本包未运行该入口。未调用真实 AI，未重启 Backend/其他服务，未启动 Worker，未更改 Demo，未修改正式 Prompt/OutputSchema/模型绑定或任何正式业务资产。

## Gateway 领域校验契约

`backend/app/modules/ai_gateway/service.py` 新增仅内部可传入的可选 `StructuredResultValidator` 回调，且未进入 `AiGenerateRequest` 或路由层。验证顺序为：

1. 单次 JSON 解析；
2. 现有 OutputSchema 校验；
3. 仅当前两步通过时，执行可选领域校验；
4. 任一约束不通过都进入原有的唯一一次 Repair；
5. Repair 输出再经过完全相同的管线，仍不通过则写入 `success=false`、`parsed_result=null`并返回原 `AI_STRUCTURED_OUTPUT_INVALID` 错误。

领域校验返回 false 或内部抛错时，Gateway 只记录并反馈固定安全摘要 `领域结果不符合调用上下文中的安全约束`。Pydantic 的输入值、原始异常和用户数据不会被拼入该错误。无回调调用仍使用原“上一次输出不符合 JSON Schema”Repair 提示，避免通用路径语义漂移。

## 失败分析接入

`backend/app/modules/web_failure_analysis/service.py` 从当次红化快照计算不可变 `allowed_failure_node_ids`，并将下列完整校验作为 Gateway 回调：

- `WebFailureAnalysisResult.model_validate(...)` 的字段枚举、长度、敏感内容、节点格式和唯一性校验；
- `evidence_node_ids` 必须是当次失败/超时节点集合的子集。

服务同时在原 `additional_instructions` 变量中以结构化 JSON 表达服务端约束：真实允许节点列表、节点格式和唯一性要求。用户附加说明被独立放在 `untrusted_user_data` 字段中，仍按不可信数据处理。`source_snapshot` 的字节与 SHA256 计算逻辑没有修改；保存前原有的 Call 归属、Prompt/Schema 身份、Pydantic 复核和节点子集复核全部保留。

## Call19 恢复入口

新增 `deploy/p5e_resume_failure_analysis.py`，只允许显式 `--resume-after-call-19` 入口。默认路径、attempt 与所有关键 ID 固定为 H3 现场。它在任何业务 POST 前拒绝下列任一漂移：

- result 与 attempt 不再逐字节相同，或不再匹配 H3 固定 SHA256；
- ledger 不再精确为原 4 条顺序不变的 `ATTEMPTED` 记录，或累计不为 `4`；
- Project 23 的 Binding 14 / Model 18 / Prompt 14 / PromptVersion 16 / OutputSchema 12 任一变化；
- baseline、原失败 Run/CaseRun28、Proposal 2/3、ElementVersion 2/3、WebCaseVersion 6/7、修复 Run/CaseRun29 的 ID、内容引用、Locator、终态或 Evidence 集合任一变化；
- Call19 不再精确匹配已知的来源 hash、模型、Prompt/Schema、`success=true/repair=false`和已知非法结果 `["://", "://"]`；
- 失败分析历史已非空，或已存在恢复 receipt。

真实执行时，入口会先保存 H3 result 和 ledger 的不可覆盖检查点，并以排他创建的 receipt 固化“已开始”状态；然后先将第 5 次业务 AI 尝试原子写入 ledger，才发出唯一一次 FailureAnalysis POST。不存在重试循环。响应、历史或新 AiCallLog 任一未知结果都会以安全错误码落盘 `FAILED`，receipt 阻止同入口再次 POST；只有完整成功时才补全 manifest 并转为 `PASSED / complete`。

该恢复脚本的 `action_1`、Call19 及其他 ID 硬编码仅用于拒绝非本次固定检查点，不参与产品领域结果生成或替换。本包没有执行该脚本，所以正式 `.codex-validation/p5e-live/` 下没有产生 receipt 或 recovery checkpoint。

## 测试与安全取证

针对性 service→Gateway→受控 Provider 测试使用真实业务服务和 Gateway 逻辑，只替换网络 Provider：

- 首轮非法且重复 `["://", "://"]` 触发恰好一次 Repair，Repair 返回 `["action_1"]` 后保存 `COMPLETED`，AiCallLog 正确保留 initial/repair 审计和 `repair_used=true`；
- 格式合法但不属于来源的 `missing_node` 同样进入单次 Repair，只有改为真实允许节点才通过；
- 两轮都非法时返回失败，不留 WebFailureAnalysis 占位，AiCallLog 为 `success=false/repair_used=true/parsed_result=null`；
- 模型输出中的敏感值没有出现在验证错误或 API 错误中；
- 首轮合法时只调用 Provider 一次，`repair_used=false`；
- 原无回调 Gateway 路径仍保持原 Repair 提示和原回退行为。

恢复入口隔离测试覆盖了 GET-only 预检不写入、manifest/ledger/远端终态漂移拒绝、单次成功补全、未知响应停止、原检查点保留和 receipt 二次调用拒绝。这些脚本测试使用临时目录和受控 API，没有连接正式库。

验证结果：

| 验证 | 结果 |
|---|---|
| 领域失败分析针对性测试 | `8 passed` |
| Gateway + Call19 恢复 + 失败分析组合测试 | `21 passed` |
| `backend/tests/test_p5e_acceptance_resume.py` | `24 passed` |
| Backend 全量 pytest | `397 passed` |
| 相关 Ruff | `All checks passed!` |
| 受影响 Python 文件 `py_compile` | 通过，pycache 隔离到 `.codex-validation/p5e-h5/pycache/` |

pytest 只有项目既有 Starlette/httpx 弃用与 ORM 模型收集警告，无测试失败。

正式只读取证使用正常认证登录，其后仅调用 GET；未执行业务 POST。安全摘要已写入 `.codex-validation/p5e-h5/formal-readonly-proof.json`：

- 正式 result 与 attempt 逐字节相同，均为原 H3 SHA256 `aa3a4a9f396d2df7e2beb6cae100da91de22160b6aecb251df2d4ce96ed999f8`；
- ledger 仍为原 SHA256 `b6e20d30a358293a58435f856230345de91e0edda2eccfe44dfd40ea6675ef65`，`total_attempted=4`；
- 正式状态仍为 `FAILED / generate_failure_analysis`，Call19 与失败节点 `action_1` 身份不变，分析历史仍为 `0`；
- 恢复 receipt 与 recovery checkpoint 均不存在，证明本包未运行真实恢复。

## 交付文件与 SHA256

| 文件 | SHA256 |
|---|---|
| `backend/app/modules/ai_gateway/service.py` | `4479107a11a8ba4b45198e3b845ac57b68f0cb587cc02ff8b46594415c835729` |
| `backend/app/modules/web_failure_analysis/service.py` | `729f707e8a0d9efa3b8aab7e0802013a80c55e2d6bb67eed9ced4cd807833453` |
| `backend/tests/test_ai_gateway.py` | `05b2029688a22a8b4a22c8b4175925c214d2855da9c04dbbb79fbe3563fb4785` |
| `backend/tests/test_runs.py` | `f6d168d1a5305b0337a3075898eeb513762482938509dde364c8279974682d6e` |
| `backend/tests/test_p5e_acceptance_resume.py` | `a3e8851e405f729f48ad5f4769e861eb6e2a038a5d22c2e2444a64d388a3dd16` |
| `deploy/p5e_resume_failure_analysis.py` | `9b387663be3b888e85e4cd7fdb1c446043c3545e5dd1407ba7808f5b6706187a` |
| `.codex-validation/p5e-h5/formal-readonly-proof.json` | `c7804500a7ba240240094d614654831c107bc9ca77bb9f4988d6b68f0709fbe0` |

`deploy/p5e_live_acceptance.py` 仍为 H3 原指纹 `4ec9bce6946cad14691ec4ae3fec10a769f07b5e64cf411800faac26719ec216`，本包未修改。`backend/app/modules/web_failure_analysis/schemas.py`、公共 JSON Schema 解释器、数据库迁移、正式 Prompt/Schema 资产均未修改。

## 后续边界

当前运行中 Backend 未因本包重启，本报告只证明磁盘代码和隔离验收完成，不声称现有进程已加载 H5。主控必须先审查本交付，再单独授权服务加载和真实 Call19 恢复。在新授权之前，不得运行 `p5e_resume_failure_analysis.py`，不得把当前 H3 `FAILED` manifest 作为 H4 完整成功输入。
