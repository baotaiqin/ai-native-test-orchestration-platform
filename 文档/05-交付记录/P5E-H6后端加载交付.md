# P5E-H6 / r1 后端加载交付

## 结论

P5E-H6/r1 已完成。仅 H2 精确拥有的旧 Backend 被重载，新 Backend 已加载主控验收的 H5 固定源码，8000 唯一监听和 `/health` 正常。最终 `h6-verification.json` 为 `ready=true`，21 项只读检查全部通过。

Frontend、Demo、正式 Worker、MySQL 与 RabbitMQ/Redis/MinIO 均未重启或修改；未接触 Runner 的 Q1 临时容器、Chrome 或 HTTP 进程。未执行 `p5e_resume_failure_analysis.py`，未发起业务请求、AI 请求、消息/死信操作，未修改 Prompt/Schema、模型绑定、正式资产、manifest、AI ledger 或 Demo 状态。

## 重载前安全门禁

`deploy/p5e_h6_readiness.py --phase preflight` 在停机前重新验证为 `ready=true`：

| 门禁 | 重载前结果 |
|---|---|
| H5 两个业务源码与 Call19 恢复工具指纹 | 与 H5 交付完全一致 |
| 正式在途 Run（QUEUED/ASSIGNED/RUNNING/CANCELLING） | `0` |
| 正式在途录制（QUEUED/RUNNING/STOP_REQUESTED） | `0` |
| Backend/Frontend/Demo | HTTP 指纹健康 |
| Demo `changed_locator` | `true` |
| Runner `1282a04dfd894f85877384cb31af5c16` | `ACTIVE / ONLINE / WEB READY / 1/1` |
| 固定原 Run | `run_15c59b0b27964dd8a6fec2fcca840f9c / FAILED` |
| 新 Run | `run_7852432fb7b241dabcad111664b9621b / SUCCESS` |
| Proposal | `2 / REJECTED`；`3 / ACCEPTED`，创建 ElementVersion 3、WebCaseVersion 7 |
| WebCaseVersion 7 | `APPROVED` |
| FailureAnalysis | `0` |
| Call19 | 存在、success=true、repair=false、已知来源 hash 匹配 |
| recovery receipt/checkpoint | 均不存在 |

H3 result 与 attempt 逐字节一致，重载前后均为 SHA256 `aa3a4a9f396d2df7e2beb6cae100da91de22160b6aecb251df2d4ce96ed999f8`；AI ledger 重载前后均为 SHA256 `b6e20d30a358293a58435f856230345de91e0edda2eccfe44dfd40ea6675ef65`，`total_attempted=4`、记录数 4，状态仍为 `FAILED / generate_failure_analysis`。

## 旧 Backend 身份与精确终止范围

8000 重载前仅有一个监听者 PID `50392`，与 H2 `ready.json` 和 owned ledger 完全一致。三条旧 Backend 记录均逐项复核实际进程创建时间、父 PID、命令身份与监听关系：

| 旧 PID | 父 PID | 进程 | 启动时间 UTC | 身份 |
|---:|---:|---|---|---|
| 55064 | 32060 | python | `2026-09-09T16:41:12.9040385Z` | `python -m uvicorn app.main:app`，127.0.0.1:8000 |
| 31292 | 55064 | conhost | `2026-09-09T16:41:12.9105676Z` | 上述 Backend 的 console host |
| 50392 | 55064 | python | `2026-09-09T16:41:12.9893466Z` | 8000 唯一监听者、同一 Uvicorn 命令 |

旧进程的工作目录依据 H2 `Start-Process -WorkingDirectory backend` 契约、相对模块 `app.main:app` 成功加载和项目 `/health` 指纹共同确认。执行前再次对 PID 50392 的创建时间和身份做 TOCTOU 复核，仅精确终止该监听 PID；55064/31292 随其退出。最终三者均不存在，没有使用进程名批量终止，也没有触碰任何其他 PID。

## H2 清单历史副本

覆盖 ready/owned 前已保存：

| 历史副本 | SHA256 |
|---|---|
| `.codex-validation/p5e-services/ready-history-pre-P5E-H6-r1-20260909T173216313Z.json` | `752cf9adb9dc7f7fdef5031727ed2f837a2dcd75d84eb0a01c97324e7cdadf3a` |
| `.codex-validation/p5e-services/owned-processes-history-pre-P5E-H6-r1-20260909T173216313Z.json` | `b8b5eeb3fbafa889cb6d27e1108e8a58ef43f6623465037a8dd3d0036b9e0690` |

后置验证重新计算的副本哈希与记录值一致。当前与历史清单的语义对比证明：非 Backend 的 services、Runner、middleware、database 条目逐字段不变，非 Backend owned 记录逐字段不变。

## H5 加载证明与新 Backend 所有权

H5 最终指纹确认时间为 `2026-09-09T17:32:14.4159395Z`：

