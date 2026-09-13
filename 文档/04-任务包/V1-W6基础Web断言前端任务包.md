# V1-W6/r1 基础 Web 断言前端接入

主控已审查通过Runner W4/r4、Backend W5/r1、Frontend I1F/r2及W3/r1；现派发至既有AI-Test-Frontend_sol，gpt-5.6-sol / xhigh。W3主控3项10.72秒、9最终源码/构建/契约指纹匹配。以本次主控消息启动。

## 目标

接入 Backend 已冻结的五种断言：ASSERT_EXISTS、ASSERT_ENABLED、ASSERT_TEXT_EQUAL、ASSERT_INPUT_VALUE、ASSERT_TITLE。页面用清楚的中文区分“存在（含隐藏）”“启用”“文本完整相等”“输入值完整相等”“页面标题完整相等”；原 ASSERT_TEXT 仍表示包含匹配，不能通过换标签误称完全相等。

EXISTS/ENABLED 只需 Locator；TEXT_EQUAL/INPUT_VALUE 需 Locator 与 expected 字符串；TITLE 只有 expected，不提交 Locator。输入值及标题的空字符串是明确合法值，与未填写/缺失字段区分；不 trim、改变大小写或将空值替换为 null。timeout 沿已冻结的整数边界。切换断言类型后不得残留禁止字段，页面级 TITLE 不要求元素。保持顺序、预算、精确 ElementVersion、模板引用、原动作及断言兼容。

人工创建/新版本、录制整理结果的人工编辑与保存路径使用同一已批准 DSL 形状；不声称录制器能自动捕捉新断言。AI 未编辑直接接受仍遵守原契约（省略 human content），已编辑接受提交完整内容及原 Session 等已有字段；非法草稿不能让用户无法拒绝 AI 建议。前端不能自动批准/执行或修改旧版本。

## 范围

限 `frontend/src/types/web.ts`、实际 Web 资产编辑器和录制整理工作台中相应断言选项/工厂/摘要/表单校验，以及必要的局部工具与专项验证。以仓库实际路径为准，不为增加五个选项做全局重构。保留 I1F 需求关联/反向深链与身份上下文修复、W3 的 13 动作，不改 Backend/Runner、认证、共享环境或全局状态文档。

独占 `.codex-validation/v1-w6/` 和 `文档/05-交付记录/V1-W6基础Web断言前端交付.md`；独立构建、随机回环服务、合成 API 和自有 Chrome/profile。禁止使用正式 5173/8000、正式身份/数据、AI、Run、Git、Claude、内部 Agent 或跨任务工具。其他任务不可主动联系。

## 验收

真实 Chrome 从页面操作并检查实际提交载荷：五种新增断言、旧包含断言、空 expected、空白/大小写保持、元素/页面类型切换不残留字段、预算越界拒绝、旧版本查看、新版本保存、Viewer/归档只读。录制整理编辑接受与未编辑接受、拒绝非法草稿均做必要回归。验证网络请求及 UI 的实际行为，不只检查 TypeScript 联合类型。

TypeScript 检查、独立构建和相应专项通过，记录逐场景事实、最终源码/构建指纹及所属资源清理。写报告与机器证据后 final 并结束，由主控审查；依赖或范围阻塞则记录检查点后结束，不等待其他任务。

## 本轮冻结输入

Backend Web DSL schemas.py SHA256：47106d6d748f653cef98ef5bb38497e3a25d93d6b7b51efc1d70b442982d2215。W3最终源码和构建参见 .codex-validation/v1-w3-controller-review.json；保留全部W3证据。W3第二张截图属于接受后的过渡状态，不能证明AI表单布局；本包交付实际编辑中且已稳定的断言表单截图即可，不为此回头改W3证据。补对应的W3动作与I1F回归，在本包独立目录记录。
