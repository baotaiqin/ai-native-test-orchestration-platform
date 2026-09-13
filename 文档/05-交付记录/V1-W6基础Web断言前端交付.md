# V1-W6 基础 Web 断言前端交付

## 任务包

- 编号 / 修订：`V1-W6 / r1`
- 日期：2026-09-10
- 状态：**执行任务自测通过，待主控验收**
- 范围：WebCase 手工编辑器与录制 AI 建议人工编辑中的五种基础断言
- 机器可读证据：`.codex-validation/v1-w6/validation-summary.json`
- 正式服务加载：未授权、未执行

## 实施结论

Frontend 已接入 Backend W5 冻结的五种断言：

- `ASSERT_EXISTS`：元素存在（含隐藏），只提交 Locator 与 timeout；
- `ASSERT_ENABLED`：元素启用，只提交 Locator 与 timeout；
- `ASSERT_TEXT_EQUAL`：元素文本完整相等，提交 Locator、expected 与 timeout；
- `ASSERT_INPUT_VALUE`：输入值完整相等，提交 Locator、expected 与 timeout；
- `ASSERT_TITLE`：页面标题完整相等，只提交 expected 与 timeout。

原 `ASSERT_VISIBLE/ASSERT_TEXT/ASSERT_URL` 保留。页面明确将 `ASSERT_TEXT` 标为“文本
包含”，没有通过改标签混同为完整相等；三种相等断言的空字符串均显示为“期望空字符串”，
不会显示成漏填。

新增 `frontend/src/utils/web-assertions.ts` 作为两个编辑器共用的断言全集、精确工厂、中文
标签、摘要、placeholder 和 expected 校验来源。类型切换始终以工厂重建对象：元素级切换
到 `ASSERT_TITLE` 会删除 Locator，`ASSERT_TITLE` 切回元素级会删除 expected 并生成新
Locator；不存在禁用字段残留。

`expected` 不执行 trim、大小写转换或空值归一化。旧包含断言与 URL 仍要求非空；
`ASSERT_TEXT_EQUAL/ASSERT_INPUT_VALUE/ASSERT_TITLE` 明确允许 `""` 或纯空白。timeout
统一要求整数 `100..600000`，表单控件对上下界外输入约束到冻结边界，保存前校验再次
fail-closed。

## 版本、引用与权限保护

- 手工创建与保存新版本均使用正式 WebCase 资产 DSL；旧版本只读取，保存始终新增版本，
  不覆盖历史版本。
- Locator 继续支持直接 locator 与精确 `ElementVersion`；专项验证保持 `#501` 引用。
- 录制自然语言步骤、全部 W3 动作、Session Profile、parameters、browser/headless 与总预算
  在 AI 已编辑接受中完整保留。
- AI 未编辑接受继续省略 `content`；已编辑接受发送完整当前内容；非法旧包含断言阻止接受，
  但不阻止显式拒绝。
- 接受只生成 DRAFT，不自动批准或创建 Run。本包专项中批准请求为 `0`。
- WebAsset 局部补齐项目成员角色判定：ADMIN、项目 owner、PROJECT_OWNER、TESTER 可写；
  VIEWER 与无法确认写权限的身份只读。新建、保存、批准、归档、录制创建/控制、AI 生成及
  接受/拒绝均有按钮门禁和函数入口门禁。
- 归档 WebCase 保持只读；I1F 需求关联、反向深链、身份序列与写入互斥保护保持。

## W6 真实 Chrome 专项

专项使用随机回环 HTTP 服务提供独立生产构建，并拦截全部 `/api/v1/**` 为合成响应；没有
连接正式 Backend、AI、Runner、数据库、消息队列或真实目标站点。

实际页面操作与请求断言覆盖：

- 从 UI 构造五种新断言及旧 `ASSERT_TEXT`，保存、重载并逐项核对精确字段集合；
- `ASSERT_TEXT_EQUAL/ASSERT_INPUT_VALUE/ASSERT_TITLE` 的空 expected 原样提交；
- `"  MiXeD  "` 与 `"  TiTle  "` 的空白和大小写原样提交；
- 元素→页面、页面→元素切换清除禁止字段；新元素断言缺 Locator 时请求数保持不变；
- 超时输入 `99/600001` 被控件约束为 `100/600000`，期间没有保存请求；
- ElementVersion 精确引用、V1→V2 新版本及 V1 内容不变；归档 WebCase 不可保存；
- AI 未编辑接受省略 content；已编辑接受核对完整 WebCaseContent 和 Session `#22`；
- 非法 AI 草稿没有接受请求，随后仍能拒绝；
- 独立 Viewer 身份读取成员角色、WebCase 历史和 AI 草稿，所有写入计数保持不变。

结果：`97` 个请求、手工创建 `1` 次、版本写入 `2` 次、批准 `0` 次、AI 接受 `2` 次、
AI 拒绝 `1` 次、Viewer 成员读取 `1` 次；`page_errors=0`、
`unexpected_console_errors=0`。

截图目视检查：手工页完整展示八行断言，长类型名、中文精确语义、空字符串 placeholder、
ElementVersion、超时均可读；AI 截图是在编辑中且稳定的断言表单，完整展示八行；Viewer
截图显示只读告警。截图不是载荷验收依据，字段与调用次数由 Playwright 断言验证。

