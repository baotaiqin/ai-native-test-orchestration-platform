# P5E-H3 / r1 真实闭环交付

## 结论

P5E-H3/r1 按固定 attempt `V1P5E_20260909_F294AC1E` 与授权次数执行了且仅执行了一次 `deploy/p5e_live_acceptance.py --resume-after-call-16`。定位器修复主链已真实完成：第一个新 Proposal 被拒绝，第二个新 Proposal 被接受，生成的新 ElementVersion/WebCaseVersion 先保持 DRAFT，再显式批准，且仅新版本的新 Run 真实成功。

本包未完成全部闭环：最后的真实 FailureAnalysis POST 返回 `409`，脚本依约立即停止并落盘 `FAILED / generate_failure_analysis`。未重试、未新增第三套资产、未重跑 baseline、未重投旧消息，也未修改业务源码或服务配置。因此本报告是阻塞检查点，不是 H3 成功验收声明；H4 所需的成功 manifest 与 `failure_analysis_id` 尚不存在。

## 固定输入与执行边界

| 项目 | 固定值 / 结果 |
|---|---|
| Attempt | `V1P5E_20260909_F294AC1E` |
| Project / WebCase / 源 WebCaseVersion | `23 / 6 / 6` |
| WebElement / 源 ElementVersion | `2 / 2` |
| 原失败 Run / CaseRun | `run_15c59b0b27964dd8a6fec2fcca840f9c / 28` |
| 既有已知 AI Call | `16`，执行前 ledger 累计 `1` |
| 恢复命令 | `.\.venv\Scripts\python.exe deploy\p5e_live_acceptance.py --resume-after-call-16` |
| 命令执行次数 | `1` |
| 新建 baseline / 第三套资产 | `0 / 0` |
| 旧失败消息重投 | `0`；原 `source_acked` journal 未触碰 |
| 脚本修订 | `0`；执行与停止语义正确，不存在可在 H3 边界内修复的脚本缺陷 |

H2 重启后 Demo 状态为 `changed_locator=false`。执行前先通过 `/health` 核对为 `service=v1-demo,status=ok`，随后按任务包授权将其重构造为 `changed_locator=true`。这是重启后环境重建，不是原 Demo 进程延续；原 Run/Trace/Evidence/AI 历史没有因此被改写。本包结束时 Backend 仍为 `service=backend,status=ok`，Demo 仍为 `service=v1-demo,status=ok,changed_locator=true`。

## 实际闭环结果

| 节点 | 安全标识 | 终态 / 核对结果 |
|---|---|---|
| 原 baseline Run | `run_786380f61a9647cd8976d376f90bc6e8` | `SUCCESS`，未重跑，Evidence `5` |
| 原旧定位器 Run | `run_15c59b0b27964dd8a6fec2fcca840f9c` | `FAILED`，CaseRun `28`，Evidence `4` |
| 新拒绝 Proposal | `2` / AiCall `17` | `REJECTED`，未生成新版本 |
| 新接受 Proposal | `3` / AiCall `18` | `ACCEPTED`，生成 ElementVersion `3` 和 WebCaseVersion `7` |
| 源 WebCaseVersion | `6` | 仍为 `APPROVED`，未改写 |
| 修复 WebCaseVersion | `7` | 接受时为 DRAFT 且未自动运行；随后显式批准为 `APPROVED` |
| 新修复 Run | `run_7852432fb7b241dabcad111664b9621b` | `SUCCESS`，CaseRun `29`，Evidence `5` |
| FailureAnalysis AI Call | `19` | 模型调用成功，但服务安全结构校验拒绝输出 |
| FailureAnalysis 业务记录 | 无 | 占位 DRAFT 已回滚删除，历史 `total=0` |

新修复 Run 的五个 Evidence ID 为：

- `artifact_cf43e9f923c1553c34a2c4fd9fe889acaacb8d5f`
- `artifact_885e6e03a13d91bd2afba960a339dc4a17e4beb7`
- `artifact_6c4da59d80b4248cbf0113a3f63846663d2dcca0`
- `artifact_406549d6f89a9a5a058740889def88fe83d0d11e`
- `artifact_3aa6fb83402a4b8dc8ceeb9482884954e4547fba`

固定合成 Case 只包含空登录表单的一次 CLICK。上述成功只证明 Locator healing 的真实链路，不代表完整登录、Session 恢复或业务 CRUD 流程已验收。

## AI 调用账本

`ai-call-ledger.json` 最终 `total_attempted=4`，与 result 的 `ai_business_calls_cumulative=4` 一致，未超过全局上限 `6`：

