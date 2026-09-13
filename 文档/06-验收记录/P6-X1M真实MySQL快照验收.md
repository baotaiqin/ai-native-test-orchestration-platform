# P6-X1M 真实 MySQL 快照验收

## 1. 结论

- 任务包：`P6-X1M/r1`
- 结论：`PASSED`
- 完成时间：`2026-09-09T20:19:27.641577+00:00`
- 验证对象：`P6-X1/r2` 报告导出的 MySQL/InnoDB 一致只读快照分支
- 产品修改：无
- 正式服务、正式 MySQL、正式凭据与既有中间件：均未触碰

在真实 MySQL 8.4、PyMySQL 与 SQLAlchemy 上直接调用产品 `create_report_export` 路径，已证明导出使用独立 `REPEATABLE READ`、`START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY` 事务；状态与第二页字段并发更改不会造成混合快照；成功和异常后独立连接均释放；调用者已有未提交事务不被提交、回滚或替换。

## 2. 冻结源码指纹

验证前后指纹完全一致：

| 文件 | SHA-256 |
| --- | --- |
| `backend/app/modules/reports/exports.py` | `4b10127f9eb252a00ff0059a25b9ed12e9cdecb243ca0bf73a338b2d6569d836` |
| `backend/app/modules/reports/router.py` | `7ef7f789d283aa29eb33ea00aac0f76d8d28c1df7ebd68af3e16edc2fe86be2a` |
| `backend/app/modules/reports/service.py` | `57c3d5fb028bde48706b573b6e53c0e476590cec8a2804122407b9d282fa1e99` |

## 3. 隔离运行环境

| 项目 | 值 |
| --- | --- |
| Docker Server | `29.7.2` |
| 镜像 | `mysql:8.4` |
| 镜像 ID | `sha256:3466ba4a4828aa8d46fb7c3bc16b67b781c98413cf4ea0fac6feaa6e881faa26` |
| MySQL | `8.4.11 Community Server` |
| SQLAlchemy | `2.0.52` |
| PyMySQL | `1.2.0` |
| 容器 | `p6-x1m-mysql-e625b8a045ce` |
| 具名卷 | `p6-x1m-mysql-e625b8a045ce-data` |
| 隔离数据库 | `p6x1m_e625b8a045ce` |
| 主机绑定 | `127.0.0.1:58044 -> 3306` |
| 标签 | `com.openai.codex.package=P6-X1M-r1`、`com.openai.codex.run=e625b8a045ce` |

容器只使用一个明确具名卷挂载：`p6-x1m-mysql-e625b8a045ce-data -> /var/lib/mysql`，没有匿名卷。临时口令只写入唯一临时 env 文件供 `docker create` 读取，容器创建后立即删除；口令、DSN 与容器环境未写入证据或控制台输出。

## 4. 真实模型与产品路径

- 从项目 ORM 的完整 `Base.metadata` 在隔离库中 `create_all`，共 52 张模型表；没有复用 SQLite 数据库，也没有运行正式迁移逻辑。
- 使用完全合成的 Project、Member、Environment、Runner、API TestCase/Version、TestRun 数据。
- 播种 105 个 CaseRun 和 105 个 StepRun，确保完整导出跨越 `page_size=100` 的第二页。
- 直接调用冻结产品函数 `app.modules.reports.exports.create_report_export`；并发注入点仅包装实际 `list_report_cases` 与 HTML renderer 以提交独立连接变更、读取安全快照指标，数据加载和导出仍走产品服务。
- 初次隔离试运行暴露了 SQLite 种子一次性 flush 在真实 MySQL 外键下的排序差异；该次运行在正式验证前停止且完整清理。验证器改为 Project → 引用 → Case → Version → Run → CaseRun → StepRun 的分层 flush 后，最终独立运行通过。这不是产品导出缺陷。

## 5. 一致快照并发证据

### 5.1 Case 状态竞态

三个同时独立的 MySQL 连接：

| 角色 | Connection ID |
| --- | ---: |
| 调用者已有事务 | `9` |
| 产品导出一致快照 | `13` |
| 并发写入者 | `14` |

导出详情统计读取后，写入者将首个 Case 从 `SUCCESS` 提交为 `FAILED`。数据库新连接确认状态已为 `FAILED`，但同一次导出的详情汇总和完整 105 个 Case 仍全部是原始 `SUCCESS`：

- 详情 success：`105`
- 完整导出 success：`105`
- 完整导出 failed：`0`
- 导出字节数：`423915`
- 导出 SHA-256：`0573af0b94a36eef838f89cf36d13c27b627158152a7e792627366d74b0ee1d3`

快照连接语句审计为一次精确 `START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY` 加 57 条 `SELECT`，无产品 DML。

