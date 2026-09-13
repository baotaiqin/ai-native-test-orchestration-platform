# P6-I2M/r1 运行需求快照真实 MySQL 交付

## 结论

P6-I2M/r1 隔离门禁通过。最终通过尝试为 `20260910T121059-2bdc1129b57c`，状态 `PASSED`；使用 `mysql:8.4` 实际运行 MySQL 8.4.11。25 项冻结输入在执行前后全部匹配，控制器审查输入、正式 `ready/owned` 清单与 P5-E 保护历史摘要前后不变。

本结论只授权主控继续审查，不授权正式数据库迁移或服务加载。正式清单记录的数据库版本仍为 `20260909_0038`；本包没有连接、迁移或重启正式数据库/服务，没有访问共享 Demo，没有导入正在变化的 Runner 执行器，也没有真实 RabbitMQ、Redis、对象存储、浏览器或 AI 调用。

## 冻结输入与运行环境

- 冻结清单：`.codex-validation/p6-i2m-frozen-inputs.json`，25/25 SHA256 匹配，执行前后相同。
- 上游审查：`.codex-validation/p6-i2-controller-r2-review.json`，执行前后 SHA256 均为 `bd1624f7132b12f3dbc4fcc51f148ef7c60debc52e50a23a4508638c2c798a9b`。
- 最终验证器 SHA256：`33f7dbbc8824914ad7e244febfb1439f4d027f3b4d0e07bcaf5aad79167550d8`。
- 运行组件：MySQL 8.4.11、Docker Server 29.7.2、Python 3.12.14、SQLAlchemy 2.0.52、PyMySQL 1.2.0、Alembic 1.19.1。
- 实例仅绑定最终尝试的随机回环端口 `127.0.0.1:50356`，容器、卷、网络均带 `P6-I2M-r1` 和随机运行标签。

## 迁移与旧历史

真实执行以下成功命令：

1. `alembic upgrade 20260910_0039`
2. 在 0039 写入合成旧 Project、Requirement/Version/Link、API/Web/Scenario 资产以及旧 Run/CaseRun。
3. `alembic upgrade 20260910_0040`
4. 空新增表条件下 `alembic downgrade 20260910_0039`
5. `alembic upgrade 20260910_0040`

旧表逐表行数和内容摘要在首次升级、空表降级及再升级后均保持一致；两次 0040 结构摘要相同。旧 Run 的 HTTP 报告与 Markdown 导出均显示 `NOT_RECORDED`、`captured_at=null`、来源总数 0，没有依据当前关联回填。

新增表非空后再次执行降级，命令按预期以退出码 1 拒绝。拒绝前后迁移版本仍为 `20260910_0040`，捕获头、条目、Run、CaseRun 和旧关联表的行数及内容摘要完全相同，证明在任何破坏性 DDL 前停止。

## MySQL 结构与约束

实际 `SHOW CREATE TABLE`/Inspector 证明：

- 捕获头唯一约束为 `uq_run_req_captures_case_run`，索引为 `ix_run_req_captures_target`。
- 条目唯一约束为 `uq_run_req_sources_capture_seq` 与 `uq_run_req_sources_capture_link`，索引包括 `ix_run_req_sources_requirement` 与 `ix_run_req_sources_capture_order`。
- 头到 CaseRun 的外键名为 `fk_run_req_captures_case_run`；条目到头的外键名为 `fk_run_req_sources_capture`。
- 条目表没有指回实时 Requirement/Version/Link/Case 资产的外键，符合冻结设计；头/条目的所属关系外键保持有效。
- `supersedes_link_id=NULL` 实际可写并回滚；值 0 被 CHECK 以 MySQL 3819 拒绝。
- 状态/计数与需求版本绑定形状错误均以 3819 拒绝；重复头、重复序号、重复原关联以 1062 拒绝；头或条目孤儿写入以 1452 拒绝。
- 全程没有关闭 `FOREIGN_KEY_CHECKS`。

## 实际 HTTP、快照与导出

在真实 MySQL Session 上，身份、心跳、事件流、Publisher 与对象存储使用明确受控替身；应用生命周期协调器为只记录 start/stop 的空实现。实际走过 Run 创建、Report 读取、需求来源分页、Markdown/HTML 导出、Dispatch 和 Claim。

