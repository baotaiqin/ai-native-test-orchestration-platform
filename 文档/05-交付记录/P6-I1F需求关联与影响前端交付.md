# P6-I1F/r2 需求关联与影响前端交付

## 交付结论

P6-I1F/r2 前端修订任务包已完成并通过隔离验收。r1 的需求精确版本关联、保留历史的移除/再关联、确定性影响分析、反向需求入口和版本深链均保留；主控发现的“旧确认跨身份派发写入”和“并发操作污染 loading”两项缺陷均已修复，主控原始测试在最终 r2 构建上为 `2 passed`。

本交付只证明前端对冻结 I1 API 的实现与合成回归通过，不宣称整体 I1 或正式联调完成。未连接正式 Backend、数据库、AI、Runner、Run 或共享 Demo，未创建正式业务数据。

## r2 修订

- 移除操作在打开确认框之前冻结 access token、完整用户身份、项目 ID、需求 ID、组件代次和操作 ID；确认返回后、发送 DELETE 之前再次核验身份、目标、组件存活、写权限及该关联仍为当前活动记录。
- 组件卸载、项目/需求切换或跨标签身份变化会使旧操作代次立即失效并释放本组件写入状态；旧确认框即使随后被点击，也不能启动 DELETE。
- 创建、确认中移除、请求中移除统一进入单操作互斥状态。表单与所有移除按钮在操作期间一起锁定，重复点击无法派发第二次写入。
- 写请求以独立 operation ID 收尾。旧请求结束时不能清除新上下文中新请求的 loading；当前操作无论成功、失败或失效都会释放自己的状态。
- 创建/移除成功后仍重新读取关联列表，以服务端最终状态为准；失败和上下文失效不自动重试，也不会永久禁用后续正常写入。
- r1 的主摘要和专项结果已先保存为 `validation-summary-r1.json`、`playwright-result-r1.json`，再写入 r2 证据。

## 冻结接口核验

- `backend/app/modules/requirement_links/schemas.py` SHA256：`b5855e2d8e1a0f8097b686abe8d1866ecea1970a73c172f52f27b6a66c8f840a`
- `backend/app/modules/requirement_links/router.py` SHA256：`9062ea0e537855650c23612e2bb99e77c932db10b5180bce0e41f0ed17dd6e1f`

两项均与任务包冻结值一致。本任务未修改 Backend、Runner、迁移或正式服务。

## 实现范围

### 需求侧关联管理

- 新增冻结契约对应的 TypeScript 类型与 API：创建、分页读取、保留历史移除、影响分析、两类资产反向读取。
- 人工创建关联必须显式选择：`TEST_CASE` 或 `WEB_CASE`、资产 ID、需求版本 ID、资产版本 ID、置信度；提交摘要展示实际固定关系，不读取 current 替换用户选择。
- 原 TestCase 的 `case_type=WEB` 显示为“原 TestCase · WEB”，独立 WebCase 显示为“独立 WebCase”；选择器 key、请求路径和深链参数都包含资产种类，同号整数 ID 不串类。
- 关联列表默认包含已移除历史；展示关联 ID、前驱 `supersedes_link_id`、来源、关系、置信度、记录的双方版本、绑定说明与当前关联历史语义。
- 缺失资产历史版本显示“历史版本未记录”，缺失需求来源版本显示“需求来源版本未知”。`CURRENT_ASSOCIATION_NOT_RUN_SNAPSHOT` 明确显示为当前关联历史，而不是 Run 快照。
- `LEGACY_UNKNOWN` 时间保留原字符串并标注“旧记录时区未知”；UTC 时间以显式 `Asia/Shanghai` 格式显示并标注源 UTC，不把旧记录套用浏览器时区。
- 归档项目、归档需求及 VIEWER 禁止新增/移除；已授权历史仍可读。

### 确定性影响分析

- From/To 使用 RequirementVersion ID 显式选择；Diff 调用现有按版本号接口，页面同时展示内容是否变化、additions、deletions 与 unified diff。
- 表格把 `selected_scope_status/reason` 和 `recorded_baseline_status/reason` 分成独立列，不混成单一结论。
- 分别呈现无变化、无有效关联、基线未知、已移除历史、请求失败和无权限；失败不会清空后伪装成空影响。
- 只展示确定性潜在范围及人工确认提示；没有自动修改用例、创建版本、调用 AI 或发起 Run 的入口。

### 正反深链

- 需求 → 原 TestCase：`project_id + test_case_id + version_id + link_source=requirement`。
- 需求 → 独立 WebCase：`project_id + web_case_id + version_id + link_source=requirement`。
- 两类资产 → 需求：`project_id + requirement_id + requirement_version_id + tab=links`。
- 深链刷新后按项目、明确资产种类、资产 ID 和历史版本定位；历史版本只用于查看，不自动切换 current。
- 反向关联面板按类型调用 `/test-cases/{id}/requirements` 或 `/web-cases/{id}/requirements`。普通资产详情下采用显式折叠入口，需求关联深链自动展开，避免无关页面后台请求并保持 T3 既有行为。

### 请求上下文与晚到结果

