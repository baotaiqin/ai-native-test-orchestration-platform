# P5-E 第二轮主控审查记录

> 2026-09-09。当前为进行中记录，不代表本轮或 Phase 5 验收通过。

## 本轮目标

四个可见 Sol/xhigh 长期任务分别负责正式环境准备、真实自愈和失败分析、前端行为和真实页面复验、真实 Chrome 异常矩阵。职责和资产清理规则以 `文档/02-产品规格与计划/V1收尾开发与验收计划.md` 第 6 节为准。

## 主控预审与返工

1. 前端 Healing/FailureAnalysis 原使用全局 15 秒超时，现改为专用 90 秒。类型检查与隔离构建已通过；还需验证超时提示、重复生成禁用、历史刷新失败不解锁、旧请求不会污染新 Run，以及正式数据的页面展示。
2. 环境脚本每次启动后须持久化自有进程 PID/启动时间，失败不能丢失归属或误停既有服务。Worker 须按 Windows launcher 进程树计为一个逻辑 Worker，并验证常驻进程、真实服务端心跳和容量，不能用单独 heartbeat-once 代替。
3. 环境 manifest 与后端验收脚本使用一致的实际字段：逻辑 Worker 数量、ACTIVE/ONLINE 状态、WEB 能力与 total/available 容量。版本接口返回顶层列表，验收客户端已按每个端点严格区分对象和列表。
4. AI 业务调用本轮累计最多 6 次，包含失败尝试和结果未知。每次 POST 前写入账本，每次尝试保留独立资产 manifest；不得盲目从头重跑覆盖历史。正常闭环预计 3 次业务调用。
5. Runner 真实矩阵已发现首缺失 Locator 耗尽节点预算和 Console Evidence 未脱敏已知填写值。执行任务正在修复与回归；主控追加延迟元素测试，避免探测时间上限导致单候选或备用候选提前失败。
6. 截图隐私需检验真实 PNG 遮罩像素；强制停止需核对本次 Chrome 后代及临时 profile 目录。纯字节搜索或仅查询 multiprocessing 子进程不能独立证明这些行为。
7. Locator 共同节点预算内允许重复探测尚未找到的候选，但不得重复执行已尝试过的动作。纯定位探测预算耗尽、实际候选均为 NOT_FOUND、尚未执行动作且 Run 总预算仍有余量时，保持 FAILED/WEB_LOCATOR_NOT_FOUND（断言为 ASSERTION_FAILED）及真实安全上下文；Run 总预算与实际动作操作超时仍为 TIMEOUT，不附带 Healing 上下文。不能输出 TIMEOUT+healing_context 违反后端严格 schema，也不能为绕过此问题扩大后端门禁。该契约须由真实缺失/延迟元素和后端 trace 模型共同验证。

## 运行顺序与未完成事项

- DevOps 准备环境，Backend 可以准备独占合成资产；实际 Web Run/AI 闭环等 Runner 生产代码稳定后开始。
- Frontend 可先执行受控 UI 响应测试；正式页面只读复验等 Backend 产出已通过的真实结果，不额外触发 AI、接受、批准或运行。
- 主控必须等待全部任务交付，审查记录与实际源码后再安排精确归档和服务收尾。仍在运行的任务不能记为完成。
- V1 历史工作包进度保持 86.25%，本轮不以预审、脚本落盘或类型检查代替真实验收。

环境进展：DevOps 已产出 `ready.json`；Backend 实际预检通过正式库 head、中间件、服务指纹、内存登录、现有双任务模型绑定和 Prompt、唯一在线 WEB Worker 及 1/1 可用容量。此时尚未创建 Run、调用 AI 或完成闭环。

环境交付审查：DevOps 任务已确认 completed/idle。主控读取 `文档/06-验收记录/P5E运行环境验收记录.md`、最终清单与 PID ledger，并用独立 HTTP 请求复核 8000 Backend、8765 Demo、5173 Frontend 的项目指纹，三项通过。自建服务保留供后续验收；其余三个执行任务继续进行。

主控复验：Runner Locator 二次修复后，按 `文档/06-验收记录/P5F主控验收记录.md` 中包含 PYTHONPATH 的命令重新执行 `.codex-validation/test_p5f_contract_review.py`，**4 passed、2 warnings、19.19s**。真实 Chrome/spawn 与后端 ASGI 隔离场景中的 Evidence 故障、completion 响应丢失和服务端终态兜底仍通过；不替代正式消息队列/AI 闭环。