- API_CASE 捕获 `REQ-API-EXACT`、`REQ-API-LEGACY`、`REQ-API-SAME-ID`；精确需求版本与执行版本正确，资产级旧关联保留 `REQUIREMENT_VERSION_UNKNOWN`、`ASSET_VERSION_UNKNOWN` 和 `LEGACY_UNKNOWN` 时间口径。
- 独立 WEB_CASE 与 API Case 使用相同数字 ID 1，但只捕获 `REQ-WEB-EXACT`、`REQ-WEB-LEGACY`；没有跨资产类型泄漏。其他精确版本关联被排除。
- 无来源 API Run 为 `CAPTURED_EMPTY`；Scenario 为 `UNSUPPORTED_TARGET`，二者均有 UTC 捕获时刻，不与旧 Run 的 `NOT_RECORDED` 混淆。
- 首次 API 快照的报告来源摘要在标题/current、需求新版本、关联移除/重关联与需求归档后仍为 `32557d56621b6b56234980cad42d2a25426554c8f0ad4a8b73f13205892106d3`；旧报告及两种导出不出现新标题或新关联。新 Run 捕获新关联集合和需求版本 102。
- 捕获来源由一条带 Requirement/RequirementVersion 外连接的 SELECT 完成，未读取私密正文；合成私密标记没有出现在 HTTP、导出或证据 JSON 中。
- Dispatch 重复提交返回同一 message，Publisher 只记录一次；Claim 重复提交返回 `idempotent=true`，捕获头仍唯一。

## 1000/1001 与 16 MiB 边界

- 1000 条来源创建成功；10 页 × 100 条得到 1000 个唯一且有序的来源，顺序摘要为 `25f6d6a1086af039184a3a61b42820e836c165068fa0f895b583b9c711e1bf5b`。
- 1000 条 Markdown 导出 483,809 字节，SHA256 `91997386c18fa709120eb900cc78c5c5346ba0b01a9a3c0be60c6ab8a28e86d1`；HTML 导出 842,292 字节，SHA256 `4fcb97d7bac2c7e0c86e2489bfb89fa6cb0104d540d353d1eb316ffdc3a5b13a`，均包含首尾来源且无缺项。
- 用公开报告模型实际输出的 10,000 条 StepRun 与 5,000 条 Evidence 构造超过 16 MiB 的产物，HTML 导出受控返回 413 / `REPORT_EXPORT_LIMIT_EXCEEDED` / `resource=utf8_bytes`，未返回缺项文件。
- 1001 条来源创建返回 409 / `RUN_REQUIREMENT_SOURCE_LIMIT_EXCEEDED`，Run、CaseRun、StepRun、捕获头、条目和 Outbox 的前后计数完全相同，事件数不变，无静默截断。
- 跨项目来源返回 409 / `RUN_REQUIREMENT_SNAPSHOT_INVALID`；受控捕获异常返回 500 / `INTERNAL_ERROR`。两者均验证全部相关行回滚、无 Outbox/事件，并在恢复产品函数后成功创建新 Run。

## RR/RC 与故障恢复

MySQL 服务端默认隔离实际为 `REPEATABLE-READ`，控制组显式为 `READ-COMMITTED`；捕获、写入和下一语句分别记录真实 connection_id，三个相关连接互不相同。

- RR：先在捕获连接建立旧 InnoDB 视图，再由另一连接提交关联移除/重关联、需求新版本和标题更新；随后的一条 joined SELECT 捕获完整旧组合。新事务捕获完整新组合。
- RC：在 joined SELECT 已执行但消费结果前让另一连接提交同样变化；该语句返回完整旧组合，下一条捕获语句返回完整新组合。Link ID、需求版本、资产版本、标题、hash、关系类型和来源均没有新旧混搭。
- 实际持有合成 TestCase 父行锁并设 `innodb_lock_wait_timeout=1`，HTTP 创建真实触发 MySQL 1205；当前接口如实返回 500 / `INTERNAL_ERROR`。失败前后 Run/CaseRun/StepRun/捕获头/条目/Outbox 与事件计数一致；释放锁后新请求返回 201。
- 本包未复现真实 1213，也没有用故障注入冒充死锁；受控捕获异常单独标注为 fault injection。

## 尝试历史与清理

`index.json` 保留 7 次不可变尝试：前六次均为验证器种子顺序、响应字段读取或超限数据构造问题，最后一次通过。失败尝试没有覆盖为成功，每次 `cleanup_ready=true`。

最终尝试清理结果：容器、卷、网络和凭据文件全部不存在；Docker 全量容器/卷/网络清单摘要与运行前一致；正式 `ready/owned`、P5-E 只读证明、正式结果和 AI 调用账本哈希均未变化。未发现序列化的 DSN、MySQL root 密码、Runner credential 或合成私密标记。

## 交付证据

- 最终 attempt：`.codex-validation/p6-i2m/attempts/attempt-20260910T121059-2bdc1129b57c.json`
  - SHA256：`bbac5c0e2c51c64055b5e6a5ecb715da543ea045b7fa5af66bc1a397a2c08eaf`
- 尝试索引：`.codex-validation/p6-i2m/index.json`
  - SHA256：`1f27ee7e52d8e1333183568690d586435a643ed0f2e308dc53b7dc1f6cf7390e`
- 可重复验证器：`.codex-validation/p6-i2m/p6_i2m_validate.py`
  - SHA256：`33f7dbbc8824914ad7e244febfb1439f4d027f3b4d0e07bcaf5aad79167550d8`

最终判定：`P6-I2M/r1 PASSED`，等待主控独立审查；正式库继续保持 0038，未授权正式迁移或加载。