- 新增身份快照工具，以 access token 与完整 current user 原文组成身份指纹。
- 工作台父层项目列表、需求树、成员角色、需求详情、版本列表和 Diff 均使用单调请求代际，并验证项目、需求、身份和组件存活状态。
- 关联列表、资产列表、资产版本、影响分页、反向读取和写入后续动作分别维护代际；A→B→A、切项目/需求/版本、卸载、退出及跨标签换身份时旧响应不能覆盖新上下文。
- 已发往服务端的单次写入不宣称取消；迟到成功不会刷新旧需求、清空新表单或显示旧身份成功消息。失败保留显式选择且不自动重发。
- TestCase 详情加载同步清除上一资产的版本/关联输入，同时保留 V1-T3 保存后重新打开当前抽屉的既有行为。

## 验证结果

### 类型与构建

- `npm run type-check`：通过。
- `npm run build -- --outDir ../.codex-validation/p6-i1f/dist-r2 --emptyOutDir`：通过；仅有既存 Rollup pure-comment 与大 chunk 警告。

### 主控缺陷复现回归

原样执行 `.codex-validation/test_p6_i1f_controller.py`，通过 `I1F_CONTROLLER_DIST` 指向最终 `dist-r2`：`2 passed in 9.14s`。

- 身份 A 打开移除确认，另一标签切换到身份 B，再点击旧确认：未发出 DELETE。
- 挂起创建 POST 时写入互斥生效；请求释放后创建 loading 正确结束，最终列表包含服务端实际创建结果。

机器结果：`.codex-validation/p6-i1f/controller-r2-result.json`。

### P6-I1F 真实 Chrome 合成回归

脚本：`frontend/tests/p6_i1f_requirement_links_playwright.py`

结果：通过；真实 Chrome `channel=chrome`，205 次受控请求、8 次合成写请求，0 个未处理 API。验证覆盖：

- 两资产种类同号隔离与两条反向 API；
- 显式需求/资产版本提交及真实 payload；
- 创建、移除、再关联和前驱修订；
- 旧未知时间、缺失历史版本、缺失需求基线；
- 多个记录基线结论、选定范围结论、Diff 和稳定分页；
- 无变化、无关联、500、403、归档和 VIEWER；
- 正反深链和刷新；
- 项目 A→B→A、跨标签身份变化、组件卸载；
- 晚到成功不刷新旧上下文；写失败保留表单且请求数确认无自动重试。
- 同一身份离开页面或切换到另一需求后，点击旧移除确认均不会发出 DELETE；随后新上下文中的正常移除和创建仍各成功一次。

机器结果：`.codex-validation/p6-i1f/playwright-result.json`。

### 既有回归

- V1-T3/r2 真实 Chrome 回归：通过，18 组检查；4 次 TestCase 写、6 次建议 patch、5 次建议决策、2 次批量写；页面错误和意外 console error 均为 0。结果见 `.codex-validation/p6-i1f/t3-regression-r2/result.json`。
- P6-X2/r2 真实 Chrome 回归：通过，24 组身份/导出检查；27 次导出请求、11 次下载、0 次业务写；页面错误和意外 console error 均为 0。结果见 `.codex-validation/p6-i1f/x2-regression-r2/result.json`。

## 证据

- `.codex-validation/p6-i1f/validation-summary.json`
- `.codex-validation/p6-i1f/validation-summary-r1.json`
- `.codex-validation/p6-i1f/playwright-result.json`
- `.codex-validation/p6-i1f/playwright-result-r1.json`
- `.codex-validation/p6-i1f/controller-r2-result.json`
- `.codex-validation/p6-i1f/01-link-history-same-id.png`
- `.codex-validation/p6-i1f/02-impact-baselines-diff-pagination.png`
- `.codex-validation/p6-i1f/03-archived-readonly.png`
- `.codex-validation/p6-i1f/04-testcase-reverse-link.png`
- `.codex-validation/p6-i1f/05-webcase-reverse-link.png`
- `.codex-validation/p6-i1f/06-request-generation-and-write-failure.png`
- `.codex-validation/p6-i1f/07-confirmation-context-and-write-mutex.png`
- `.codex-validation/p6-i1f/t3-regression-r2/`
- `.codex-validation/p6-i1f/x2-regression-r2/`

所有专项截图均为中文界面。需求关联展开模式隐藏右侧版本栏以提供完整表格宽度；人工检查未见选择器文字溢出或关联表不可读。TestCase 抽屉在过渡结束后截图，反向入口及定位版本可读。

## r2 改动文件

- `frontend/src/views/requirements/components/RequirementLinksImpactPanel.vue`
- `frontend/tests/p6_i1f_requirement_links_playwright.py`
- `.codex-validation/p6-i1f/`
- `文档/05-交付记录/P6-I1F需求关联与影响前端交付.md`

r1 其余七个源文件保持原 SHA256；最终九个源码及 r2 构建指纹详见 `.codex-validation/p6-i1f/validation-summary.json`。

## 剩余边界

- 正式 Backend/MySQL 联调与真实权限数据验证仍受 I1 总门禁约束，由主控在上游就绪后统一安排。
- 本包没有尝试创建真实关联、调用真实 AI 或发起 Run。
