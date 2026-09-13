# P5-F 后端交付记录

日期：2026-09-09

## 交付结论

P5-F 后端范围已完成实现与本地全量回归，覆盖 Web Evidence 平台失败终态、超期执行安全收口、固定总超时预算，以及本轮真实 MySQL 回归暴露的迁移降级依赖顺序问题。

后端未连接、修改或清理任何正式数据库。MySQL 迁移往返由 DevOps 使用隔离资源执行。

## 行为契约

### Web Evidence 平台失败

- 仅接受 `outcome=FAILED` 与 `error_type=WEB_EVIDENCE_ERROR` 的特殊组合。
- 非取消场景仍要求 trace 完整且不重复地覆盖固定 Web Action/Assertion 节点。
- StepRun 与结果审计保留 Runner 上报的真实执行终态；即使全部 StepRun 成功，CaseRun 与 Run 仍以平台失败结束。
- 对外错误固定归一为 `WEB_EVIDENCE_ERROR / Web 执行证据处理失败`，不保存或回显 Runner 传入的潜在敏感错误文本。
- 普通 `FAILED` 仍必须包含失败节点；取消状态优先于 Evidence 平台失败；重复完成保持幂等。

### 超期执行安全收口

- 后台协调器独立于 Redis heartbeat，扫描 `RUNNING` 与 `CANCELLING` Run。
- 首次成功 claim 时，将当时有效的默认总超时写入现有 `runs.total_timeout_ms`；显式 Run 超时保持原值。API、Scenario、Web execution plan 均只读取该固定值。
- 截止时间严格使用 `started_at + total_timeout_ms + grace`，宽限默认 60 秒，可通过 `APP_RUNNER_EXECUTION_TIMEOUT_GRACE_SECONDS` 配置。
- 候选查询排除无 `started_at` 或无持久预算的 Run，并在数据库侧按截止时间预筛；进入协调前再精确筛选，行锁内再次检查状态与截止时间。
- `RUNNING` 超期收口为 `TIMEOUT / TOTAL_TIMEOUT`；`CANCELLING` 超期收口为 `CANCELLED`，保留普通取消与强停请求的错误类型差异。
- 已有终态 CaseRun、StepRun 与 Evidence 不重写；未完成节点仅做保守终态收口，不伪造 trace、executor result 或 Cleanup 成功。
- 正常完成若先取得终态，协调器行锁复查后不再覆盖。
- 历史在途 Run 若 `total_timeout_ms` 为空，无法证明其 Runner 领取时预算，后台明确跳过，不使用当前默认值猜测。此类遗留 Run 需在单独取证后人工或专项处理。

### MySQL 迁移降级

- `0036_model_provider_connections` 使用显式自增主键表元数据取得非空回填的新 connection id，并对主键做正数 fail-fast 校验；回退会把连接侧 provider、base URL 与加密凭据状态写回旧列。降级同时保证先删除 `connection_id` 外键，再删除被该外键依赖的索引。
- `0031` 至 `0034` 的新表降级不再提前逐个删除仍服务于表内外键的索引，直接删除整表，由 MySQL 随表安全移除内部外键与索引。
- `0037_model_catalog_metadata` upgrade 明确移除旧 `max_context` 的数据库默认值并允许 NULL；downgrade 在回填 NULL 后恢复旧契约的 `NOT NULL DEFAULT 128000`，使回退到 0034 的旧式插入无需显式提供该列。
- 已审查 `0031` 至 `0038` 的约束、索引、列与默认值回退顺序；`0035`、`0038` 无需调整。

## 修改文件

- `backend/app/modules/runs/service.py`
- `backend/app/modules/runs/disconnect_coordinator.py`
- `backend/app/core/config.py`
- `backend/.env.example`
- `backend/tests/test_runs.py`
- `backend/tests/test_disconnect_coordinator.py`
- `backend/migrations/versions/20260904_0031_web_recordings.py`
- `backend/migrations/versions/20260907_0032_web_recording_ai_suggestions.py`
- `backend/migrations/versions/20260908_0033_locator_healing_proposals.py`
- `backend/migrations/versions/20260908_0034_web_failure_analyses.py`
- `backend/migrations/versions/20260908_0036_model_provider_connections.py`
- `backend/migrations/versions/20260909_0037_model_catalog_metadata.py`
- `backend/tests/test_web_recordings_migration.py`
- `backend/tests/test_web_recording_ai_migration.py`
- `backend/tests/test_web_healing_migration.py`
- `backend/tests/test_web_failure_analysis_migration.py`
- `backend/tests/test_model_provider_connections_migration.py`
- `backend/tests/test_model_catalog_migration.py`
- `文档/05-交付记录/P5F后端交付记录.md`

## 验证记录

在 `backend` 目录执行：

```powershell
$env:TEMP='<项目根目录>\.codex-validation\p5f-backend-20260909'
$env:TMP=$env:TEMP
..\.venv\Scripts\python.exe -m pytest tests
```

结果：`360 passed, 34 warnings in 24.75s`。警告均为现有 Starlette 弃用提示与 SQLAlchemy 模型类 pytest 收集提示，无新增失败。

```powershell
..\.venv\Scripts\python.exe -m ruff check --no-cache app tests migrations
```

结果：`All checks passed!`

相关 Run、协调器、Evidence 与迁移专项测试也已单独通过，包括旧 model + secret 非空数据的 `0035 -> 0036 -> 0035` 回填/回退保护。MySQL 8.4 的完整 `0031 -> 0038 -> 0030 -> 0038` 隔离往返由 DevOps 在本次迁移修复后复验，结果以 DevOps 交付记录为准。

## 待主控联合验收

- Runner 与 Backend 的 `WEB_EVIDENCE_ERROR` 真实链路联调及 P5-E 门禁恢复。
- DevOps 隔离 MySQL 8.4 完整迁移往返最终结果。
- 无持久总超时预算的历史在途 Run 不自动处理，若环境中实际存在，需先取证其领取预算与执行状态。
