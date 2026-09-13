# Phase 6 报告与 Dashboard 实施契约

2026-09-10 主控设计；本文件不是完成证明或执行授权。P5F-L2正式加载与主控复验完成后，按独立编号任务包实施。完整范围仍以总规格第46、73～77节及V1需求验收矩阵为准。

## 目标与来源

统一报告按 Run → CaseRun → StepRun → Evidence 展示API、Scenario、Web执行。只读取真实持久化结果及执行锁定的版本，不重新运行、不重新判断通过失败、不查询目标系统补造历史。现有RunCenter保留控制用途，报告提供独立、可分享路径的项目内只读页面；链接本身不能绕过登录与项目授权。

使用现有 runs、case_runs、step_runs、run_api_execution_results、run_scenario_execution_results、run_web_execution_results、artifacts、locator_healing_proposals、web_failure_analyses。第一包不复制整套执行表，也不为报告引入新状态机。项目名、环境名、Runner名如只有当前值，明确为当前元数据，不声称其在执行时已锁定；实际执行ID、版本ID、时间和结果来自历史记录。

API/Web/Scenario版本都必须从Run/CaseRun引用取值，禁止使用资产current_version_id代替旧版本。没有存储的请求正文、提取结果或旧版本内容，应显示“未记录”，不能从当前环境变量、Secret或当前资产配置重建。不要将此降级显示计为完整API运行能力已实现；正式API/Scenario执行缺口仍单独补齐。

## 第一包接口边界

建议新增 `modules/reports` 和 `modules/dashboard`，复用现有认证/项目授权和核心脱敏，不改Runner协议。

- `GET /api/v1/reports?project_id=...`：项目内分页Run报告目录，按created_at/id稳定排序；支持Run类型、终态/进行中状态、环境、创建日期筛选。参数严格校验，page_size上限100。
- `GET /api/v1/reports/{run_id}`：报告摘要、固定版本引用、CaseRun/StepRun真实状态与有界类型化结果、Evidence元数据/受保护下载路径、自愈和已有失败分析审计引用。
- `GET /api/v1/dashboard`：仅聚合当前用户可见ACTIVE项目；允许project_id进一步限定，显式不可见项目404。返回统计时间、时区、日期范围、口径及指标，不用前端拉取所有记录后计算。

报告支持已归档项目和资产的授权历史读取，但不得因此开放执行/修改。不存在和无权限的报告统一404，列表总数与聚合也必须先过滤项目权限，不能通过全局统计泄露其他项目。

报告详情需有界。CaseRun/StepRun未来增长时使用明确分页入口或返回truncated标记和继续读取参数，不能静默截掉记录。避免逐条Case/Step/Evidence查询造成N+1；禁止为列表下载MinIO正文或完整AI原始响应。

## 状态、统计和时间口径

报告Run状态和已有total/pass/fail/review/timeout原样保留为执行记录。另由CaseRun状态分组得到明示口径的展示计数，包括SKIPPED/CANCELLED和未完成数量；不能因为所有Step成功就把WEB_EVIDENCE_ERROR的平台失败改为成功，也不能把取消当作跳过。

报告用例通过率定义为SUCCESS CaseRun / (SUCCESS + FAILED + REVIEW + TIMEOUT CaseRun)，展示分子、分母；SKIPPED/CANCELLED/未完成不进入分母。分母0返回null，页面显示“暂无已评估结果”，不显示100%。统计口径必须随接口和页面明确说明，防止与Run成功率混用。

Dashboard今日范围按Asia/Shanghai自然日转换为UTC查询，返回timezone与明确UTC起止；测试午夜边界。今日Run按created_at计算；今日成功率为今日创建且已终态的Run中SUCCESS/(SUCCESS+FAILED+TIMEOUT)，CANCELLED单独显示并不进入该分母，无结果返回null。Fail统计FAILED，TIMEOUT独立字段；不把时间耗尽静默当作普通失败。

Dashboard基本指标：可见活动项目数、非归档API/Web Case数（返回分项，Scenario单独计数）、今日Run数、成功率、失败/超时数、待人工审核数、Runner在线概览、最近Run。待审核按真正待决策的建议记录分类统计，不以所有DRAFT资产冒充待审核。Runner若是全局资源，先按已有可见性规则确定展示范围；Redis不可用时返回unknown/available=false，不能记为0台在线。现有权限不支持用户查看某类Runner时不返回其细节。

历史duration只在started_at/ended_at均可靠时计算，进行中明确为当前观察值；无开始时间返回null。时间统一使用带时区的UTC响应，UI本地显示。统计刷新不修改业务状态。

源码核对后的明确边界：现有Runner列表及详情为平台ADMIN专用，Dashboard对非ADMIN返回runner统计不可见（例如visibility=ADMIN_ONLY、online=null），不能绕过权限提供全局数量。待审核分项为RequirementReview、AiCaseSuggestion、WebRecordingAiSuggestion、WebHealingProposal的DRAFT记录；按各自项目及父资产可用性过滤，不统计已归档/停用来源的不可处理草稿。单独返回四类数量及总数，不混入CaseVersion DRAFT或已完成FailureAnalysis。

## 隐私与展示

所有任意文本/JSON摘要通过L1/r2有界脱敏副本处理，保留数据库原记录。Request、Response、Trace及AI内容只使用既有安全快照/公开Schema，不能序列化ORM全部列。禁止返回StorageState、凭据、密文、MinIO内部bucket/key和未授权下载地址。

Evidence沿用授权下载和哈希/大小校验，页面先显示元数据，不自动打开原始Trace或执行任何脚本。导出与缺陷草稿后续单独任务实现：同一权限/脱敏边界，Markdown/HTML转义动态内容，HTML禁止脚本执行和外部资源自动加载，禁止任何外部缺陷提交。

## 任务顺序与验收

P6-R1先由Backend实现并隔离测试只读报告/统计契约，交付OpenAPI字段示例和准确数据口径；主控审查后冻结，Frontend再实现报告/Evidence Center/Dashboard页面。不得让Frontend在上游未定义时等待或自行发消息协调。

第一包必须验证：多项目身份隔离及统计总数、归档历史、旧版本与当前版本不同、API/Web/Scenario多类型真实历史、缺失结果、平台失败但步骤成功、取消/跳过/超时、空分母、跨日边界、Redis不可用、分页限制、敏感文本/JSON及原库不变。使用隔离SQLite和必要存储替身，不操作正式AI/Run；保持后端全量回归与Ruff通过。真实页面、实际数据接口和导出分别验收，不用Schema或mock成功替代整体交付。

后续包继续完善需求→用例→执行→证据追溯、AI生成可编辑缺陷草稿、Markdown/HTML导出和统一交互。报告第一包通过不会自动关闭Phase6或完整V1，其余认证、API正式执行、数据驱动、Web操作与Session恢复缺项仍按矩阵推进。
