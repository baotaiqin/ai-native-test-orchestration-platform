# P6-I1M/r1 需求关联真实 MySQL 验收报告

## 结论

**PASSED**。已在完全隔离的 MySQL 8.4 临时实例上完成 0038→0039 迁移、非空旧数据兼容、三类有损降级拒绝、真实 FK/CHECK/唯一性、产品服务历史语义、两组独立事务并发以及 UTC 契约验收。最终 15 项总检查全部为 `true`，无产品失败。

本轮未修改产品源码、迁移或 Backend 测试；未读取正式凭据，未访问正式数据库、共享 Demo、AI 服务或正式资产。

## 环境与边界

- 执行完成时间：`2026-09-10T01:16:03.946532+00:00`
- Docker Server：`29.7.2`
- 镜像：`mysql:8.4`
- MySQL：`8.4.11 MySQL Community Server`
- 镜像 digest：`sha256:3466ba4a4828aa8d46fb7c3bc16b67b781c98413cf4ea0fac6feaa6e881faa26`
- Python / SQLAlchemy / PyMySQL / Alembic：`3.12.14 / 2.0.52 / 1.2.0 / 1.19.1`
- 临时回环端口：`127.0.0.1:50826`，未使用 3306 主机端口，未对外网卡发布。
- 专属标签：`com.openai.codex.package=P6-I1M-r1`、`com.openai.codex.run=c54aa0bdb4fd`。
- 专属资源：容器 `p6-i1m-mysql-c54aa0bdb4fd`、卷 `p6-i1m-mysql-c54aa0bdb4fd-data`、网络 `p6-i1m-mysql-c54aa0bdb4fd-net`；仅连接该专属网络，仅挂载该专属卷。
- 口令仅在临时 env 文件与验证进程内使用；env 文件在容器创建后立即删除，机器结果和日志中无口令、完整连接 URL 或 API 密钥。

## 冻结源码指纹

控制器冻结的 14/14 项在执行前后均匹配：

| 路径 | SHA256 |
|---|---|
| `backend/app/api/v1/router.py` | `792f77e570f7625aa49eafd2634eef21001e6a6fa9225c8514bca4bd57b48557` |
| `backend/app/modules/requirement_links/__init__.py` | `a652b28a1b7ee6d371adc3e5df9a2bc6b75d554164e97a248d28c3ef154fee0d` |
| `backend/app/modules/requirement_links/router.py` | `9062ea0e537855650c23612e2bb99e77c932db10b5180bce0e41f0ed17dd6e1f` |
| `backend/app/modules/requirement_links/schemas.py` | `b5855e2d8e1a0f8097b686abe8d1866ecea1970a73c172f52f27b6a66c8f840a` |
| `backend/app/modules/requirement_links/service.py` | `699b65750e71ba68e0d8b86f04ddb5d39207601caae262c885be735562582d6c` |
| `backend/app/modules/requirements/schemas.py` | `6f0f854fb8751f39b116131f6fddaa0af443e40be1906815d1463dbc7086d5a5` |
| `backend/app/modules/requirements/service.py` | `0abced32fb5a4e7a103a6d6d4bec64226ecf8c4c0bff65b48e8c9ad1bae16f54` |
| `backend/app/modules/test_cases/models.py` | `6055617c9b4cf97e44bbfc412afa6f1bece4a0aa371994b86d25ae2dd96c13c3` |
| `backend/app/modules/test_cases/schemas.py` | `b1c0cec0797738f6bb02c9350c69f9df03f25ba2faaca2ece8a697f17aa77691` |
| `backend/app/modules/test_cases/service.py` | `267f093d00997e982aad05878b5c93f3704e2f7e824906d92d8ea0d26331af82` |
| `backend/migrations/versions/20260910_0039_requirement_impact_links.py` | `857ab58f765246203c6f72eef7f99e55b76c8d3e15c598b64c83470a2017b316` |
| `backend/tests/test_ai_case_generation.py` | `e356dcb1865914d24f37c2146d862b0cf0d8c5307721a01f6e5e1174c2eab63e` |
| `backend/tests/test_requirement_links.py` | `4dadb60419fa2dd235eea084fd5bc0f7d4f3284bf8f93a9e687c6da81f417b27` |
| `backend/tests/test_requirement_links_migration.py` | `94b0d655b7fe16c6a7cdbbe038d13641c3bf9cc723a0cffd2b5f196ce16914f8` |

