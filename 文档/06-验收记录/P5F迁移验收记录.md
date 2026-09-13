# P5-F 独立 MySQL 迁移验收记录

> 验收日期：2026-09-09  
> 数据库：独立临时 MySQL 8.4 容器  
> 目标范围：`20260904_0031`～`20260909_0038`  
> 状态：完整验收通过

## 1. 隔离与安全边界

- 使用唯一 `p5f-mysql-<随机 ID>` 容器和独立临时 Volume；端口由本机动态选择并仅绑定 `127.0.0.1`。
- 随机数据库凭据只写入本轮 `.codex-validation/p5f-mysql-*/mysql.env`，Docker 通过 `--env-file` 读取；Alembic 只使用当前 PowerShell 进程的临时 `APP_DATABASE_URL`。
- 不读取或修改正式 `.env`，不连接或降级本机 `127.0.0.1:3306` 正式开发库。
- 不启动、停止或重建现有 Redis、RabbitMQ、MinIO 容器，不复用现有 Compose Volume。
- 无论成功或失败均在 `finally` 中删除本轮容器、Volume、凭据文件和运行目录，并再次查询 Docker 与工作区确认无残留。

## 2. 验收工具

- `deploy/p5f_mysql_migration_validation.ps1`：创建隔离实例、调用 Alembic、编排逐版本往返并执行精确清理。
- `deploy/p5f_mysql_migration_verifier.py`：通过 MySQL `information_schema` 核对 revision、表、列、外键、CHECK 和索引；使用合成数据验证 `0035`/`0036` 凭据与渠道回填、`0036` 降级恢复、`0037` 非空 `max_context` 恢复及 `0038` 分类约束。
- Python 验收辅助脚本已通过 Ruff `--no-cache` 和 Ruff format 检查；PowerShell 脚本已通过解析检查。

## 3. 第一轮真实执行结果

执行入口：

```powershell
& .\deploy\p5f_mysql_migration_validation.ps1
```

实际结果：

1. Docker Engine `29.7.2` 可用；本机拉取并使用官方 `mysql:8.4` 镜像。
2. 独立实例达到 `healthy`，未占用 3306。
3. 空库执行 `alembic upgrade head` 成功，从 base 完整迁移到 `20260909_0038`。
4. `0038` 状态的表、列、关键 FK、CHECK 与索引结构断言通过。
5. 执行 `alembic downgrade 20260902_0030` 时，在 `20260908_0036` 降级失败：MySQL 错误 1553，`ix_model_configurations_connection_id` 仍被外键依赖，不能先删除索引。
6. 失败原因为 `0036` migration 的 downgrade 顺序是先删除索引、后删除外键；该问题已报告主控和 Backend，由 Backend 修改 migration 并补回归。DevOps 未修改 Backend 文件。
7. 失败后临时容器、Volume、凭据文件和 `.codex-validation/p5f-mysql-*` 目录均已删除；额外查询未发现同前缀残留。

## 4. 第一轮当时判定（历史）

- 已通过：真实 MySQL 8.4 空库全量升级到 `0038`、head 结构断言、失败路径精确清理。
- 未通过：`0031`～`0038` 完整降级及逐版本再升级、非空数据保护全链路。
- 当时在 Backend 修正 `0036` 前，不把 P5-F 独立 MySQL 迁移往返标记为完成；后续每轮均从全新隔离实例重新执行，没有从失败实例续跑。

## 5. 第二轮真实执行结果

Backend 将 `0036` 调整为先删除外键、再删除依赖索引后，使用另一个全新随机实例重新执行相同入口：

1. 空库再次成功升级到 `0038`，head 结构断言通过。
2. `0038→0037→0036→0035→0034` 的降级已继续执行。
3. `0034→0033` 在 `20260908_0034_web_failure_analyses.py` 失败：MySQL 错误 1553，`ix_web_failure_analyses_case_run_id` 仍被外键依赖，不能在删除表之前先删除该索引。
4. `0031`～`0034` migration 使用相似的“先逐项删除索引、最后删除表”模式，已要求 Backend 一次性检查并修正，不逐个绕过真实错误。
5. 第二轮失败后，新的临时容器、Volume、凭据文件和验收目录再次全部删除，无同前缀残留。

同时按主控预审加固验收工具：

- verifier 在建立连接前强制要求 `mysql+pymysql`、`127.0.0.1`、非 3306 端口、数据库名 `p5f_validation` 和用户 `p5f_validator`；不符合时以固定安全错误拒绝，不回显 URL 或凭据。
- PowerShell 清理阶段分别捕获环境恢复、凭据文件、容器、Volume 和目录删除错误；单项失败不会跳过后续清理，最后统一报告。
- 已使用指向 3306/正式库名的合成 URL 验证隔离守卫会在连接前拒绝。

## 6. 第三、四轮工具与迁移验证

第三轮新增 MySQL 超期候选表达式探针时，未按真实列类型声明绑定的 `started_at`，导致工具自身的精度断言提前失败；该轮只完成空库升级和 head 结构断言，未进入迁移往返。实例及全部临时资源已清理。随后按 `information_schema` 返回的真实列精度修正探针，不把该工具问题归因于 Backend。

第四轮使用全新实例，实际进展如下：

