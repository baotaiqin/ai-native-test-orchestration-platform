# V1-W4/r1 基础Web规则断言Runner扩展

主控已确认Runner W1 completed且已审查，现派发给同一AI-Test-Runner_sol，gpt-5.6-sol / xhigh。本包是总规格第39节的独立Runner实现，不依赖Backend正在挂起的I1事务修复，不等待或联络其他任务。

## 冻结输入与产品目标

W1最终protocol.py SHA256 1941af58b67b8ec2483e16274b24b04c73908dc7ed83761d1f5c11aa938032f4，executors/web.py为9cf7c6c2e32a923f34711700eaee12cc747855cff8172c9b1ca3c2a807a67ddc，models.py为f33405453276d19bac96748bbf69aa93df55bfcc48b75e40a2dbd67c0880a16a，主控派发前已核对一致。保留W1证据，不覆盖。

仅新增5种固定断言，继续schema_version=1及既有type/timeout_ms/locator/expected四字段，models结构无需改动：

| type | locator | expected | 行为 |
|---|---|---|---|
| ASSERT_EXISTS | 必须 | 必须null | 元素已附着于DOM即可，隐藏元素也可以存在；没有元素不能通过 |
| ASSERT_ENABLED | 必须 | 必须null | 真实元素处于启用状态，disabled/aria-disabled等按浏览器原生状态判定 |
| ASSERT_TEXT_EQUAL | 必须 | 必须字符串，可空 | 与实际inner_text完整、区分大小写相等；不自行trim或改为包含 |
| ASSERT_INPUT_VALUE | 必须 | 必须字符串，可空 | 使用原生input_value读取输入控件值做完整相等比较；非输入目标受控失败 |
| ASSERT_TITLE | 必须null | 必须字符串，可空 | 当前页面title完整相等，不能用URL或页面正文代替 |

expected沿现有模板解析和10000字符上限；timeout_ms保持100..600000整数，拒绝bool、未知type、额外字段和新类型无关参数。旧三种断言兼容：尤其现有ASSERT_TEXT实际为包含比较，不能改成相等；旧ASSERT_VISIBLE/URL原有载荷不因本包被收紧或重新解释。

沿用现有断言执行/Locator候选/预算/trace体系，输入值、标题、实际/期望文本、模板结果和原始浏览器异常不得进入可见错误、日志、截图或Trace中的未脱敏区域。不得调用真实click/fill来探测存在或启用；断言不变更表单、导航、Session或资产。不新增失败重试或重放动作，不重构旧断言等待策略。新的page级断言失败必须保留正确节点归属；节点/总预算与外部取消仍可收敛。

Hidden/Clickable/Attribute/Element Count/Download/Network/图像/AI断言及其他动作不在本包；不能为方便塞到expected字符串JSON中偷渡参数。Backend DSL、Frontend和正式加载由后续专包完成，本包成功仅表示Runner能力通过。

## 文件、验证与交付

允许runner/runner/protocol.py、runner/runner/executors/web.py局部修改及Runner专项测试；优先新增runner/tests/test_v1_web_assertions.py。不得改Backend、Frontend、迁移、models计划结构、消费者/队列/部署、正式Worker/共享Demo或其他证据。只用独立随机回环服务、所属Chrome进程/profile；退出逐项清理，不关闭用户浏览器。

真实Chrome必须覆盖5种断言成功/失败：隐藏但存在与完全缺失、enabled/disabled、相等与仅包含、空输入/非空输入/错误目标、正确标题与仅子串标题；验证没有click/input事件副作用。覆盖候选回退、模板解析、节点/总超时、取消和敏感随机字符串不进入结果/证据。真实执行器和最终协议解析器都要用到，不只mock方法或检查PASS。

Runner全量pytest/Ruff通过，回归W1的13动作与原7动作/3断言，重跑主控 `.codex-validation/test_v1_w1_controller.py` 五项真实Chrome以确保预算和动作行为未退化。新增范围的小型确定性测试即可，不复制实现写大量结构断言。

独占 `.codex-validation/v1-w4/` 与 `文档/05-交付记录/V1-W4基础Web断言Runner交付.md`。记录准确命令、Chrome版本、逐场景事实、文件指纹及资源清理，不调用真实AI、正式网络服务、注册Runner或创建正式Run。先读根AGENTS；禁止跨任务消息/等待、Git、Claude、内部Agent；超界保存阻塞后结束，完成final后结束，由主控读取。
