# V1-W2/r1 基础 Web 动作后端接入

主控现派发。I1M/r2 已 completed/idle 且通过主控真实 MySQL 门禁审查（`.codex-validation/p6-i1m-controller-r2-review.json`），Runner W1 动作已验收。执行者 AI-Test-Backend_sol，gpt-5.6-sol / xhigh。

主控重新核定依赖：W4/r1 已完成并冻结 protocol.py 为 dd9f92c0aa7bb63c3682a9eb5d39a25f574128522b4843186fbba464d7ebad83、models.py 为 f33405453276d19bac96748bbf69aa93df55bfcc48b75e40a2dbd67c0880a16a。正在执行的 W4/r2 只修执行器证据脱敏，明确不得改这两个文件。因此本包可只读导入上述冻结解析器/models，不导入或验证仍在修改的 executors/web.py；执行前后核对这两个指纹，漂移则保存阻塞并结束。W4 整体隐私验收仍未通过，不据此宣称已验收。W2范围仍仅W1的13种动作，不顺带启用W4的新断言；新断言的Backend/Frontend另包处理。

## 目标和契约

把《V1-W1基础Web动作Runner任务包》冻结的13种动作接入 WebCase 正式版本校验与执行计划，使其能按现有人工保存DRAFT→批准→新Run路径使用。动作包括 RELOAD、BACK、FORWARD、DOUBLE_CLICK、RIGHT_CLICK、CLEAR、HOVER、CHECK、UNCHECK、RADIO、ENTER、TAB、WAIT_NETWORK_IDLE。

页面操作和WAIT_NETWORK_IDLE无需业务参数，其余动作要求原WebLocator；统一沿用timeout_ms/failure_policy，ENTER/TAB固定键名，不接收用于覆写动作的key/value/url。后端资产DSL保持extra=forbid；执行计划保留schema_version=1与既有七字段，未用字段显式null，严格通过Runner最终协议解析。未知type、非法locator、越界预算和额外字段应拒绝。

保留原7类动作、3类断言、不可变版本、人工批准、项目/资产归档、角色权限与锁定ElementVersion引用。不能因为扩展type放宽Secret、URL、参数、Locator或版本归属检查。现有录制输入类型仍按真实录制能力处理，不以新增手工动作宣称录制器已经支持这些事件。

沿现有通用计划生成逻辑做最小接入，不重构runs/service.py、不改变队列/预算/取消/重试/历史完成兼容。新增动作不创建任何隐含Run，不重新解释历史WebCaseVersion或旧Run。

## 范围与验收

允许 `backend/app/modules/web_cases/schemas.py` 及必要的同模块局部校验，`backend/app/modules/runs/` 仅确有必要的新动作计划校验/序列化接入；新增Web资产/执行计划相关测试。不得修改 I1 关联模型/迁移/服务、Runner或Frontend，不做新迁移或正式服务加载，不触碰P5-E历史。

隔离合成测试必须覆盖13动作合法/非法DSL、保存/读取/新版本批准、精确计划字段、Runner最终真实解析器兼容、拒绝未批准/错项目/归档/错ElementVersion、旧动作版本和旧Run兼容。验证实际锁定版本，不只构造Pydantic对象即计为完整接入。Backend全量pytest及Ruff通过；必要跨模块验证可只读导入Runner代码，不修改它或联系其任务。

本包只在隔离SQLite/受控网络替身运行，不读取正式凭据，不连正式MySQL/Redis/RabbitMQ/MinIO，不调用真实AI、不创建正式资产或Run、不重启服务。真实加载与完整运行由主控后续另行安排；交付不得把本包测试写成正式端到端成功。

独占 `.codex-validation/v1-w2/` 与《文档/05-交付记录/V1-W2基础Web动作后端交付.md》，记录命令、结果、源码指纹和协议示例。遵守根AGENTS主控独占调度，禁止Git/Claude/内部Agent及任何跨任务工具；超出范围保存阻塞后结束，完成后final并结束，由主控读取。
