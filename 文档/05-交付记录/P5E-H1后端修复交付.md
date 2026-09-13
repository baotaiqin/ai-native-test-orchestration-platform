# P5E-H1 后端修复交付

任务包：`P5E-H1 / r2`  
最终交付时间：2026-09-10 00:30 +08:00  
结论：H1 r2 隔离代码与测试已完成，可交主控审查；本报告不代表 H2 服务加载或 H3 真实 AI 门禁完成。

## 修订记录

- r1 曾以 `383 passed` 交付，但主控审查发现 280 项提前返回会丢弃尾部 DOM 候选的既有合法 Locator，因此 r1 已被审查结论取代，不是 H1 最终版本。
- r2 取消该回归：公开响应契约与唯一派生函数共享 360 上限，完整支持 `40 × 9` 项；本报告下方测试与 SHA256 均以 r2 为准。

## 范围与安全边界

- 本轮只修改任务包允许的 Healing 服务及 Schema、后端测试、验收脚本及本报告/独占验证记录。
- 未连接正式系统，未读取或修改正式数据库、`result.json`、attempt manifest 或 AI ledger。
- 未重启 Backend/Worker，未重投消息，未创建、批准或执行任何正式业务资产，未调用真实 AI。
- 未使用 Git、Claude、跨任务消息/等待/调度工具。
- 结束检查未发现遗留 `python`、`pytest` 或 `ruff` 进程。

## 实现收口

### 安全 data-testid 派生

`backend/app/modules/web_healing/service.py` 现在只对已经通过 `_SIMPLE_VALUE` 的同源 `data-testid` 确定性派生：

1. `test_id` 原值；
2. 单引号 CSS 属性选择器；
3. 双引号 CSS 属性选择器。

AI 结果仍必须与同一 `candidate_index` 和派生出的完整 Locator 精确相等。陌生 value、错误索引、组合选择器/注入均不做等价解析，也不会被接受。每个 DOM 候选最多派生原有 7 种和新增 2 种，共 9 种；40 个候选完整得到 360 项。`schemas.py` 暴露同一个 360 常量给公开响应约束与服务派生函数使用，避免上限漂移；source snapshot、AI 输出校验、人工选择校验和公开响应继续共享完整列表，不再以截断尾部候选凑长度。

新生成的安全 source snapshot 包含完整的有界 `candidate_locators` 列表（`candidate_index + locator`），使模型看到实际允许项。历史 snapshot 没有该字段时，Proposal 读取仍由 `healing_context` 重新确定性派生，保持兼容。

### Call16 显式恢复入口

`deploy/p5e_live_acceptance.py` 的 `--resume-after-call-16` 只允许已知恢复事实，不能用于一般 AI 日志恢复，也未增加“从 AiCallLog 直接生成 Proposal”的产品能力。入口在任何后续业务 POST 前验证：

- 固定 attempt、baseline Run、失败 Run、Project、Case、Case Version、Element、Element Version、CaseRun；
- result pointer 与同名 attempt manifest 完全一致，阶段和已知 409 错误一致，尚无 Healing/Healed 物化标识；
- manifest 累计计数与全局 ledger 总数一致，本 attempt 恰好只有 ordinal 1 的已尝试记录；重复记录、错 ordinal/stage/status 或可篡改累计值均拒绝；
- Call16 是固定 Project 下成功的 `LOCATOR_HEALING` 调用，无 fallback、repair、retry、validation error 或 error type；
- Call16 的 ModelConfiguration 与当前 Locator Healing 主绑定及 model name 一致，Prompt Version 和 Output Schema 与固定 manifest 一致；
- 从固定 FAILED Run、Case Version、Element Version 和按原顺序的真实 healing candidates 重新构造旧 source snapshot，并要求其 SHA256 与 Call16 `entity_id` 相等；
- Call16 结构化结果必须是已知 candidate 2 的单引号 data-testid CSS，且固定失败节点当前仍无 Proposal。

未知结果、源实体漂移、模型/Prompt/Schema 漂移、已有 Proposal、重复调用账本都在恢复前 fail closed。通过恢复校验后，既有一次调用不会归零；完成闭环的预期累计数仍为 4。

## 隔离测试

新增 `backend/tests/test_p5e_acceptance_resume.py`，全部使用 `tmp_path` 文件和 Stub API：不创建网络客户端、不访问正式数据库、不调用 AI。覆盖精确正例及以下反例：

- Call entity、模型、Prompt、结构化结果、固定 Run、候选来源漂移；
- 已有 Proposal；
- ledger 重复、ordinal 错误、manifest/ledger 累计不一致；
- manifest 调用数、固定 Run、固定资产、已物化标识漂移。

`backend/tests/test_runs.py` 补充两种引号合法派生、错误索引、陌生 value、selector 注入拒绝、旧 snapshot 读取兼容，以及 40 个满属性候选每个索引均精确保留 9 种 Locator 的测试。另有真实服务层集成用例让尾部 `candidate_index=39` 完成 Proposal 生成、source snapshot/响应 Schema 一致性、列表读取和人工接受，防止再次通过丢尾部候选满足长度约束。

## 验证结果

- Ruff：5 个相关源码/测试文件，`All checks passed!`
- r2 聚焦 pytest：`34 passed, 75 deselected, 4 warnings in 6.76s`
- r2 Backend 全量 pytest：`384 passed, 0 skipped, 34 warnings in 37.92s`
- `deploy/p5e_live_acceptance.py`：`py_compile` 通过；`--help` 显示三个互斥入口，包括 `--resume-after-call-16`
- 警告为现有 Starlette 弃用与 pytest 模型类收集警告；无失败、错误或跳过。

首次编译尝试因共享 `deploy/__pycache__` 权限冲突退出；随后将缓存重定向到本包独占目录后成功。独占临时 pycache 已删除，不含业务数据且可重建。

机器可读记录：`.codex-validation/p5e-h1/verification.json`。

## 源码 SHA256（H2 加载核对）

| 文件 | SHA256 |
|---|---|
| `backend/app/modules/web_healing/service.py` | `b17fa1d82f818f325850558e2621c280c57f4468c69348ce247bc5c12a0b183f` |
| `backend/app/modules/web_healing/schemas.py` | `1c8a4d5b1a29fad37ea81a5861c363495247c0d50fc5db5d26187c01af069a11` |
| `backend/tests/test_runs.py` | `06884bc572729657e541da1cacb90a14787256cab706bc6bf70f95c1412da8b6` |
| `backend/tests/test_p5e_acceptance_resume.py` | `1b3c606a612105cd0837340014ca5079b6aa93aa2da2b2abe621d9add75ce591` |
| `deploy/p5e_live_acceptance.py` | `4ec9bce6946cad14691ec4ae3fec10a769f07b5e64cf411800faac26719ec216` |

五个文件均检查为 UTF-8/LF（无 CRLF、无孤立 CR）。r1 的 service/test_runs 指纹已失效，不得用于 H2 加载核对。

## 后续包前置条件

H2 需要由主控审查 H1 后再派发，加载并核对上述 Healing 服务源码指纹；H1 不执行服务加载。

H3 需要 H2 就绪记录已验收，并再次只读确认：固定失败 Run 仍为终态 FAILED、无 Proposal、正式 ledger 仍只有已知一次调用且 Call16/当前模型绑定/Prompt/Schema/旧 source entity 全部通过入口校验。届时才可由明确的 H3 包授权执行一次 `--resume-after-call-16`。任何校验失败都必须在 AI POST 前停止，不能重投原消息或改写账本。
