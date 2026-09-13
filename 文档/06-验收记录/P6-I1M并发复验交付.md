# P6-I1M/r2 需求关联并发复验交付

## 结论

**PASSED**。P6-I1/r4 已在独立 MySQL 8.4.11 临时实例上通过默认 `REPEATABLE-READ`、独立 `READ-COMMITTED` 对照、两服务真实并发、旧 RR 读视图、旧 ORM 缓存对象、真实 MySQL 1205 以及公开 HTTP 错误契约复验。最终 attempt 的 23/23 总门禁全部为 `true`。

本轮没有将任意 `DBAPIError` 当作可接受冲突，也没有由测试器先 rollback 再宣称产品事务已恢复。未处理的 `OperationalError` 在两服务并发场景中是直接失败条件。

## 边界与冻结指纹

- 冻结依据：`.codex-validation/p6-i1-controller-r4-review.json`。
- 执行前后 15/15 指纹均匹配，其中关联服务 SHA256 为 `e470987e2b8a2940fb0fa58e1a75d2680740a9b739d24da03d5e8b0849caf176`。
- 0039 迁移 SHA256 保持 `857ab58f765246203c6f72eef7f99e55b76c8d3e15c598b64c83470a2017b316`。
- r1 历史三项指纹执行前后不变：结果 `cbcc73ca…5c1f`、脚本 `3b99617d…44f2`、attempt-log `39965d3e…d594`。
- 未修改产品源码、迁移、Backend 测试、主控探针或 r1 证据。
- 未访问正式库/凭据、共享 Demo、AI、正式 Run，也未重启正式服务。

15 项冻结指纹如下：

| 路径 | SHA256 |
|---|---|
| `backend/app/api/v1/router.py` | `792f77e570f7625aa49eafd2634eef21001e6a6fa9225c8514bca4bd57b48557` |
| `backend/app/modules/requirement_links/__init__.py` | `a652b28a1b7ee6d371adc3e5df9a2bc6b75d554164e97a248d28c3ef154fee0d` |
| `backend/app/modules/requirement_links/router.py` | `9062ea0e537855650c23612e2bb99e77c932db10b5180bce0e41f0ed17dd6e1f` |
| `backend/app/modules/requirement_links/schemas.py` | `b5855e2d8e1a0f8097b686abe8d1866ecea1970a73c172f52f27b6a66c8f840a` |
| `backend/app/modules/requirement_links/service.py` | `e470987e2b8a2940fb0fa58e1a75d2680740a9b739d24da03d5e8b0849caf176` |
| `backend/app/modules/requirements/schemas.py` | `6f0f854fb8751f39b116131f6fddaa0af443e40be1906815d1463dbc7086d5a5` |
| `backend/app/modules/requirements/service.py` | `0abced32fb5a4e7a103a6d6d4bec64226ecf8c4c0bff65b48e8c9ad1bae16f54` |
| `backend/app/modules/test_cases/models.py` | `6055617c9b4cf97e44bbfc412afa6f1bece4a0aa371994b86d25ae2dd96c13c3` |
| `backend/app/modules/test_cases/schemas.py` | `b1c0cec0797738f6bb02c9350c69f9df03f25ba2faaca2ece8a697f17aa77691` |
| `backend/app/modules/test_cases/service.py` | `267f093d00997e982aad05878b5c93f3704e2f7e824906d92d8ea0d26331af82` |
| `backend/migrations/versions/20260910_0039_requirement_impact_links.py` | `857ab58f765246203c6f72eef7f99e55b76c8d3e15c598b64c83470a2017b316` |
| `backend/tests/test_ai_case_generation.py` | `e356dcb1865914d24f37c2146d862b0cf0d8c5307721a01f6e5e1174c2eab63e` |
| `backend/tests/test_requirement_link_lock_conflicts.py` | `fa349c0b1b8b368f602038f8db7db431da26099a7517dd5c67f52636334cf975` |
| `backend/tests/test_requirement_links.py` | `4dadb60419fa2dd235eea084fd5bc0f7d4f3284bf8f93a9e687c6da81f417b27` |
| `backend/tests/test_requirement_links_migration.py` | `94b0d655b7fe16c6a7cdbbe038d13641c3bf9cc723a0cffd2b5f196ce16914f8` |

## 执行环境与命令

- Docker Server `29.7.2`；MySQL `8.4.11 MySQL Community Server`。
- 镜像 `mysql:8.4`，digest `sha256:3466ba4a4828aa8d46fb7c3bc16b67b781c98413cf4ea0fac6feaa6e881faa26`。
- Python / SQLAlchemy / PyMySQL / Alembic：`3.12.14 / 2.0.52 / 1.2.0 / 1.19.1`。
- 通过 attempt：`20260910T060733-20475c3f377e`，随机回环端口 `127.0.0.1:60613`。
- 临时资源仅有带 `com.openai.codex.package=P6-I1M-r2` 和 `com.openai.codex.run=20475c3f377e` 的专属容器、卷、网络。
- 口令仅在临时 env 文件与当次进程内使用；attempt/index 不包含口令、完整数据库 URL 或授权值。

