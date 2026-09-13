# P5-F 主控验收记录

> 更新：2026-09-10。P5-F 原工作包已最终验收，见《Phase5原工作包最终验收》。下文保留各轮当时状态，未完成项已由该最终记录收口；完整V1缺项继续按矩阵开发。

## 已完成的主控审查

- 已核对启动指南及 `文档/06-验收记录/P5F启动指南核对记录.md`，命令、解释器、Web Slot、Evidence 与当前迁移头已对齐；Dashboard 的早期引导文案仍待产品页面阶段处理。
- Runner 修复后交付全量 `323 passed，2 conditional skipped`，Ruff 通过。主控审查后要求删除 Evidence 失败特殊路径中合成 trace 的分支，已核对实际修复与补充测试。
- 后端超期预审发现空预算使用当前默认值可能改变历史执行截止，已要求固定持久化新执行预算、跳过无可靠预算的旧 Run，待交付和复核。
- 真实 MySQL 发现 `0036` FK/索引删除顺序，以及 `0034` 删除整表前先删除 FK 依赖索引的问题。已授权 Backend 修复 `0031`～`0034` 同类问题及 `0036`，由 DevOps 全新实例重跑。
- 非空数据复验继续发现 `0037` 回滚未恢复旧列默认值、`0036` 旧模型回填获取新渠道主键失败；均由 Backend 在明确授权文件内修复，再由 DevOps 使用全新实例复验。空库升级成功不能替代非空数据迁移通过。

## 跨模块真实 Chrome 契约验证

验证代码：`.codex-validation/test_p5f_contract_review.py`。

