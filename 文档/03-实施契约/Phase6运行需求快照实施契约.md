# Phase6 运行需求快照实施契约

主控实施契约；I1/r4与I1M/r2已验收，I2/r2已通过后端代码与主控6项HTTP/缓存审查（624全量、16指纹），I2M独立真实MySQL门禁进行中，I2F前端包已准备未派发。I1提供关联与影响查询，本契约补齐总规格15节“历史Run可追溯”。执行权限、具体文件与迁移边界以该编号任务包为准，不据此扩大其他执行任务范围。

## 实现前已核对的入口基线

`runs/service.py:create_run`在一次数据库事务中创建TestRun、CaseRun、StepRun，并把API/WEB/SCENARIO目标版本ID持久锁定；提交后才发布状态事件。现有TestRun/CaseRun没有Requirement来源快照。reports仅将已锁定CaseVersion与当前资产信息分开，不存在可用的历史需求绑定。

当前Scenario是独立DSL资产，其HTTP节点不等于已关联TestCase。不得依据节点URL、名称或相同数字ID猜测需求来源；只对真实支持的关联目标捕获来源，未支持的类型如实显示，不伪造链路。

## 新Run必须保存的事实

在创建Run及CaseRun的同一事务保存需求来源捕获标记与有界关联条目。捕获失败则整个Run创建失败，不先发布消息/状态事件，不留下部分Run或部分来源；以后Dispatch/Claim不得重算或覆盖创建时的来源。

区分三种情况：旧Run未捕获NOT_RECORDED；新Run已捕获但没有关联CAPTURED_EMPTY；新Run有来源CAPTURED。不能仅以关联条目数为0判断旧Run创建时“没有需求”。必要时使用独立捕获记录/版本字段，不回写受保护旧Run。

条目至少能确定：来源关联/修订身份、明确资产类型、锁定用例版本、需求ID、记录的RequirementVersion（可能未知）、来源/source/relation_type/confidence、捕获方式及时间。引用强外键或可靠不可变记录，后续移除覆盖关系、重新关联或切换current不能改写已捕获来源。

只捕获与该Run实际锁定用例版本匹配的精确关联。旧资产级关联可单独记录为“创建Run时观察到的资产级关系，未记录用例版本”，不能声称覆盖当前执行版本；其他CaseVersion的精确关联不能塞给本Run。没有可靠RequirementVersion的来源继续明确未知，不推定为创建时current。

如果保存名称、版本号或内容hash作为展示快照，明确它们的捕获时间及来源；不保存原始Markdown、Secret、请求body或AI正文。版本内容hash仅用于来源核验，不代替RequirementVersion归属校验。

## 并发与历史

关联新增/移除/重新关联与Run创建并发时，捕获必须对应一个明确事务边界；不能拼出同一链接的旧版本ID和新状态。用实际SQLAlchemy/MySQL事务验证，不只用同一个Session或假Connection声称一致性。

未来数据驱动新增CaseRun时，来源与实际锁定版本/迭代一致；本包不顺便实现数据驱动。若采用Run级共享捕获，CaseRun查询必须证明它确实属于相同锁定目标，不能让其他目标错误继承。

旧Run没有该标记就保持NOT_RECORDED，不根据关联created_at、资产current、版本时间或名称推断历史。可另提供清楚标注的当前关联查询，但不能放在“执行时快照”中。P5-E四Run/版本/19Evidence/AI历史不回填、不重放。

## 报告与接口

报告详情、逐CaseRun分页与Markdown/HTML导出返回同一持久来源。大量关联分页且完整导出有现有大小上限保护，不偷偷截断；权限沿用报告项目访问，导出仍只读和一致快照。

页面显示可靠链路 Requirement/Version → Case/Version → Run/CaseRun → Step/Evidence；关联级来源、未知版本与旧Run未捕获分别说明。变更当前需求/用例后展示保持锁定引用，归档来源可授权查看，不启用历史版本编辑。

## 后续验收要求

隔离测试覆盖：新Run有/无关联、旧Run不回填、旧资产级/未知版本、其他CaseVersion排除、同ID不同资产、跨项目拒绝、归档历史、移除和重新关联不漂移、创建失败原子回滚、Dispatch/Claim不重算、分页和导出一致。真实MySQL并发及空/非空迁移验收通过后再独立加载。

最后使用独立合成需求和用例版本进行明确创建Run的真实验收，前后改current/关联证明历史不变；不复用旧P5-E恢复/AI账本。本契约没有授予新正式业务写入，本轮专用任务包将明确资产、运行预算、清理及保护边界。