主命令：

```powershell
.\.venv\Scripts\python.exe .codex-validation\p6-i1m-r2\p6_i1m_r2_validate.py
```

隔离库中真实执行：

```text
python -m alembic -c alembic.ini upgrade 20260909_0038
python -m alembic -c alembic.ini upgrade 20260910_0039
```

在 0038 插入 r1 同类合成旧数据后升到 0039，旧关联指纹为 `fe69622a5858df19de59fed563aeb621f9bb0355092ddb83e245968913964e24`。r1 已通过的完整迁移往返与 17 项约束结论本轮仅核对指纹，未冒充为新执行。

## 1. 真实 MySQL 隔离级别

未指定隔离级别的新引擎在连接 `24` 上实测 `@@transaction_isolation=REPEATABLE-READ`；验证器没有修改全局隔离参数。独立引擎在连接 `25` 上实测 `READ-COMMITTED`。

所有并发场景都保存两个不同 `CONNECTION_ID()` 和每个连接的实际隔离级别，没有共用 Session 冒充并发。

## 2. 两服务并发创建

| 隔离级别 | 连接 ID | 结果 | 终态 |
|---|---|---|---|
| 默认 RR | `24 / 27` | `SUCCESS / CONTROLLED_BUSINESS_CONFLICT` | 1 ACTIVE |
| RC 对照 | `25 / 28` | `SUCCESS / CONTROLLED_BUSINESS_CONFLICT` | 1 ACTIVE |

两组都恰好一成功、一受控业务冲突，没有未处理 `DBAPIError`，没有重试。冲突 Session 在测试程序未 rollback 的前提下立即执行 `SELECT 1` 成功；此处是查到已有 ACTIVE 后的业务冲突，不是一个已中止的 DB 事务。成功侧的事件为 1 before-commit / 1 after-flush / 1 after-commit，并保存了 Requirement 先加锁、Link 当前读、INSERT flush 的产品路径证据。

## 3. 两服务移除/再关联真实交错

移除与再关联两侧都直接调用产品 `remove_requirement_link` / `create_requirement_link`，没有使用原始 SQL 代替任一侧。

| 隔离级别 | 同步点 | 连接 ID | 结果 | 新链 |
|---|---|---|---|---|
| RR | before-commit / flush 前 | `24 / 27` | `REMOVE_SUCCESS / SUCCESS` | `7 → 8` |
| RR | after-flush / commit 前 | `27 / 24` | `REMOVE_SUCCESS / SUCCESS` | `9 → 10` |
| RC | before-commit / flush 前 | `25 / 28` | `REMOVE_SUCCESS / SUCCESS` | `11 → 12` |
| RC | after-flush / commit 前 | `28 / 25` | `REMOVE_SUCCESS / SUCCESS` | `13 → 14` |

每组都在移除事务未提交时证明再关联 future 尚未完成，实际锁等待在释放后结束。移除提交在先，再关联创建新 ID，`supersedes_link_id` 精确指向刚移除的前驱。每组终态均为 1 REMOVED + 1 ACTIVE，历史稳定字段指纹前后一致，无半提交、无重复 ACTIVE、`retry_count=0`。

这四组中原 r1 的 before-flush 合法时序也被保留并通过；没有只删掉失败时序、只测 after-flush 换取通过。

## 4. 旧 RR 读视图和 ORM 缓存

- RR 旧视图连接 `24` 先读 Requirement 和旧 ACTIVE Link，连接 `27` 完成移除后，原 Session 调用创建服务得到新 Link `16`，精确 `supersedes_link_id=15`。
- RC 对照为连接 `25 / 28`，新 Link `19` 精确指向 `18`。
- 两组语句证据都包含先前的普通 `SELECT_REQUIREMENT/SELECT_LINK`，随后是带 `FOR UPDATE` 的当前读和最新 REMOVED 前驱读取。历史稳定字段指纹前后一致。
- 另外分别在 RR/RC 预先缓存 ACTIVE ORM 对象，由另一 Session 移除后，原 Session 再次移除均收到受控 `ResourceConflictError`；`populate_existing` 当前读将同一 identity 刷新为 `REMOVED`，移除人/时间与已提交审计指纹完全一致。
- 原 Session 没有被测试程序 rollback 就执行后续查询成功，且旧行没有被覆写。

## 5. 真实 MySQL 1205 + 公开 HTTP

使用独立 MySQL 事务对 Requirement `id=1` 持有 `FOR UPDATE` 锁，HTTP 依赖仅连接当次隔离库，使用合成 owner 身份。应用 lifespan 中的真实断连协调器被 noop 覆盖，记录 1 次 start / 1 次 stop，未启动真实协调扫描。