| 累计序号 | 阶段 | 状态 | 对应记录 |
|---|---|---|---|
| 1 | `generate_and_reject_healing` | `ATTEMPTED` | 已知 Call `16` |
| 2 | `generate_and_reject_healing` | `ATTEMPTED` | Call `17` |
| 3 | `generate_and_accept_healing` | `ATTEMPTED` | Call `18` |
| 4 | `generate_failure_analysis` | `ATTEMPTED` | Call `19` |

失败后的诊断只建立正常认证会话并调用 GET 读接口；未新增任何业务 POST 或 AI POST，所以账本仍精确为 `4`。

## 409 根因取证

1. 公开 AI 调用查询显示新增 `AiCallLog 19`：Project `23`、task `WEB_FAILURE_ANALYSIS`、entity type `WEB_FAILURE_ANALYSIS`、model config `18`、actual model `qwen3.7-plus`、PromptVersion `16`、OutputSchema `12`。其 `success=true`、`fallback_used=false`、`retry_count=0`、`repair_used=false`、`error_type=null`、`validation_errors=[]`。这证明请求已进入真实模型并通过 AI Gateway 的 JSON Schema 校验，不是模型未调用、网络失败或配置缺失。
2. Call `19` 的安全结构摘要为 `failure_category=LOCATOR_NOT_FOUND`、`confidence=0.95`、`needs_human_review=false`，但 `evidence_node_ids` 为两个重复的 `"://"`。原始响应与修复响应内容未输出，只记录其字符数分别为 `441 / 0`。
3. 原失败 Run 的 WebTrace 和 StepRun 均只有节点 `action_1`，状态 `FAILED`，错误类型 `WEB_LOCATOR_NOT_FOUND`。因此 `"://"` 既不是有效节点标识，也不属于失败快照允许集合 `{action_1}`。
4. OutputSchema `12` 对 `evidence_node_ids.items` 只声明了 `type=string`和数组 `maxItems=40`，没有与服务层一致的 `pattern` 或 `uniqueItems`约束。PromptVersion `16` 明确要求只能返回快照中失败/超时的 `node_id`，但本次模型输出未遵守该语义约束。
5. `backend/app/modules/web_failure_analysis/schemas.py` 的服务结构要求节点 ID 匹配 `^[A-Za-z][A-Za-z0-9_-]*$` 且不能重复。`service.py` 在获取 Call 后先执行 `WebFailureAnalysisResult.model_validate(...)`，然后才校验其是否为失败节点子集。本次输出因非法节点格式在第一步被拒绝，服务删除占位分析并返回资源冲突；脚本记录的安全错误码为 `HTTP_409_POST_/runs/run_15c59b0b27964dd8a6fec2fcca840f9c/web-failure-analyses`。

综合结论：409 是真实模型输出通过较宽的持久化 OutputSchema、却未通过更严的服务层安全模型所导致的契约缺口。现有脚本已在 POST 前记账，且在首个未知结果后停止和保存检查点，不应修改脚本绕过业务安全校验。

## 落盘检查点

`.codex-validation/p5e-live/result.json` 与对应 attempt manifest 字节完全相同：

| 文件 | SHA256 | 关键状态 |
|---|---|---|
| `.codex-validation/p5e-live/result.json` | `aa3a4a9f396d2df7e2beb6cae100da91de22160b6aecb251df2d4ce96ed999f8` | `FAILED / generate_failure_analysis` |
| `.codex-validation/p5e-live/attempts/V1P5E_20260909_F294AC1E.json` | `aa3a4a9f396d2df7e2beb6cae100da91de22160b6aecb251df2d4ce96ed999f8` | 与 result 字节相同，长度均为 `3427` |
| `.codex-validation/p5e-live/ai-call-ledger.json` | `b6e20d30a358293a58435f856230345de91e0edda2eccfe44dfd40ea6675ef65` | `total_attempted=4` |
| `deploy/p5e_live_acceptance.py` | `4ec9bce6946cad14691ec4ae3fec10a769f07b5e64cf411800faac26719ec216` | 与 H3 固定输入指纹一致，未修改 |

result 已保留 Proposal `2/3`、新版本 `3/7`、修复 Run 及全部 Evidence ID，但依约不写入未成功持久化的 `failure_analysis_id`。所有合成资产与 Demo `changed_locator=true` 现状均保留，未清理。

## 后续边界

当前任务包到此结束，不得在原 H3 授权下再次调用 FailureAnalysis。后续必须由主控审查本检查点后单独决定是否修订 OutputSchema/Prompt/服务契约，并在新任务包中重新冻结调用预算和恢复入口。在获得该新授权前，H4 不能把当前 `FAILED` manifest 当作完整成功输入。
