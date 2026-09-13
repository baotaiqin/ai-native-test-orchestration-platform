# Phase 5 原工作包最终验收

2026-09-10 主控验收。P5-A～P5-F原工作包通过，允许进入Phase 6；这不等于完整V1产品验收通过。原Phase 3/5分包未覆盖的完整正式API执行、登录态过期恢复、扩展Web操作与高级自愈等缺口继续按《V1需求验收矩阵》实施，不能删减需求或计为已支持。

## 验收依据

| 范围 | 已核对证据 |
|---|---|
| 基础Web与录制 | P5-A～P5-D历史门禁；实际headed录制、真实模型整理、人工接受、新版本批准及Runner执行成功，见开发计划及P5-D记录 |
| 自愈与失败分析 | H1/r2修复候选完整性，H5领域校验进入原单次Repair；H7真实Analysis2/AiCall20成功；H4真实Chrome逐ID验证Proposal2/3、旧6/新7版本、成功Run、Evidence及分析审计，主控独立测试/截图/指纹确认 |
| 基础异常收敛 | Evidence平台失败保留真实成功步骤；不可变执行预算、超期Run收敛；主控真实Chrome/RunnerClient与隔离Backend组合验证5项通过 |
| 迁移 | 独立MySQL8.4的0031～0038空/非空数据往返，26命令及26结构断言；临时容器/卷已清理 |
| Chrome异常矩阵 | headed/headless、有效Session复用、Locator fallback、Cancel/ForceStop/Timeout、Slot/进程清理；12项真实Chrome通过，ForceStop连续3次无所属进程残留，PNG真实遮罩检查通过 |
| RabbitMQ重连 | Q1真实broker/Chrome两场景，重连与ACK不确定重投不重复执行动作；结合已有真实Backend claim/completion隔离验证，临时资源清理经主控复查 |
| 隐私与读取授权 | L1/r2通用日志/AI历史脱敏，Backend412全量及主控34项组合通过；项目外Evidence下载在对象存储读取前拒绝、SSE入口404、序列化事件丢弃未知敏感字段；正式Redis/SSE只读首帧身份匹配 |
| 精确归档 | C1仅六项合成资产ARCHIVED，所有版本、4组执行、19个Evidence、AI/Proposal/Analysis历史保留；主控正式只读8项检查通过，无外部引用、无删除或重复投递 |
| 正式加载 | L2只重载拥有的Backend，17项就绪通过；主控独立核对PID52512、启动时间、8000监听、3源码和5交付指纹，再逐条比对9项AI历史展示及审计数据，全部通过 |

相关原始报告：P5F主控验收记录、P5F迁移验收记录、P5FChrome异常矩阵记录、P5F-Web队列重连验收记录、P5E主控审查记录、P5E-H7失败分析恢复验收记录、P5E-H4前端完整验收记录、P5F-L1日志隐私修复交付、P5F-C1合成资产归档交付、P5F-L2隐私修复加载交付。

## 最终加载与主控独立结果

DevOps L2任务 `01a0875c-b43b-78f2-afc5-ed5f7b9ecc7c` completed/idle，结束游标 `91fe84fb-8014-4756-aed5-a02ef0a29008:9`。仅Backend旧树22188/10400/20860结束，新树29460/22252/52512；监听进程启动时间2026-09-09T18:19:56.1653374Z，晚于源码确认18:19:52.0753714Z。其他应用/Worker/中间件保持。

主控运行 `.codex-validation/p5f_l2_controller_readonly.py`：正常登录1次，其余仅GET和SELECT/SHOW；9条AI历史逐字段匹配脱敏副本，7个内容字段的展示形式变化，模型/版本/Token/成本均保留。原库与项目、六资产归档状态、manifest/ledger均不变，8项检查通过，结果 `.codex-validation/p5f-l2-controller-review.json`。所有历史总摘要仍为 `1d4b2a85d48bc286db7475fb3ebc42eceb689d9a56095e66c3f1270a036dfc78`。

## 范围与后续

P5-E的Case6是一条空登录表单CLICK，证明当前自愈和审计链，不是完整登录/CRUD/Session恢复或最终场景D。隐私复验采用合成数据、已知敏感形态和既有授权接口边界，不声称任意自然语言秘密识别或尚未实现的正式用户Session撤销已通过。

所有验收历史保留；共享服务、原有死信及用户资产不清空。原P5-E恢复入口不得重复运行；归档资产如需恢复必须另行明确任务范围，不能重跑旧验收脚本。

按历史工作包权重，Phase5为100%，V1旧工作包总计90%；Phase6从0开始。该数值不是完整需求覆盖率。下一包执行《Phase6报告与Dashboard实施契约》，完整V1仍以逐项矩阵与最终A～E场景验收为准。
