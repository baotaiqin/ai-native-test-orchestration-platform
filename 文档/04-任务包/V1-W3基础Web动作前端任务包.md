# V1-W3/r1 基础Web动作前端接入

主控已派发给空闲AI-Test-Frontend_sol（gpt-5.6-sol / xhigh）。W2后端已验，I1F/r2已completed/idle且主控验收通过：最终9源码/3构建/2接口指纹匹配，主控Chrome2项、T3 18组及X2 24组通过。证据见 `.codex-validation/p6-i1f-controller-r2-review.json`。W5另有五种断言后端已验，但本包只做13动作，五断言由W6串行接入。Backend I2只修改需求快照和报告链，不改W2/W5的WebCase DSL；读取固定schema而不依赖其运行中的新实现。

## 目标

把Runner W1、Backend W2的13种新增固定动作接入WebCase手工编辑和录制AI建议的人工内容编辑：RELOAD、BACK、FORWARD、DOUBLE_CLICK、RIGHT_CLICK、CLEAR、HOVER、CHECK、UNCHECK、RADIO、ENTER、TAB、WAIT_NETWORK_IDLE。原7动作与3断言保留；本包不扩充录制器实际捕获事件、不启用其他动作或断言。

依据W2最终WebCase资产DSL定义更新类型、选项、工厂、摘要与校验；不得把Runner七字段计划格式当成编辑器资产格式发送。页面动作和WAIT_NETWORK_IDLE无需locator/url/value/key；其余9种新动作需要既有WebLocator，ENTER/TAB不能由自由key字段改义。所有新动作保留timeout_ms/failure_policy配置。

从带URL/文本/key的旧动作切换类型后，提交对象不残留无关字段；从页面动作切换元素动作必须要求有效定位信息。类型选择不能落到旧工厂默认WAIT_URL分支。旧动作不因扩展类型丢失原有配置。已有ElementVersion精确引用、会话配置、自然语言步骤、只读历史和权限门禁保持。

手工保存/版本批准与AI Edit/Accept/Reject继续各自明确操作；未编辑的AI接受省略content，已编辑时提交当前完整有效内容并保持录制Session引用。非法编辑不能阻止拒绝建议。只读/归档不得写，写入失败保留编辑且不自动重复请求；切项目/版本/身份后迟到响应不得改新页面或触发后续写入。

## 文件与验证

允许 `frontend/src/types/web.ts`、`frontend/src/views/web/WebAssetView.vue`、`frontend/src/views/web/WebRecordingWorkbench.vue` 及确有必要的共用动作构造/显示辅助与专项验证。不得改Backend/Runner/迁移、录制事件协议、正式服务、主控文档或其他任务证据。I1F新增需求关联入口及上下文保护须保留。

真实Chrome使用独立随机回环服务与合成API：13种动作分别构造、保存并重载保持准确载荷；覆盖页面/元素互切、已有参数清除、定位缺失、边界预算、STOP/CONTINUE、版本只读与权限；AI已编辑接受/未编辑接受/拒绝完整载荷与调用数；旧7动作和3断言回归。检查实际请求体和次数，不以截图替代。中文页面布局检查、类型检查与独立生产构建通过。

独占 `.codex-validation/v1-w3/` 与 `文档/05-交付记录/V1-W3基础Web动作前端交付.md`，记录源码指纹、命令、场景、截图与未验项。不得访问正式服务/共享Demo、调用真实AI、创建正式资产或Run。此包前端验证不宣称正式完整执行通过；后续加载与完整动作执行另派。

先读根AGENTS；禁止跨任务消息、等待、Git、Claude、内部Agent。超界或接口缺项保存阻塞后结束；完成后final并结束，由主控读取。