遗留记录：主控只读正式库确认 `RUN-D41EB71FFFFE` 已按既有 60000ms 预算收敛为 TIMEOUT，保留原截图且没有伪造 Web 结果。见 `文档/06-验收记录/P5F遗留Run核查记录.md`。

Runner 交付审查：任务已确认 completed/idle，最终 **真实矩阵 11 passed、全量 334 passed + 2 conditional skipped、85.38s，Ruff 通过**。主控核对最终 `web.py` 中共同节点/Run deadline、版本化 NOT_FOUND 分类、后端兼容的 Healing 上下文，以及 Console 已解析填写值脱敏。相关源码仅此执行器变更，测试和结果见 `文档/06-验收记录/P5FChrome异常矩阵记录.md`。本轮矩阵通过；Session 过期自动恢复仍为独立 V1 缺项。

正式 Worker 版本：DevOps 完成仅本轮自建 Worker 的串行重启，旧 PID 10392/43084 已终止，新 PID 58120/59460，启动时间约 `2026-09-09T14:06:05Z`。新 ready 于 `14:09:46Z` 原子更新，ACTIVE/ONLINE、WEB READY、容量 1/1、active_run_count=0；其余服务未重启。DevOps 追加任务也已 completed/idle。

正式首轮尝试 `V1P5E_20260909_39E35D46`：Case 5 的基准 Run `run_da4b8ee6615840c49e5bc732c9cad99b` 真实 SUCCESS，产生 5 个 Evidence。第二个 Run 创建前因心跳可用 Slot 尚未回补而校验失败；稍后同一 payload 已 valid，AI 调用 0 次。主控授权脚本对 ONLINE/WEB Slot 回补有限等待后，再进行一次明确的新合成尝试；保留两次独立 manifest 以便归档，不得无条件重试所有 validation 错误。

第二次尝试 `V1P5E_20260909_F294AC1E`：Case 6 的基准 Run 再次成功，但旧 Locator Run `run_15c59b0b27964dd8a6fec2fcca840f9c` 留在 QUEUED，Outbox=PUBLISHED、未 claim、无 Web Result/Evidence，AI 仍为 0。停止新尝试保留现场。主控两次直接读取 RabbitMQ 均确认专属队列存在且为空、死信队列有 2 条消息，不能根据 Runner basic_get 模式下的 consumers=0 推断无人消费。

主控发现发布确认竞态候选：dispatch 先提交 PENDING，再发布消息，最后提交 PUBLISHED；窗口内 claim 会收到永久冲突并可能被 Runner reject 到死信。已追加授权 Backend 在 runs/service、必要错误映射及测试中确定性复现与最小修复；仅合法 QUEUED/PENDING 的匹配消息可采用专用暂态，其他错误、RUNNING 重认领及权限规则不放宽。DevOps/Runner 同步只读核对 broker/协议；未确认根因前不修改正式状态或重复投递。

Runner 最终边界复验：候选列表未遍历完就耗尽预算时，禁止生成“全候选缺失”Healing；只有实际状态完整覆盖候选才允许该结论。新增真实回归通过。重复 Force Stop 又发现 Windows 偶发 Chrome 后代残留，`isolation.py` 已对仍存活的本次 spawn 根 PID 使用无 shell、隐藏窗口、5秒上限的进程树终止，失败才回退原终止路径。最终 **真实矩阵 12 passed、全量 335 passed + 2 skipped、86.37s，Ruff 通过**，Force Stop 连续 3 次验证无残留。主控审查该变更仅指向所属隔离 PID，不按浏览器名称批量结束进程。

Runner 协议诊断已 completed/idle：既有 claim 对503最多4次（0.5/1/2秒退避），耗尽后 TransportError 重新入队；409仍为 ProtocolError 永久拒绝。5项相关聚焦回归通过，无 Runner 代码变更。独立 broker 被动检查也确认专属队列存在且非自动删除；正在等待 Backend 的竞态复现与修复。

发布竞态修复审查通过：`RunDispatchPendingError` 使用固定安全代码 `RUN_DISPATCH_PENDING` 和 HTTP503，仅在鉴权、信封、消息匹配及 QUEUED 已验证，且 Outbox=PENDING/两项 claimed 字段为空时返回。未放宽其他409或PUBLISHED前的实际认领。Backend 确定性回归与主控实际 RunnerClient/Consumer 跨模块复现均证明旧代码会永久拒绝；修复后主控新增 `.codex-validation/test_p5e_dispatch_race_review.py` 验证503有限重试→NACK/requeue，再于真实发布状态提交后原消息认领→ACK。与原4项Evidence故障联合 **5 passed、2 warnings、18.98s**，新增文件Ruff通过。