1. 空库从 base 升级到 `0038` 通过，head 结构断言通过。
2. 真实 `runs.started_at` 为 `DATETIME(0)`；MySQL `TIMESTAMPADD(MICROSECOND, total_timeout_ms * 1000 + grace_microseconds, started_at)` 对 1501 ms 预算保留毫秒，并满足截止前不超期、等于截止不超期、超过截止才超期的严格边界。
3. `0038→0030` 完整空数据降级通过，随后 `0030→0031→0032→0033→0034` 逐条再升级及结构断言通过。
4. 在 `0034` 插入依赖旧 schema 默认值的合成模型时，MySQL 返回 1364：`max_context` 没有默认值。
5. `0008` 原契约为 `max_context NOT NULL DEFAULT 128000`；`0037` downgrade 只恢复了非空约束，没有恢复旧默认值，因此回退后的 schema 不等价。该问题已交由 Backend 修复并补回归；验收工具增加了 revision 低于 `0037` 时对 `NOT NULL DEFAULT 128000` 的显式断言，不通过显式填写字段绕过。
6. 第三、四轮结束后均确认无临时容器、Volume、凭据文件或验收目录残留。

第五轮在增加 `max_context` 默认值结构断言后，`information_schema` 返回字段名的大小写映射与工具假设不同，导致 head 结构断言中的辅助字段读取失败。该轮尚未进入迁移往返，实例及临时资源已清理；工具随后改为按查询列位置读取 nullability/default，避免驱动字段名差异。

## 7. 第六轮真实非空迁移结果

修正辅助字段读取后，使用第六个全新实例执行：

1. 空库 base→`0038`、head 结构和真实 MySQL 超期边界再次通过。
2. `0038→0030` 完整降级通过；验收工具确认 revision 低于 `0037` 时 `max_context` 已恢复为 `NOT NULL DEFAULT 128000`。
3. `0030→0031→0032→0033→0034` 逐条升级通过；在 `0034` 插入省略 `max_context` 的旧式合成模型成功，证明默认值兼容性已恢复。
4. `0034→0035` 通过；合成 Secret 的加密值与指纹正确回填到旧模型新增字段。
5. 非空 `0035→0036` 在 `_backfill_connections()` 中失败：轻量 `sa.table()` 未声明主键元数据，MySQL 插入结果的 `inserted_primary_key` 为空，代码访问第一项时触发 `IndexError`。
6. 该缺陷只在存在旧模型时进入回填循环；历史空库或零模型迁移不会触发。已要求 Backend 改用可靠的 MySQL `lastrowid` 或具备正确主键元数据的表定义，并补旧 Model/Secret 非空回填及降级保护测试。
7. 第六轮未绕过错误，所有临时资源再次清理完毕；完整往返仍未通过。

## 8. 最终完整验收结果

Backend 修复 `0036` 非空主键回取后，使用第七个全新随机 MySQL 8.4 实例执行相同入口，完整流程通过。

实际迁移命令类型与范围：

```text
alembic upgrade head
alembic downgrade 20260902_0030
alembic upgrade 20260904_0031
alembic upgrade 20260907_0032
alembic upgrade 20260908_0033
alembic upgrade 20260908_0034
alembic upgrade 20260908_0035
alembic upgrade 20260908_0036
alembic upgrade 20260909_0037
alembic upgrade 20260909_0038
逐条 downgrade：0038 → 0037 → 0036 → 0035 → 0034 → 0033 → 0032 → 0031 → 0030
逐条 re-upgrade：0030 → 0031 → 0032 → 0033 → 0034 → 0035 → 0036 → 0037 → 0038
```

共执行 26 次 Alembic upgrade/downgrade 命令，并在每个目标 revision 后执行结构断言。结果如下：

1. 空库从 base 完整升级到 `20260909_0038` 通过。
2. 空数据从 `0038` 完整降到 `0030` 通过；`0031`～`0038` 的表、列、外键、CHECK 和索引均按 revision 正确出现和移除。
3. 从 `0030` 逐条升级到 `0034` 后，成功插入一条省略 `max_context` 的旧式合成 Model 及其合成 Secret；`max_context` 使用恢复后的旧默认值。
4. `0035` 正确将旧 Secret 的合成加密值、指纹和轮换状态回填到模型新增字段。
5. `0036` 对非空旧模型创建且只创建一个 Provider Connection，正确回填 `connection_id`、模型厂商、接入类型、路由信息和合成凭据状态。
6. 修改合成 Connection 的路由和凭据状态后逐条降级，`0036` 正确把 Connection 状态恢复到旧模型字段；旧 Model、Secret 及其引用保持完整。
7. 在 `0037` 将合成模型的 `max_context` 置空后降级，迁移正确回填 `128000` 并恢复 `NOT NULL DEFAULT 128000`。
8. `0038` 分类 CHECK 在真实 MySQL 上拒绝非法值并接受 `LLM`。
9. 带非空数据逐条降到 `0030` 后再次逐条升级到 `0038` 通过；最终仍为一个 Model、一个 Secret 引用和一个 Provider Connection，关键数据与关联未丢失。
10. 真实 `runs.started_at` 为 `DATETIME(0)`。只读 SELECT 验证 1501 ms 执行预算加 60 秒宽限仍保留毫秒，截止前和等于截止均不判定超期，超过截止 1 微秒才判定超期；开始和最终 head 状态各验证一次。

最终清理与隔离复核：

- 验收脚本正常删除临时容器、独立 Volume、随机凭据文件和本轮 `.codex-validation/p5f-mysql-*` 目录。
- 验收结束后再次执行 Docker 容器/Volume 前缀查询和工作区目录查询，结果均为空。
- 正式 `127.0.0.1:3306` 未连接、未升级、未降级；现有 Redis、RabbitMQ、MinIO 未启动、停止或重建。
- verifier 的隔离白名单、Ruff `--no-cache`、Ruff format 及 PowerShell 解析检查全部通过。

最终判定：P5-F 独立 MySQL `0031`～`0038` 真实迁移往返、非空数据保护、关键结构与清理门禁通过。