## 执行命令

从工作区根目录执行一次性验证器：

```powershell
.\.venv\Scripts\python.exe .codex-validation\p6-i1m\p6_i1m_validate.py
```

验证器在 `backend` 目录对各独立场景数据库执行下列真实 Alembic 命令：

```text
python -m alembic -c alembic.ini upgrade 20260909_0038
python -m alembic -c alembic.ini upgrade 20260910_0039
python -m alembic -c alembic.ini downgrade 20260909_0038
```

空库和旧数据库分别执行完整往返；三个有损降级场景的 downgrade 返回码均为 1，且均出现预期拒绝标记 `Cannot downgrade requirement links`。全部输出只保存字符数和 SHA256；未执行 `SET FOREIGN_KEY_CHECKS=0`。

## 逐场景结果

### 1. 空库与结构反射

- 空库从 base 实际升到 `20260910_0039`，再执行 `0039→0038→0039`，所有命令返回 0。
- 结构反射确认 0039 新列的可空性和默认值：`asset_type='TEST_CASE'`、`status='ACTIVE'`、`active_slot=1`、`created_at_time_basis='LEGACY_UNKNOWN'`，其余新引用/审计列可空。
- 反射确认 7 个 FK：需求/需求版本、TestCase/TestCaseVersion、WebCase/WebCaseVersion 和自引用 supersedes；`CASCADE` / `SET NULL` / `RESTRICT` 目标和动作均匹配。
- 反射确认 4 个 CHECK、2 个活动唯一约束、状态复合索引和 4 个新 FK 支撑索引。
- 空表实际降级成功，证明 MySQL 接受迁移中的 FK 先于依赖索引删除顺序。

### 2. 0038 非空旧数据与兼容往返

- 在 0038 插入 2 条旧关联：API TestCase `id=1`、旧 WEB TestCase `id=2`；同时存在独立 WebCase `id=1` 作为同数字 ID 对照。
- 旧关联全部原字段指纹在升级前后一致：`fe69622a5858df19de59fed563aeb621f9bb0355092ddb83e245968913964e24`。ID、`source`、`confidence`、`relation_type`、`created_at`等均保留。
- 相关内容指纹保留：RequirementVersion `a50c0352…ee57`，TestCaseVersion `124b467e…53c2`，WebCaseVersion `0d4b8bc7…65e`。
- 升级后旧行均为 `asset_type=TEST_CASE`；`case_version_id/web_case_id/web_case_version_id` 保持 `NULL`，没有根据 current 或时间猜测版本。独立 WebCase `id=1` 未被误连接。
- 旧创建时间保持 naive/unknown，`created_at_time_basis=LEGACY_UNKNOWN`，未伪造 UTC 语义。
- 仅包含旧兼容数据时，`0039→0038→0039` 往返成功，0039 首次与二次结构+数据指纹完全等价。

### 3. 有损降级拒绝

对以下 3 个独立数据库分别尝试 `0039→0038`：

1. 含新精确 RequirementVersion + TestCaseVersion 关联；
2. 含独立 WebCase/WebCaseVersion 关联；
3. 含 REMOVED 历史、审计与 supersedes 再关联链。

三者均在破坏性 DDL 前明确拒绝。拒绝前后 `alembic_version` 均为 `20260910_0039`，列集、`SHOW CREATE TABLE` 指纹、行数和全行指纹逐项相同；未删除新行换取降级成功。

### 4. 真实 MySQL 约束

- 17/17 拒绝探针通过，每次失败后同一连接执行 `SELECT 1` 均成功，证明回滚后事务可继续使用。
- 7 个不存在的需求/需求版本/资产/资产版本/前驱外键均返回 MySQL `1452`。
- 双目标、空目标、TestCase/WebCase 类型错配、`ACTIVE+NULL slot`、`ACTIVE+slot 2`、`REMOVED+slot 1`、`REMOVED+缺审计` 均返回 MySQL `3819`。
- 普通重复 ACTIVE 返回 `1062`；尝试以 `active_slot=NULL` 利用 SQL NULL 三值逻辑绕过唯一性时，先被强 CHECK 以 `3819` 拒绝。
- 同一目标同时保留 2 条审计完整的 REMOVED 行成功。

### 5. 产品服务历史与归属