恢复授权：仅重启本轮owned Backend加载修复，保留已知QUEUED且未认领的Run；不以手工数据库终态清除障碍。DevOps正在准备固定Run的DLQ精确恢复工具，须主控审查后才执行；保持原消息ID与正文，原路由发布确认成功后才ACK匹配死信，其他消息保留。Backend随后从同一第二attempt继续真实Healing/批准/再执行/失败分析，不创建第三套资产。

恢复执行放行：主控核对新 Backend PID `38960`，启动于 `2026-09-09T14:55:12Z`，HTTP 健康，Worker 未重启且在线；active_run_count=1 如实保留该未认领 QUEUED Run。`p5e_redrive_run_dead_letter.py` 已检查读取最多20条、连接超时、完整原信封与死信来源匹配、发布前再次核对数据库、publish confirm 后才 ACK，以及不确定状态 journal 禁止盲重试。已授权 inspect 全通过后只执行一次目标恢复。Backend 的 `--resume` 已审查：无 AI 历史调用、资产/模型/Prompt版本不漂移、不重置 demo，并只轮询原失败Run。真实恢复和AI结果仍待返回。

Backend 最终竞态修复回归：**361 passed、34 warnings、27.94s，Ruff通过**。

恢复实测通过：主控读取journal并独立查询正式库，原Run在 `14:59:08Z` 开始、`14:59:16Z` 结束，`FAILED / WEB_LOCATOR_NOT_FOUND`，WebResult=1，Evidence=4。DevOps确认原DLQ由2降1、其他消息保留，唯一Worker与容量正常。未创建替代Run或手工修改状态。

第一次真实自愈请求产生 AiCallLog `16`，模型调用成功、无Schema repair，但域校验409：模型选择 `css [data-testid='signin-confirm']`，实际候选存在相同data-testid，白名单却仅派生 test_id 写法。没有生成可审核Proposal，调用账本已计1。主控决定修复确定性表示不一致：仅对已有简单安全data-testid派生test_id及单/双引号CSS属性形式，并将同一实际允许候选列表加入安全来源快照；仍拒绝任意CSS、错索引和陌生值。不添加从AI日志直接物化资产的新能力。

主控明确授权诊断已知失败后的继续调用（预计本轮累计4次，仍最多6次），保留Call16、同一Case/Run和账本；未知AI结果不得自动重试。Backend正在增加仅针对此已知拒绝的显式恢复入口和相关回归，修复加载后继续真实Reject/Accept/批准/再执行/失败分析。

2026-09-10 恢复采用独立 H1 修复包，不再在同一执行轮次串接服务加载和真实验收。主控以数据库只读 Session 加 SQL SELECT/SHOW 限制器核对正式原 Run、CaseRun28、固定版本和 Call16：当前 source snapshot 移除本次新增的 candidate_locators 后，其 SHA256 与 Call16.entity_id **完全一致**；Call16 原实际拒绝的候选及索引在修复后的确定性列表中 **精确匹配**。未输出原始快照或调用内容、未修改数据库、未发起 AI。此证据证明旧调用与原来源的关联及最初拒绝原因已被修复，不替代 H1 全量回归或实际 Proposal 再生成。

H1/r1 已 completed/idle，Backend 383 passed，报告指纹与磁盘一致；主控未放行。审查发现新增两种派生后仍按280项提前截断，主控隔离复现40个满属性元素只剩32个被表示，索引39完全缺失。已于原轮次结束后独立发送 H1/r2，授权同步公开响应 Schema 到40×9的360项，保留全部有效候选及快照大小/来源限制，测试尾部元素实际Proposal路径。不能用丢弃合法输入来满足旧Schema测试。H2/H3尚未派发。

H1/r2 最终验收通过：任务 completed/idle，后端 **384 passed、0 skipped、34 warnings、37.92s**，聚焦34 passed，相关5文件Ruff通过。主控逐项核对5文件SHA256和新增尾部索引39的Proposal生成/source snapshot/读取/Accept测试；独立运行原40元素复现，结果变为360项、40个元素、每个9种均保留。未扩大DOM数量、放宽来源安全或修改正式AI账本。随后才向空闲DevOps派发H2服务恢复包；H3真实调用仍未派发，P5-E门禁不因此算通过。