### 5.2 第二页字段竞态

- 产品快照 Connection ID：`9`
- 并发写入 Connection ID：`13`
- 写入时机：完整 Case 分页读取第 2 页之前

写入者把最后一个 Case（ID `961105`）字段从 `P6_X1M_ORIGINAL_SECOND_PAGE_VALUE` 提交为 `P6_X1M_CHANGED_AFTER_FIRST_PAGE`。数据库新连接看到新值，但快照对象和最终 HTML 均只包含旧值，且第 1/100/101/105 条 ID 顺序为 `961001/961100/961101/961105`。

- 完整 Case 数：`105`
- 导出字节数：`423915`
- 导出 SHA-256：`9b9c7fdb35f5dfae3972eb004b8906c1fcc1a83fbb8cac52f4c17e4e9a8521f8`
- 快照语句：一次精确只读一致快照事务边界 + 56 条 `SELECT`

因此，第二页并发变化没有与第一页或详情统计混入同一份导出。

## 6. 事务、异常与连接池

### 6.1 只读与隔离级别

- 真实连接 `9` 执行产品 `_begin_consistent_read` 后，`@@transaction_isolation=REPEATABLE-READ`。
- 在该事务内尝试写入隔离探针，被 MySQL 错误 `1792` 拒绝，证明 `READ ONLY` 生效。
- 引擎基准隔离级别在验证前后均为 `READ-COMMITTED`，证明导出连接的 `REPEATABLE READ` 没有污染连接池其他用途。

### 6.2 调用者事务

成功导出与字节上限异常两条路径均在调用者已有事务和未提交探针更新的条件下执行。两条路径全部确认：

- `SessionTransaction` 对象保持同一个且仍 active；
- 调用者可以继续执行和读取下一次未提交更新；
- 其他连接看不到调用者未提交值；
- 调用者主动 rollback 后基准值仍为 `baseline`。

### 6.3 连接释放与异常

- 成功导出期间连接池 checked-out 数从调用者基线 `1` 回到 `1`。
- 跨页导出从 `0` 回到 `0`。
- 将产品 `MAX_EXPORT_BYTES` 临时设为 `1` 后，实际导出稳定抛出 `REPORT_EXPORT_LIMIT_EXCEEDED`、HTTP `413`、resource `utf8_bytes`；异常后连接池从调用者基线 `1` 回到 `1`，调用者事务仍可继续。
- 全部 Session 结束后 pool checked-out 为 `0`。
- 三次产品快照的业务语句均只有 `SELECT`，另有精确一致只读事务边界；没有 INSERT、UPDATE、DELETE 或 DDL。

## 7. 资源清理与正式环境隔离

最终独立复核：

- 匹配本包标签的容器：`0`
- 匹配本包标签的卷：`0`
- 随机端口 `58044` 监听：`0`
- 临时 credential 文件：`0`
- Docker 容器清单前后：数量 `6` 且 ID 集合 SHA-256 均为 `34d30a2b8da624c58f00b9e73daeebb4453beeb78f1b90b7c11351549e8aac99`
- Docker 卷清单前后：数量 `4` 且名称集合 SHA-256 均为 `63e6edc007261bfbe8492d1159b408e8de57546fb34bd756012411f1ffa3fbb4`
- 正式 ready 清单前后 SHA-256：`d431be67d8bf90a0665b117cee11ef44c3768aedd2534305f6ef118d0d94c086`
- 正式 owned 清单前后 SHA-256：`47f4ae34d688f276032549e7187c35653ac9349c8270ba402d14a6d46e97b856`

本包未加载 P6-L1，未操作 Backend、Worker、Frontend、Demo、正式 MySQL 或现有 Docker 资源。

## 8. 交付范围与指纹

本任务包只写入：

- `deploy/p6_x1_mysql_snapshot.py`
- `.codex-validation/p6-x1-mysql/result.json`
- `文档/06-验收记录/P6-X1M真实MySQL快照验收.md`

| 产物 | SHA-256 |
| --- | --- |
| `deploy/p6_x1_mysql_snapshot.py` | `eb4751a60dda5c19680d79cd6f855f062281f740e2e01e875a36ef686fc382a6` |
| `.codex-validation/p6-x1-mysql/result.json` | `cf5595d8c1c7bea541ee493377efb0a97a16811ce88b67b4c0efda05ed1029d5` |

静态验证：Ruff 通过，`py_compile` 通过；其精确 `.pyc` 临时产物已删除。

## 9. 未通过项与剩余事项

- 未通过项：无。
- 产品缺陷：未发现。
- 阻塞：无。
- 剩余事项：由主控复核本报告与机器证据；本执行任务不自行加载 P6-L1，也不扩大到下一任务包。
