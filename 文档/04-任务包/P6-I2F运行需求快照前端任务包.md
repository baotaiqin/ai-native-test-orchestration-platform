# P6-I2F/r1 运行需求来源快照前端

## 本次派发覆盖：开发优先

用户已明确要求先推进、验收后置。本包现在派发，保留功能目标和独占修改范围；下文完整验收清单作为后续集中验收标准，本轮不执行全量测试、完整Chrome矩阵、截图审计或重复主控探针。只做编译/类型检查、相关lint及关键契约针对性检查，完成后交付简短报告：修改文件、基本检查、未验事项、阻塞与接口变更。不要为收集证据反复构建/测试。无编译/契约阻塞即结束，交由主控继续下一开发包。不得将本次开发交付称为完整验收通过。

Backend I2/r2、Runner W7/r2、Frontend W6/r1均已交付，I2M已结束；Backend冻结即刻解除。I2M独立清理复核留待集中验收，不阻塞纯代码。禁止修改报告快照API接口和Runner协议，以便前后端并行。

主控已派发，按本次开发优先覆盖执行。Backend I2/r2已通过代码与HTTP审查，I2M真实MySQL门禁另行进行；Frontend须先完成W6并经主控审查，再由主控发送本包，不能自行开始或等待他人。执行者为既有AI-Test-Frontend_sol，gpt-5.6-sol / xhigh。

## 目标与精确接口

报告展示创建Run时持久保存的需求来源。依据 backend/app/modules/reports/schemas.py（SHA256 a5e0bb00327cda407f275da0c3ca410e14b2bbea324e185c64fc63daf953fe4a）及I2交付：ReportDetail.requirement_sources、ReportCase.requirement_capture、GET /reports/{run_id}/requirement-sources。分页参数page/page_size/可选case_run_id，响应captures/items/total/has_more/next_page。使用既有http客户端与固定API路径构造请求，不把响应continuation_path当任意URL直接访问。

清晰区分四种状态：CAPTURED“已记录来源”；CAPTURED_EMPTY“创建时未关联需求”；NOT_RECORDED“历史运行未记录，无法判断当时关联”；UNSUPPORTED_TARGET“此目标类型尚不支持来源捕获”。后两者不能显示为“无需求”。缺失旧字段也只表示未记录，不能补造当前快照。

展示每项捕获的需求code/title/type/status、需求版本或明确未知、实际执行用例类型/ID/版本、关联资产版本或未知、来源/关系/置信度、原关联ID与前驱、捕获时间及原关联时间。首次默认简洁表格，可展开详情查看hash与完整元数据。标题和状态属于捕获事实，不读取当前对象覆盖。未知版本不能用current补齐；旧资产级关系明确不证明精确执行版本覆盖。

捕获UTC按既有明确时区格式显示。原关联LEGACY_UNKNOWN时间原样显示并标“历史时间，时区未记录”，不使用Date解析给其补当地时区或Z。数字ID相同的TEST_CASE/WEB_CASE仍保持不同目标。

需求/固定版本、用例/固定执行版本深链沿现有路由和I1F入口。没有版本或目标已不可访问时保持快照可读并明确限制，不暗中转到current。保持报告→CaseRun→Step/Evidence现有筛选；选择CaseRun查来源有独立分页，切换Run/CaseRun/身份、A-B-A和组件卸载后迟到结果不可覆盖新上下文。

来源查询失败显示错误与显式重试，不显示为CAPTURED_EMPTY；只有total/has_more支持时才显示完整提示。Markdown/HTML仍通过既有只读服务端导出，不能从当前可见首屏拼接不完整文件，不引入任何来源写入/批准/执行。

## 独占范围

仅 frontend/src/types/reports.ts、frontend/src/api/reports.ts、frontend/src/views/reports/ReportDetailView.vue、必要局部展示组件/工具、frontend/tests下专项。尽量沿用既有报告上下文/导出保护，不重构认证和全站页面。不改Backend/Runner、资产编辑器W3/W6、I1F关联组件、共享配置或主控全局文档。

独占 .codex-validation/p6-i2f/ 与 文档/05-交付记录/P6-I2F运行需求快照前端交付.md。独立构建、随机回环服务、受控合成API与所属真实Chrome/profile；禁用正式5173/8000、AI、数据库、Run、Git、Claude、内部Agent和跨任务工具。完成后清理所属资源并结束，不等待主控。

## 验收

真实Chrome核对四种状态、精确/未知两类绑定、捕获标题与当前标题差异、同ID异类、归档来源、深链版本、分页与CaseRun筛选、UTC和未知旧时间。合成恶意标题作为普通文本显示，无脚本执行。冻结API响应形状应由实际Backend Schema校验，不能使用根本不存在的字段让UI测试假通过。

覆盖Run和身份切换迟到响应、分页失败/重试、无权限与空来源区别；实际触发两种下载确认走服务端接口，旧报告/证据/X2相关回归通过。类型检查、构建、真实浏览器断言和稳定截图均记录；交付最终源码/构建指纹、请求统计与资源清理。I2M和正式新Run验收另有主控门禁，本包不声称已完成正式环境链路。