H2/r1 最终验收通过：DevOps completed/idle，23项就绪检查全通过，主控核对5项工具/清单哈希与报告一致，并独立HTTP确认三服务200及运行Schema maxItems=360。新Backend监听50392，唯一Worker23364/57108，Web1/1、无排队/执行中Run与录制；旧ready/owned保留历史副本，中间件及MySQL未重启。Demo重新启动，changed_locator=false如实记录。

主控新增只读HTTP预检 `.codex-validation/p5e_h1_controller_readonly.py`，Ruff通过；H2交付后实际执行 **PASSED**，Call16的真实API来源/模型/Prompt/Schema和固定Run/资产全部匹配，无Proposal，原manifest/ledger字节不变，AI保持1次。随后才派H3真实闭环包，授权仅重建固定Demo changed=true及恢复原attempt，不重投旧消息、不重跑baseline。

H3已交付部分成功检查点：Proposal2拒绝、Proposal3接受，新ElementVersion3/WebCaseVersion7先DRAFT再人工批准，新Run真实SUCCESS并有5项Evidence，旧版本6未改写。最终FailureAnalysis的Call19模型调用成功，但两个重复非法节点引用被服务409拒绝；无分析业务记录，脚本停止、账本累计4次，不算完整门禁通过。

主控针对Call19独立读取正式数据库，使用SQL SELECT/SHOW限制器、无commit/外部调用，重建实际失败来源与Prompt16渲染：来源摘要与Call19.entity_id及固定摘要 `a7a7815b0548190b4768febb53c5e2139c35e158cb83e465037b5930f177f486` 均完全一致；失败节点集合为action_1，完整序列化来源在实际user_prompt中原样保留，非法字面引用 `://` 不在来源中，Prompt没有缺失变量。仅输出布尔摘要，不输出原始Prompt、response或凭据。该证据支持模型输出违反领域约束的诊断，不是来源节点被渲染器改坏。

依总规格第52节，派发H5隔离修复：把已有领域约束接入Gateway既有单次Repair，保持服务最终校验和失败审计，不修改历史Prompt/Schema。准备严格限定Call19后、只补失败分析的新恢复入口；本包禁止实际AI/资产写入或重启服务。完整安全与前端验收仍待后续。

H5/r1已completed/idle并经主控验收：Backend全量397项通过，主控独立复跑4项实际service→Gateway→受控provider测试和28项Gateway/恢复测试，全部通过；交付7项SHA256逐项匹配。来源约束、敏感错误固定摘要、原单次Repair预算及服务保存前复核均保留，没有通过替换非法结果通过验收。

主控另以正常登录后GET-only客户端执行新FailureAnalysisRecovery.preflight，正式接口预检PASSED，manifest/attempt/ledger字节不变，recovery receipt不存在，业务POST为0。结合正式库来源hash复核，当前输入符合恢复前提。随后仅派DevOps H6重载本轮Backend；未经加载复核不得执行H7真实分析。

H6已completed/idle并验收，21项就绪检查通过，主控核对新监听PID20860/启动时间/健康与源码和7项交付哈希。四个执行任务均空闲后，主控H7仅执行一次Call19后的失败分析恢复命令，退出0：Analysis2 COMPLETED/AiCall20首轮成功，无repair/fallback/retry，节点action_1。独立正式GET复验通过，旧Call19和前4条账本保留，累计5次，manifest与receipt均PASSED，详见 `文档/06-验收记录/P5E-H7失败分析恢复验收记录.md`。随后派Frontend H4完整页面只读验收，同时Backend L1仅改独立日志展示/脱敏文件并跑隔离测试，不重启正式服务。

H4已完成并经主控验收：真实Chrome逐项核对Proposal2/3、AiCall17/18、新旧版本6/7、失败与成功Run及Evidence、Analysis2/AiCall20。主控独立2项manifest测试通过，6项指纹一致，审计截图可读；未改前端产品源码，业务写尝试0，manifest及ledger不变。HTTP错误检查排除了events/stream，不作为SSE专项证据。当前限定P5-E场景通过，P5-F隐私修复与精确资产归档未完成，完整登录/Session/CRUD及最终V1仍未验收。