## 回归与构建

类型检查：

```powershell
Set-Location frontend
npm run type-check
```

结果：通过，退出码 `0`。

独立生产构建：

```powershell
Set-Location frontend
npm run build -- --outDir ../.codex-validation/v1-w6/dist --emptyOutDir
```

结果：`1776 modules transformed`，退出码 `0`。仅有 `@vueuse/core` PURE 注释位置和
主 chunk 大小两类非阻断告警。

W6 专项：

```powershell
& '.\.venv\Scripts\python.exe' `
  'frontend/tests/v1_w6_web_assertions_playwright.py' `
  --dist '.codex-validation/v1-w6/dist' `
  --evidence '.codex-validation/v1-w6'
```

结果：`passed`，真实 Chrome，随机端口，`97` 请求。

W3 动作回归：

```powershell
& '.\.venv\Scripts\python.exe' `
  'frontend/tests/v1_w3_web_actions_playwright.py' `
  --dist '.codex-validation/v1-w6/dist' `
  --evidence '.codex-validation/v1-w6/w3-regression'
```

结果：`passed`，`89` 请求；13 种新增动作、原 7 动作、失败保留、AI 决策与迟到响应保护
全部通过，页面错误和非预期控制台错误为 `0`。唯一 HTTP 500 是用例明确制造的保存失败
负例。

I1F 回归：

```powershell
& '.\.venv\Scripts\python.exe' `
  'frontend/tests/p6_i1f_requirement_links_playwright.py' `
  --dist '.codex-validation/v1-w6/dist' `
  --evidence '.codex-validation/v1-w6/i1f-regression'
```

结果：`passed`，`205` 请求、`8` 写入；需求关联、反向入口、只读、失败保留、确认上下文
和写入互斥均通过。

## 验证夹具纠正

首次启动专项因遗漏 `ThreadingHTTPServer` 导入而在浏览器启动前退出；只补充测试导入。
第二次将 DOM 显示值直接设为越界数，发现该方式不会更新 Vue 模型；测试改为从页面真实
操作 InputNumber 并核对其边界约束及零请求。两次均未放宽或修改产品校验。

## SHA-256

- `frontend/src/types/web.ts`  
  `cf8db9f104321753f247836099a1987cc9c2ea80ffccfd5577ef3d7a3e78f97c`
- 未改动的 `frontend/src/utils/web-actions.ts`  
  `bb8ba22dbc5db535fb861276c9e28601b22baeb72deba2fae45f545d3a9d79d1`
- `frontend/src/utils/web-assertions.ts`  
  `c6b9a726df20e8f52d5bdcc3f5a14735b7fe6c9993c769dd3483b9a6acf0feff`
- `frontend/src/views/web/WebAssetView.vue`  
  `83375afc4955d8946db18247dbf255804aaf540b56f594ecb0756db63fc54de6`
- `frontend/src/views/web/WebRecordingWorkbench.vue`  
  `a0ddf59b00c314ab95d8f2aafe57479f83c8573dba017620418d7e96ead8449c`
- `frontend/tests/v1_w6_web_assertions_playwright.py`  
  `270a80075279406aa3359f2ee79efdcca273abc277c7eeb1805849083446dbe0`
- 只读冻结依赖 `backend/app/modules/web_cases/schemas.py`  
  `47106d6d748f653cef98ef5bb38497e3a25d93d6b7b51efc1d70b442982d2215`
- 构建 `assets/WebAssetView-DjKoEJmk.js`  
  `faccf41207e38544a83c6fd118c787450f92761013718af16fbe485463d4ae6c`
- 构建 `assets/web-assets-RD0feK-M.js`  
  `986aaef6b72219f4c06007188a6c85ac47a7b9b92fed0138872bc1c1925a924e`
- 构建 `assets/index-BhEkQyad.js`  
  `76f73b80ca9496d9abec67d008f26b124d8b119171691c554b35085f102b57ae`

## 修改清单与资源边界

修改或新增：

- `frontend/src/types/web.ts`
- `frontend/src/utils/web-assertions.ts`
- `frontend/src/views/web/WebAssetView.vue`
- `frontend/src/views/web/WebRecordingWorkbench.vue`
- `frontend/tests/v1_w6_web_assertions_playwright.py`
- `.codex-validation/v1-w6/`
- `文档/05-交付记录/V1-W6基础Web断言前端交付.md`

`frontend/src/utils/web-actions.ts` 仅作为冻结 W3 指纹与回归依赖读取，未修改。

全部随机回环服务已停止，Chrome browser/context 已关闭，没有创建持久 profile。未修改
Backend、Runner、认证实现、迁移、录制事件协议、正式服务、主控文档或其他任务证据；
未使用 Git、Claude、内部 Agent 或跨任务消息工具。

## 未验边界与最终结论

本包没有验证正式服务端到端、真实目标站点的完整断言执行，也没有声明录制器能够自动捕捉
五种新断言。这些不属于 `V1-W6/r1` 前端接入范围。

`V1-W6/r1` 执行任务自测通过，等待主控验收。五种基础断言已进入 WebCase 手工编辑和
录制 AI 建议人工编辑链，并保持 W3 动作、版本、ElementVersion、Session、模板参数、
I1F 关联与身份上下文保护。本结论不表示正式完整执行或 V1 全阶段已完成。