| HTTP 路径 | 持锁 / 请求连接 | 真实 MySQL 码 | 耗时 | HTTP | 释放后显式新请求 |
|---|---|---|---|---|---|
| POST 创建 | `27 / 24` | `1205` | `1.157s` | `409` | `201` |
| DELETE 移除 | `24 / 27` | `1205` | `1.000s` | `409` | `200 REMOVED` |

两个请求的 `innodb_lock_wait_timeout=1`，都在 Requirement 锁当前读阶段由真实 MySQL 返回 1205。产品 `_write_transaction_guard` 在返回 HTTP 前执行 rollback；Session 事件为 `after_rollback=1`，而测试器没有在恢复探针前 rollback。创建场景无意外行，移除场景的旧行仍为 ACTIVE。

公开错误体为：

```json
{
  "code": "RESOURCE_CONFLICT",
  "message": "需求关联写入遇到数据库锁冲突，请稍后重试",
  "request_id": "<generated>",
  "details": null
}
```

观测到的契约是任务包允许的 `409`，没有暴露 MySQL 错误码、SQL、口令或连接信息。锁释放后的后续请求是明确的新请求，记录 `explicit_retry_count=1`，不是产品隐式重试。

## 6. flush / commit 故障注入补充

独立执行 8 个故障注入场景：

- 错误码：`1205 / 1213`；
- 阶段：`flush / commit`；
- 操作：`create / remove`。

所有记录都显式标记 `FAULT_INJECTION_NOT_REAL_SERVER_ERROR`。flush 注入发生在 Requirement/Link 真实产品读取之后、Link INSERT/UPDATE 执行时；commit 注入前已观测 `after_flush=1`。八个场景都由产品观测到 `after_rollback=1`，HTTP 均为安全 `409`，创建无遗留行、移除保持原 ACTIVE，显式新请求分别成功返回 201/200。

**本轮没有声称真实复现 1213。** r4 按固定的 owning-Requirement 锁顺序使 r1 的 before-flush 死锁时序变为正常等待并通过；1213 仅是 flush/commit 错误映射的补充故障注入，不计入“真实 MySQL 死锁”。

## 7. 历史、版本与 UTC 冒烟

- 显式创建→移除→再创建链为 `3 → 4`，新 ID 精确指向已移除前驱。
- 创建和移除响应的 UTC 偏移均为 `+00:00`。
- RequirementVersion、TestCaseVersion、WebCaseVersion 内容指纹在所有并发、HTTP 和故障场景前后完全一致。
- 最终 30 条合成 Link 均满足 ACTIVE/REMOVED slot 和移除审计不变式，无半提交状态。

## attempt 保留

`index.json` 保留两次 Docker/MySQL attempt，没有覆盖原始失败：

1. `20260910T060558-aab6a1098457`：`FAILED`，阶段 `service_smoke`，验证器在生成证据哈希时遗漏 MySQL `DECIMAL` 序列化，产生 Python `TypeError`。这是有证据的验证器缺陷，不是产品错误；`cleanup_ready=true`。
2. `20260910T060733-20475c3f377e`：`PASSED`，仅将 `Decimal` 稳定规范为字符串后进行有限重跑；`cleanup_ready=true`。

首次 attempt 的 SHA256 为 `a07d6721f9dd3a0b0712866ab73391e494e0097732ad98a688c2b55658bcdd12`；最终通过 attempt 为 `16b31df76871b603e0335f8f67d1dcbb979833d575156c4e4cf4f2fc79e98fbd`。

## 清理证据

两个 attempt 均在 `finally` 中逐项校验标签后删除所属容器、卷、网络和临时凭据文件。最终 attempt 记录：

- `container_absent=true`
- `volume_absent=true`
- `network_absent=true`
- `credential_file_absent=true`
- `cleanup_errors=[]`
- Docker 清单前后相同：6 容器 / 4 卷 / 5 网络，各列表指纹一致。
- 正式 `ready.json` 前后指纹均为 `89a58ad23680f79b02809a73218582a466c1b11584157d1a88ef7c502615d3eb`。
- 正式 `owned-processes.json` 前后指纹均为 `2f78160afc4b8c361b00f207526020d58d954cc523d09032b4aae99ec236d0a9`。

## 交付物

- 总索引：`.codex-validation/p6-i1m-r2/index.json`，SHA256 `e2b98f9502056e8b725d331501531d3cafc4e078dd989394e0b9aa6729a427e7`
- 首次失败 attempt：`.codex-validation/p6-i1m-r2/attempts/attempt-20260910T060558-aab6a1098457.json`
- 最终通过 attempt：`.codex-validation/p6-i1m-r2/attempts/attempt-20260910T060733-20475c3f377e.json`
- 验证器：`.codex-validation/p6-i1m-r2/p6_i1m_r2_validate.py`，SHA256 `a3b170113258d75caee05e8caa4d470d1fa4c13fc552676a7b1e0e5c376f069b`
- 本报告：`文档/06-验收记录/P6-I1M并发复验交付.md`

验收结论仅适用于主控冻结的 P6-I1/r4 指纹和本轮 MySQL 8.4.11 隔离环境。