- 通过实际 `create_requirement_link` / `remove_requirement_link` 执行创建→移除→再创建→再移除→再创建，链 ID 为 `9203→9204→9205`，supersedes 为 `9203→9204`。
- 终态为 1 ACTIVE + 2 REMOVED；新链接使用新 ID，老链接移除后的全字段指纹在后续操作中保持不变。
- 真实服务分别拒绝跨项目 TestCase、跨需求 RequirementVersion、不属于资产的 TestCaseVersion 和跨项目 WebCaseVersion，4/4 为服务 `ResourceConflict`，且事务可继续使用。这些对象的 FK 都真实存在，因而与“外键存在”明确区分。

### 6. 两组独立真实事务并发

- 连接使用 `READ COMMITTED`，每个并发 session 设置 `innodb_lock_wait_timeout=3`，进程 future 也使用有界超时，所有场景 `retry_count=0`。
- **并发创建**：两个独立 session 同时调用产品创建服务；结果为 1 `SUCCESS` + 1 `SERVICE_CONFLICT`，耗时 `0.063s`。终态仅 1 条 ACTIVE，两个 session 均证明事务可继续使用。
- **并发移除/再关联**：移除与再关联两侧都直接调用产品服务。在移除 DML `after_flush` 后、commit 前启动再关联，确认真实锁等待后串行化；结果为 `REMOVE_SUCCESS` + `SUCCESS`，新 ID `9209` 的 `supersedes_link_id=9208`。终态 1 REMOVED + 1 ACTIVE，两事务均可继续使用，无重复 ACTIVE、无半提交，移除响应偏移为 `+00:00`。

### 7. UTC 与三种创建路径

- **显式关联**：session `time_zone` 依次为 `+08:00` 和 `-05:00`；数据库创建/移除时间都落在 Python UTC 前后窗口内，服务响应的偏移均为 `+00:00`。
- **手工创建 TestCase 并附带关联**：session `time_zone=+09:00`；产生 Case `6`、CaseVersion `1006`、Link `9211`，精确绑定 RequirementVersion `102`，时间落在 UTC 窗口内，服务序列化偏移 `+00:00`。
- **已预置合成 AI 建议接受**：session `time_zone=-07:00`；不调用模型（`ai_gateway_calls=0`），通过实际 `decide_suggestion(ACCEPT)` 产生 Case `7`、CaseVersion `1007`、Link `9212`，精确保留生成时 RequirementVersion `101`，时间落在 UTC 窗口内，服务序列化偏移 `+00:00`。
- 旧行继续保持 `LEGACY_UNKNOWN` 和 naive 时间，没有因 session 时区切换被重解释。

## 验证器失败轮与收敛

为保留可审计的失败点，有界尝试记录保存在 `attempt-log.json`。首轮因 MySQL 8.4 将 CHECK `3819` 映射为 SQLAlchemy `OperationalError` 而非 `IntegrityError` 失败；后续两轮用于将移除侧收紧为产品服务、显式分类数据库并发冲突，并最终将同步点从 flush 前移至 `after_flush`。这些均是验证器编排问题，不是产品失败；每一失败轮都报告 `cleanup_ready=true`。最终第 5 轮通过。

## 清理与无副作用证据

- 容器、卷、网络、临时凭据文件均为 absent，`cleanup_errors=[]`。
- Docker 全局清单前后一致：容器 `6`、卷 `4`、网络 `5`，各 ID/名称列表 SHA256 前后完全相同。
- 正式服务 `ready.json` 前后均为 `89a58ad2…3eb`，`owned-processes.json` 前后均为 `2f78160a…0a9`。
- 冻结的 14 个产品/测试文件前后全部一致。

## 交付物

- 机器结果：`.codex-validation/p6-i1m/result.json`，SHA256 `cbcc73caec72eefc48469fe987f640422f556aafcbd28c7dc48225fb813e5c1f`
- 一次性验证器：`.codex-validation/p6-i1m/p6_i1m_validate.py`，SHA256 `3b99617d0c9fb8976ee0f7073ccb67abe18a290b35f241ec0211fcd07a6844f2`
- 有界尝试日志：`.codex-validation/p6-i1m/attempt-log.json`，SHA256 `39965d3e6088afbe37a1b59fc73d4f5753fc7581ec89bd6169f4dcdc3124d594`
- 本报告：`文档/06-验收记录/P6-I1M需求关联MySQL验收.md`

验收结论仅适用于控制器冻结的 P6-I1/r2 指纹和本轮 MySQL 8.4.11 隔离环境。
