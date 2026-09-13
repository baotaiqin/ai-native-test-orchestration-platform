# P6-I1F/r2 确认上下文与并发写入修订

状态：主控现派发。I1F/r1 已 completed/idle，游标 :24；主控核对最终源码9指纹及构建与2项失败探针一致。执行者为既有 AI-Test-Frontend_sol，gpt-5.6-sol / xhigh。

## 已确认的工作中构建缺陷

主控 `.codex-validation/test_p6_i1f_controller.py` 使用隔离生产构建、自有随机回环服务器、全拦截合成 API 及真实 Chrome，工作中预审两项均失败12.91s。交付报告所指最终构建再次2 failed、12.49s，9个源码指纹全部匹配；主控证据 `p6-i1f-controller-r1-review.json`。正式 API/身份/业务资产均未访问。

1. 打开移除确认，在另一标签把身份 A 换为 B，原确认仍显示；点击旧确认，浏览器实际以 Bearer synthetic-b 发出 DELETE。removeLink 在 await 确认后才读取身份，且没有在请求前检查组件存活/原上下文/权限。请求返回后的失效判断不能撤销已发出的写入。
2. 挂起创建 POST，完成另一现有关联的移除 DELETE，再释放 POST，创建按钮持续 is-loading。create/remove 共享 mutationSequence，先发创建的 finally 因序号变化跳过 saving=false，列表也可能不包含已经创建的关联。

## 修订目标和范围

仅修 `frontend/src/views/requirements/components/RequirementLinksImpactPanel.vue` 及必要的 I1F 局部上下文代码/测试，保留全部原 I1F 功能、精确绑定版本、只读门禁、反向深链和旧历史展示。不得改 Backend API、Runner、迁移、公共认证契约或其他全局模块来规避问题。

确认之前捕获身份与目标上下文；确认等待结束后、发送写入之前，必须判断原身份/目标/组件及当前写权限仍有效。组件卸载、项目/需求切换、身份切换不能让旧弹窗启动新写入。若主动关闭失效弹窗，限制为本组件拥有的弹窗，不影响无关操作；无须新增全局模态框框架。

创建和移除采用清楚的互斥策略或互不污染的操作状态；任一已启动请求结束后须正确释放其 loading，列表最终反映实际服务器返回的创建与移除结果。重复点击、失败或上下文变化不应触发自动重试，不通过永远禁用写入来规避验证。确认期间与请求期间的状态分别处理，避免确认后重复派发。

独占 `.codex-validation/p6-i1f/` 与 `文档/05-交付记录/P6-I1F需求关联与影响前端交付.md`；将现有 validation-summary.json / playwright-result.json 先保存为 validation-summary-r1.json / playwright-result-r1.json，再更新 r2 结果。保留原验收事实，不修改主控测试/证据。使用新的本包独立构建与随机端口；不访问正式 5173/8000，不重启共享服务，不创建正式资产/Run 或调用 AI。

## 验收与结束

主控两项真实 Chrome 必须在最终隔离构建上通过（可用 I1F_CONTROLLER_DIST 环境变量指定同包构建）；另补同身份卸载/切换目标后旧确认不发送写入、正常创建/移除仍成功的少量真实行为验证。执行 I1F 原专项和必要的关联页面回归、TypeScript 检查与构建，记录确切结果和最终源码/构建指纹。

先读根 AGENTS.md；禁止跨任务消息、反向等待、内部 Agent、Git、Claude。超出范围保存检查点和阻塞原因后结束；完成验证与交付后结束，由主控拉取。此文档本身不构成启动授权。
