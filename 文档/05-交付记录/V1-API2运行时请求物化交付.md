# V1-API2 运行时请求物化交付

状态：已实现，基本检查通过，待集中验收；完整 API 流水线仍未完成。

- 正式 `API_CASE` 可使用当前 Environment 的 `{{base_url}}` 和非敏感变量构造 URL、Header、Query 与请求 Body。
- Secret 引用在普通 Runtime 替换阶段保持引用形态，最后按项目/环境校验并解密，避免把密钥混入通用 Context。
- 敏感变量名、未定义变量、畸形花括号和 `{{$...}}` 动态值继续拒绝。动态值必须等后续 Runner 现场动作接入，不能在 Backend 计划生成时伪装为现场结果。
- 无 Pre Action 时 Context 仍可在生成已认证 Runner 执行计划时固定；API3 已在存在受支持 Pre Action 时将模板最终渲染下沉至 Runner 隔离进程。

基本检查：Runtime 正向计划、三种认证 Secret 计划及既有非法能力门禁共 5 项通过；相关 Ruff 通过。未运行全量、真实目标服务、正式 Environment 或报告验收。Pre Action 后渲染的新增结果见《V1-API3前置动作Runner流水线交付》；本记录保留 API2 边界历史。