| 文件 | 重载前后 SHA256 |
|---|---|
| `backend/app/modules/ai_gateway/service.py` | `4479107a11a8ba4b45198e3b845ac57b68f0cb587cc02ff8b46594415c835729` |
| `backend/app/modules/web_failure_analysis/service.py` | `729f707e8a0d9efa3b8aab7e0802013a80c55e2d6bb67eed9ced4cd807833453` |
| `deploy/p5e_resume_failure_analysis.py`（仅核对，未执行） | `9b387663be3b888e85e4cd7fdb1c446043c3545e5dd1407ba7808f5b6706187a` |

新进程由隐藏窗口启动，工作目录明确设为项目 `backend` 目录，命令身份固定为 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`。新 owned 树如下：

| 新 PID | 父 PID | 进程 | 启动时间 UTC | 命令身份 SHA256 |
|---:|---:|---|---|---|
| 22188 | 25908 | python | `2026-09-09T17:32:17.4848031Z` | `9cbb5504ee4accdfff5140e25fa883401284743eb3086ec9c927553a8a31878e` |
| 10400 | 22188 | conhost | `2026-09-09T17:32:17.4921056Z` | `b4359116b59ef8fd3343f3c7d5a2f144b7f60a36d8c928e85ef8003dcd9f8fc1` |
| 20860 | 22188 | python | `2026-09-09T17:32:17.5408739Z` | `c720ba10bdc8b52f7694a2b125892b25a5e3937c62b35105477ac871b0388ab8` |

三个创建时间均精确复核到小数秒并晚于 H5 指纹确认；父子关系匹配，PID 20860 是 8000 唯一监听者，`ready.json` 也记录 PID 20860、`p5e-owned`、`P5E-H6/r1` 与 H5 源码指纹。`/health` 返回本项目 Backend 指纹。

## 未触碰服务与正式现场

最终只读核验：

- Frontend 仍监听 PID `53572`，Demo 仍监听 PID `21264`；
- Demo `/control/state.changed_locator=true`，未改变；
- 正式 Worker 仍是原一个逻辑实例、两个 venv 进程 PID `23364/57108`；数据库/Redis 实测 `ACTIVE / ONLINE / WEB READY / 1/1`；
- 正式在途 Run 和录制仍均为 0；原 Run FAILED、新 Run SUCCESS、Proposal 2/3、版本 3/7、FailureAnalysis 0、Call19 均未漂移；
- result/attempt/ledger 哈希未变，AI 账本仍为 4，无 recovery receipt/checkpoint；
- 本包未运行 Docker 命令，没有枚举、停止、重启或修改 Q1 临时资源及共享中间件。

## 工具与验证

新增：

- `deploy/p5e_h6_backend_reload.ps1`：要求两分钟内 `ready=true` 的正式预检，只接受 H2 owned Backend 唯一监听树；保存历史后精确停启，隐藏窗口启动并原子更新 ready/owned/reload 记录。
- `deploy/p5e_h6_readiness.py`：只读核对 H5 指纹、H3 固定现场、正式库/Redis、三个 HTTP 服务与 H6 Backend-only 隔离。

验证结果：

- PowerShell Parser：通过；
- Python `py_compile`：通过；
- Ruff：`All checks passed!`；
- 后置正式只读验证：`ready=true`，21 项检查全部通过；
- 最终进程巡检：旧 PID 全部不存在，新 PID 全部存活且创建时间/父子关系精确匹配，无临时 manifest 残留。

关键交付 SHA256：

| 文件 | SHA256 |
|---|---|
| `deploy/p5e_h6_backend_reload.ps1` | `59e9119d27608eaf4a31f3d7db9c67d8b7893112b39575c1a3b5c356da33aa0b` |
| `deploy/p5e_h6_readiness.py` | `f3ba210daf906cdf2228509531149c8bec2bdebe22737e49cffde3cd66d75f22` |
| `.codex-validation/p5e-services/ready.json` | `65351ae4a7f520828f2ead7bc2119a51e88af75c6b1029c76499ffa5e39e2076` |
| `.codex-validation/p5e-services/owned-processes.json` | `728967bf1fddca1946172764e39b1ee66ee884673a5b1481f0f0f6a1116631e6` |
| `.codex-validation/p5e-services/h6-preflight.json` | `78a5d4ed88496578d0b10921e18e5a8f4b4d48114b5f25ccc67a1b0ec7a18a88` |
| `.codex-validation/p5e-services/h6-backend-reload.json` | `738dfbfba1cb779cfdf072b7ee6eb759a61ca702ed56a277e9806eb7386baf3e` |
| `.codex-validation/p5e-services/h6-verification.json` | `76f0ffad664d51e39efdc71a93f73f2225a3e16de265ba1f59476db18fedd42f` |

## 后续边界

新 Backend 与其他服务保持运行。H6 只证明 H5 已加载并且 H3 检查点未漂移，不授权真实恢复。Call19 后的单次 FailureAnalysis 必须由主控审查 H6 后另行派发；在该授权前不得执行 `p5e_resume_failure_analysis.py`，不得新增 AI 调用或写入 recovery receipt。
