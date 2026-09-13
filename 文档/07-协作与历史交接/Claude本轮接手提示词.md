# Claude 本轮接手提示词

> 用户可直接复制以下全部内容给 VSCode Claude 插件。上一版仅处理 Runner 断线的小任务提示词已经失效。

```text
你现在通过 VSCode Claude 插件，接手“AI 原生智能测试编排平台”的 Phase 4 连续开发。

用户已经明确授权：你可以在本提示词与“文档/07-协作与历史交接/Claude本轮交接任务.md”锁定的边界内，依次完成整个 Phase 4，不需要每完成一个小步骤就重新向用户申请授权。但是你不能进入 Phase 5、V1 后续增强包或 V2，不能自行增加产品范围，也不能在 Phase 4 结束后继续开发。

Codex/Sol 和四个长期 Luna 已停止本轮相关文件写入。你直接操作用户当前在 VSCode 中打开的同一项目工作区，不复制项目、不创建新 Workspace。

开始前必须确认：
- VSCode 资源管理器根目录是“AI 原生智能测试编排平台”；
- 根目录 AGENTS.md 是唯一权威 Agent 规则；
- AGENTS - 副本.md 不是当前权威规则；
- 当前没有 Codex/Luna 同时修改本轮文件。

开始前依次完整阅读：
1. 根目录 AGENTS.md；
2. 文档/当前开发状态.md；
3. 文档/07-协作与历史交接/Codex与Claude串行交接规则.md；
4. 文档/07-协作与历史交接/Claude本轮交接任务.md；
5. 文档/02-产品规格与计划/开发规范与原则.md；
6. 文档/02-产品规格与计划/项目开发计划及完成度.md 的完整 Phase 4；
7. 总规格中 Runner、调度、Executor、Run 生命周期、Evidence、实时事件、Cancel/Force Stop、Timeout/Retry 和 Runner 断线章节；
8. 每个工作包涉及的现有源码和测试。

“文档/07-协作与历史交接/Claude本轮交接任务.md”是本轮完整任务边界，必须严格遵守。若本提示词和该文件存在细节差异，以范围更严格的一项为准。

当前基线：
- Phase 4 为 85%，V1 为 72%；
- MySQL migration head 为 20260829_0025；
- Backend 交接前 207 项 pytest 全部通过，Ruff 通过；
- Runner 189 collected，187 passed、2 conditional skipped，Ruff 通过；
- Frontend 最近一次 type-check 与 production build 通过；
- API_CASE 的成功、失败、目标超时、Step Retry、RUNNING Cancel、Redis/SSE 和最小 MinIO Evidence 已通过真实链路；
- Luna 已实现管理员手动 Runner 断线收口接口，但自动触发尚未完成。

你必须按以下工作包顺序连续推进，同一时间只处理一个工作包：

A. Runner 断线自动收口
- 复用现有管理员手动接口和幂等核心；
- 后端按可配置间隔扫描；
- 只有 Redis heartbeat 明确缺失且 MySQL last_heartbeat_at 已超过 TTL 才能处理；
- Redis UNKNOWN、heartbeat 新鲜、无法证明超时、终态 Run 均不处理；
- 使用短生命周期 Session、行锁、锁后复查和幂等更新；
- 单轮异常不导致应用退出，lifespan 关闭正常停止；
- 不自动 Rerun、不迁移 Runner。

B. 通用执行隔离、Runner 总超时与真正的 Force Stop
- 建立可终止的执行隔离边界；
- 普通 Cancel 保持安全检查点和 Cleanup；
- Force Stop 必须能终止阻塞中的外部 HTTP/SQL/Script 执行，不得只是修改状态名称；
- Runner 总超时与 Step timeout 分离；
- Backend 状态机、API、审计、Runner ACK/NACK 和前端提示区分 Cancel、Force Stop、Timeout；
- Force Stop 必须明确记录 Cleanup 可能未完成的风险；
- 不把 Secret、请求正文、SQL 参数或脚本内容放入进程参数、日志和错误消息。

C. 最小 Scenario + SQL/Script Executor
- Run 锁定并投递固定 Scenario Version 快照；
- 创建前完成 Runner Capability/Tag/Slot、DSL 与引用校验；
- 支持 START/END、API、IF/ELSE、LOOP、WAIT、SET_VARIABLE、ASSERT、SQL、SCRIPT、CLEANUP；
- SQL 保持参数绑定、项目隔离、查询行数上限、读写边界、timeout、rollback/close 和脱敏；
- Script 复用受限 AST，不允许 exec/eval/subprocess/import；
- Runtime Context、失败策略、Step Retry、Cancel、Force Stop、Cleanup LIFO 与 Resource Registry 保持确定性；
- 每个节点可追溯到 StepRun，最终状态由服务端聚合；
- 新能力开放后扩展共享 Validate，未实现能力继续 fail closed；
- 不实现 Web/Playwright 节点，Web 节点必须在创建前明确拒绝并保留到 Phase 5。

D. Phase 4 真实故障与浏览器验收
- RabbitMQ 停止→Worker 重连→RabbitMQ 恢复→重新消费；
- 在途消息重复交付幂等；
- 真实慢任务期间 API Slot 在 Backend/Runner Center 显示 1→0→1；
- QUEUED Run 取消后，Runner 收到旧消息并安全 ACK；
- cancel/claim、cancel/complete、cancel/retry 关键竞态；
- 登录后的 SSE、断线续读、轮询降级和多标签页隔离；
- Evidence 列表和鉴权下载；
- Retry、retry_count、Cancel 和 Force Stop 页面反馈；
- Scenario + SQL/Script 至少一条真实成功链路和一条失败/清理链路。

E. 最终回归和 Phase 4 验收候选
- Backend 全量 pytest、Ruff、alembic current；
- Runner 全量 pytest 与 Ruff；
- Frontend type-check 与 production build；
- 新 migration 的空库升级、现有 head 升级、downgrade/upgrade 和结构检查；
- 逐项核对 Phase 4 清单；
- 更新交接结果并停止，不进入 Phase 5。

连续开发规则：
- 每个工作包必须先阅读、设计、修改、运行专项测试和静态检查；
- 当前工作包测试失败时不得进入下一个；
- 工作包通过后，把检查点增量写入文档/07-协作与历史交接/Claude本轮交接结果.md，然后直接继续下一包；
- 如果某项受环境或用户权限阻塞，记录真实阻塞并继续其他独立工作，不得伪造通过；
- 不要为每个工作包再次询问用户是否继续；本轮授权已经覆盖 A～E；
- 只有需要扩大本文件范围、删除用户数据、增加大型生产依赖或执行未授权危险操作时才停止并请求用户决定。

允许修改范围：
- backend 内 runs、runners、scenarios、与执行快照/Runtime Context直接相关的 test_cases、database_connections、resource_registry；
- backend 的 db、redis、rabbitmq、storage 适配，以及 core/config.py、main.py、api/v1/router.py；
- backend/migrations 和相关 tests，仅在数据结构确实需要时；
- runner/runner/** 与 runner/tests/**；
- frontend 的运行中心 API、types、RunCenterView及相关既有组件；
- TestCaseView 仅限 Retry/执行配置；RunnerCenterView 仅限 Slot/断线展示；
- backend/runner/deploy 的 env example、docker-compose.dev.yml，仅在 Phase 4 配置确实需要时；
- .run/** 仅在一键启动必须同步时；
- 文档/当前开发状态.md；
- 文档/07-协作与历史交接/Claude本轮交接结果.md；
- 文档/02-产品规格与计划/项目开发计划及完成度.md 仅记录真实完成项、测试和遗留项，不调整百分比；
- 总规格只同步已经实现且不改变 Scope Lock 的 Phase 4 技术事实。

明确不属于本轮：
- Web Executor、Playwright Web 自动化和自愈，它们属于 Phase 5；
- Windows Service；
- 多任务并发和 Backend 原子 Slot 预留；
- 自动 Rerun、断点续跑、跨 Runner Failover；
- JMeter、性能测试、V1 后续增强包、V2；
- Linux Runner；
- 历史 Evidence 回填和 Web 截图/Trace/Console/Network Evidence。

强制禁止：
- 不修改 AGENTS.md、AGENTS - 副本.md、开发规范和 Codex/Claude 交接规则；
- 不修改无关 .vscode/.idea 配置；
- 不执行全项目格式化、批量换行转换或无关清理；
- 不使用任何 Git 命令；
- 不创建 commit、branch、worktree；
- 不执行 reset、checkout、push、pull；
- 不删除用户业务数据、Docker Volume、MinIO Bucket 或 Runner 本机身份；
- 不读取、显示或复制 .env、数据库密码、Runner credential、Token、Cookie、Authorization、API Key 等 Secret；
- 不把 Secret 放入 RabbitMQ、日志、错误、进程参数、Evidence 或文档；
- 不新增大型框架或未经说明的生产依赖；
- 不通过大范围重构替代最小实现。

真实故障演练只允许操作本项目开发服务。可以在确认没有用户真实 Run 执行后停止并恢复现有 RabbitMQ 容器，但不得删除容器或 Volume，结束时必须恢复 Redis、RabbitMQ、MinIO healthy。测试数据使用明确前缀，并只清理本轮创建的数据。

每个工作包至少运行相关专项测试。最终从对应目录运行：

Backend：
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\ruff.exe check --no-cache app migrations tests
..\.venv\Scripts\python.exe -m alembic current

Runner：
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\ruff.exe check --no-cache runner tests

Frontend：
npm run type-check
npm run build -- --configLoader runner

你不能自行把 Phase 4 或 V1 百分比改为 100%。完成所有可执行工作后，仍保留 Phase 4=85%、V1=72%，等待 Codex/Sol 根据实际结果最终验收和更新。

完成后必须创建或更新文档/07-协作与历史交接/Claude本轮交接结果.md，按工作包列出：
- 实际修改文件和主要实现；
- migration、配置和依赖变化；
- 每条测试命令、测试数量、结果和耗时；
- MySQL/Redis/RabbitMQ/MinIO/Runner/浏览器真实验收结果；
- 未执行测试及原因；
- 遗留问题、潜在风险、未完成部分和范围外发现；
- 给 Codex/Sol 的逐项 Code Review 建议。

完成全部 Phase 4 可执行工作后立即停止所有修改，不进入 Phase 5。最终回复和交接结果末尾必须明确写：

“本轮 Claude Phase 4 开发已结束，已停止继续修改，等待 Codex / Sol 接管。”
```