从项目根目录执行：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:APP_RUNNER_DISCONNECT_SCAN_ENABLED='false'
$env:PYTHONPATH="$PWD\backend;$PWD\backend\tests;$PWD\runner;$PWD"
& .\.venv\Scripts\python.exe -m pytest .codex-validation/test_p5f_contract_review.py -q -p no:cacheprovider --tb=short --show-capture=no
```

最终扩展验证结果：**4 passed，18.49 秒**。范围为真实 Chrome/Runner 隔离进程、真实 RunnerClient 协议和后端 ASGI 路由/事务；后端数据库为独立内存 SQLite，RabbitMQ、心跳与对象存储使用测试替身，不连接正式服务。

1. Chrome 访问本机独立演示目标并生成真实成功 trace；对象存储替身返回不可用，上传有限重试耗尽后，Runner 提交 `FAILED / WEB_EVIDENCE_ERROR`，后端 Run/CaseRun 为 FAILED、StepRun 与审计 trace 保持 SUCCESS。
2. 同样的 Evidence 故障下，注入后端已提交但首次 completion 响应丢失；Runner HTTP 有限重试取回同一完成结果，消息确认成功。
3. completion 已提交但所有响应均丢失时，Runner 有限重试耗尽后重投；后端真实 claim 拒绝终态 Run，不再次执行浏览器，已提交的失败结果与成功步骤保留。
4. completion 接口始终不可达时，Run 保留 RUNNING，重投不能重新启动浏览器；使用持久化预算和注入时间调用真实后台收敛核心后，Run/未结束节点为 TIMEOUT，不生成虚假的 Web 执行结果或真实 trace。

四项均核对有限上传次数、完成调用次数和临时 Evidence 文件清理。测试未绕过 Runner 响应字段/身份关联检查。Ruff 通过。

本验证不等同于真实 MySQL、RabbitMQ、MinIO 联动或完整 P5-E 自愈验收。演示目标自身另有 4 项测试通过，见 `文档/01-使用指南/演示系统使用说明.md`。

## 尚待完成

- P5-E 真实 Chrome 自愈矩阵、其他 P5-F 门禁和遗留诊断 Run 的可靠取证处理。

2026-09-10收尾门禁细化（尚未整体验收）：

| 门禁 | 当前证据 | 还需要的结果 |
|---|---|---|
| 自愈与只读失败分析 | H5/H6/H7及H4全部验收，Analysis2/AiCall20真实成功，前端完整审计通过 | 当前限定CLICK场景通过；不替代完整登录/CRUD/Session及最终场景D |
| Web队列重连 | Q1已验收：两次真实断线/重连、浏览器动作各1次、终态重投不重执行，资源清理已独立核对 | 后端部分是受控HTTP夹具；与既有主控真实Backend claim/completion验证结合使用，不声称正式服务全链在本轮重跑 |
| AI历史展示与平台日志 | 主控隔离复现两处脱敏失败 | 独立修复包、原失败复现转通过、保留审计/诊断语义与项目隔离 |
| Evidence/Trace/运行快照/SSE | 真实Chrome遮罩、Console已知填写值脱敏和原跨模块证据错误矩阵已有；事件解析过滤未知字段通过 | 按最终版本复核现有覆盖，缺少的跨路径隐私与权限验证补齐，不能扩大已有单项证据结论 |
| 遗留与验收资产 | 已知旧Run已有真实预算收敛证据；本轮两个attempt保留 | H4之后按两份资产清单精确归档，保留Run/AI/Evidence历史，不清空共享队列或用户资产 |

当前发现的两处隐私缺陷详见 `文档/06-验收记录/P5F隐私主控审查记录.md`。不因功能主链成功而跳过这些门禁，也不把日志审查中新发现的缺项写成已完成。

Q1主控审查：Runner已completed/idle。已读真实Worker/Consumer/Pika/Chrome路径、服务端关闭命令、旧channel ACK失败、拓扑重建与broker redelivered标记及站点动作计数；两Run各动作1次，执行中断线的Run claim2次但plan/start/complete各1次。早期CLI连接数瞬时不归零是观测门禁问题，最终保留原值并以连续协议证据验证关闭，不伪造0。3项SHA256匹配；主控独立Docker只读查询确认p5f-q1前缀容器0个、最终容器与记录的首轮匿名卷均不存在；按PID和创建时间复核记录的10个所属进程均已退出。无Runner产品代码改动，相关4项回归和真实两场景证据充分，未重复执行全量测试。

## 第一轮最终验收

Backend 最终 **360 passed，34 warnings，24.75s**，Runner **323 passed，2 conditional skipped**，两端 Ruff 通过。固定预算、无预算旧 Run 跳过和候选筛选已核对。跨模块验证及指南均已审查。

DevOps 最终在全新 MySQL 8.4 完成 26 次 Alembic 命令与逐 revision 结构断言，`0031`～`0038` 空库/非空数据往返均通过；验证旧 Secret 回填、渠道主键与关联、降级回写、旧列默认值恢复、非法分类拒绝及实际 DATETIME 精度下的严格超期边界。完整结果见 `文档/06-验收记录/P5F迁移验收记录.md`。

DevOps 复核临时 Docker 容器与 Volume 已清理，主控复核临时目录无残留；主控沙箱直接查询 Docker 的权限不足，未把该次失败查询记作独立 Docker 复验。正式数据库与现有中间件未修改。四个任务均已确认 completed/idle。

历史 Phase 5 75% / V1 86.25% 仅为工作包计分，本记录不增加百分比。

## 2026-09-10 读取边界补充复核

主控新增 `.codex-validation/test_p5f_read_boundary_review.py`，独立SQLite和内存存储，仅使用合成身份/事件，没有正式数据库、Redis或MinIO写入。实际FastAPI路由证明：项目外用户访问SSE入口404；项目外用户下载执行产出的每个Evidence均404，且对象存储get_object调用数为0。另从事件生成器取得实际序列化SSE帧，注入的未知password/raw_response/cookie字段全部丢弃，事件ID与状态帧保留。3项通过（2.74秒）。这补足读取入口和帧边界，不声称完成真实Redis断线或登录后权限撤销的全链验收。既有test_runs中的事件发布/分页/恢复/心跳/异常、Web上传限制与运行快照测试已审阅，后端全量403项覆盖；后续正式认证变更应重新审计持续流权限。

正式SSE只读补验：主控正常登录后仅GET成功Run run_7852432fb7b241dabcad111664b9621b的events与events/stream。实际source=REDIS_STREAM、事件5条、SSE HTTP200/text-event-stream，首帧RUN_CREATED身份匹配project23和固定Run，敏感字段名缺失。收到首帧后关闭连接；业务写入0，无重跑或消息发布。安全摘要 `.codex-validation/p5f-formal-sse-readonly.json` 为PASSED。此项证明真实Backend/Redis/SSE读取可用，与隔离权限和未知字段过滤测试互补；不包含真实Redis断线重连或权限中途撤销测试。

L1/r2磁盘修复与C1归档已最终验收：Backend412项全量、主控34项隐私/权限组合通过；六项资产ARCHIVED，主控正式数据库只读8项检查通过，全部历史和保护文件不变。四执行任务已批量确认completed/idle。正式Backend仍未加载L1，下一步单独加载与正式只读复验，P5-F不提前关闭。

## 2026-09-10 最终加载与原工作包关闭

L2已交付并经主控验收：Backend进程身份、启动时间、源码与交付指纹匹配；17项就绪通过。主控正式只读复验9条AI历史，7个展示字段发生脱敏变化，模型/Token/成本/原审计及保护文件均保留，8项检查通过。结合L1/r2、C1、H4/Q1及前述证据，P5-F原工作包关闭。详细范围和后续缺项见《Phase5原工作包最终验收》。
