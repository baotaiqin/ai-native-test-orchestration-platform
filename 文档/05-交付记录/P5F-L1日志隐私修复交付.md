# P5F-L1 / r2 日志与 AI 历史隐私修复交付

## 结论

P5F-L1/r2 已完成磁盘实现与隔离验收。主控记录中的两个缺口均已关闭：结构化日志在最终 JSON 序列化边界统一脱敏；`GET /api/v1/ai/calls` 只返回四个内容字段的脱敏展示副本。主控原始复现由 `2 passed、2 failed` 恢复为 `4 passed`，主控新增边界套件与原复现合计 `16 passed`。

实现保留项目权限语义和审计诊断价值。VIEWER、项目负责人和管理员仍能看到授权项目的模型、Prompt/Schema 标识、实体标识、响应标识、成功状态、错误类型、延迟、重试/Repair/Fallback 状态、Token 用量和成本；项目外用户仍得到 404。展示操作不改写 `AiCallLog` 的原始持久化内容。

本包没有修改 H5 Gateway、Web 失败分析、Run、前端、Runner 或部署代码，没有运行真实服务、真实 AI、正式数据库或正式凭据读取，也没有重启任何服务或执行 P5E 恢复动作。

## 修订历史

r1 关闭了原始四项取证中的日志与 AI 历史泄漏，并补齐顶层 extra、转义/未闭合凭据文本、权限和读取不改库回归。主控在 r1 完成后独立增加边界套件，复测结果为 `13 passed、3 failed`；仅 `mysql+pymysql`、`amqp`、`redis` 三个服务 DSN 的 userinfo 仍会展示，根因是 r1 的 URI 匹配入口只接受 HTTP(S)。

r2 没有为三种协议添加白名单，而是将同一有界逻辑推广到合法的 `scheme://authority`：scheme 允许字母开头及后续字母、数字、`+`、`-`、`.`。因此 HTTP(S)、MySQL/PostgreSQL driver scheme、AMQP(S)、Redis(S) 及其他同形 URI 都使用同一解析与脱敏路径。主控原复现及边界套件最终为 `16 passed`。

## 通用有界脱敏层

新增 `backend/app/core/redaction.py`，提供字符串和结构化值的统一脱敏：

- 识别大小写、空格、连字符、下划线和驼峰变体的 password、credential、authorization、cookie、token、secret、API key、private key、storage state、raw/repair response 等敏感键；敏感键对应值直接整体替换为 `<redacted>`，不会保留带空格、引号或换行的局部内容。
- 识别 Bearer/Basic 凭据、引号或非引号赋值、通用 URI/DSN userinfo 及敏感查询参数；同一行后续的 `run_id`、错误码和普通查询参数仍保留。
- URI/DSN 通过本地标准解析器处理，不发起网络连接。百分号编码 userinfo 会整体脱敏；IPv6 authority、端口、路径和普通查询参数保留。没有 userinfo 或敏感 query 的合法普通 URI 原样返回；不完整 IPv6、非法端口等畸形 authority 安全失败为 `<redacted>`。
- 对完整 JSON 字符串及嵌套 JSON 字符串递归脱敏；普通结构、布尔值、计数与诊断字段保持可用。明确的 Token 计数字段在值为数字时不被误判为凭据。
- 深度最多 6 层、容器最多 64 项、总节点最多 256、单字符串最多 16 KiB、一次结构化处理的文本预算最多 64 KiB；循环引用输出 `<cycle>`，超限输出 `<truncated>`。
- 未知对象只输出类型占位，不调用其 `repr`；bytes 只输出长度；异常只保留脱敏消息和有界的文件名、行号、函数名帧，不输出源码行。

该策略是面向凭据形态和敏感键的确定性展示边界，不是任意自然语言秘密的语义 DLP。没有敏感标签、认证方案、赋值或 URL 上下文的自由文本不会被臆测清空。

## 结构化日志

`backend/app/core/logging.py` 的 `JsonFormatter` 不再直接调用会拼接原始参数的 `LogRecord.getMessage()`。它先安全处理参数，再格式化并脱敏最终消息；顶层和嵌套 extra 作为一个结构统一处理，敏感 extra 键不会绕过脱敏。异常、预格式化异常文本和 stack info 也在最终 JSON 输出前处理。

输出继续保留 timestamp、level、logger、event，以及 module/file/line/function 来源信息。普通 request ID、run ID、call ID 和错误码回归用例均验证可见。最终 `json.dumps` 不使用 `default=str`，未知对象无法通过隐式字符串化泄漏内容。

## AI 调用历史展示边界

`backend/app/modules/prompt_center/service.py` 的 `list_ai_calls` 保留原查询和 `get_project` 权限检查。每条 ORM 记录先构造响应模型，再基于 `model_dump()` 新建展示字典，仅处理：

- `raw_response`
- `repair_response`
- `parsed_result`
- `validation_errors`

处理后的副本重新经过 `AiCallLogResponse` 校验。ORM 实例和 JSON 容器不被原地修改，也没有 commit/flush。独立 SQLite 路由测试在 VIEWER、项目负责人、管理员连续读取前后，对两个文本字段和两个 JSON 字段做了深拷贝逐项相等断言；原始合成标记仍在库中，响应中均不存在。

## 隔离测试覆盖

新增测试覆盖：

- 敏感键的大小写/空格/连字符/驼峰变体，嵌套 mapping/list/JSON 字符串和 header name/value 结构；
- Bearer、Basic/Authorization、带空格及换行的引号值、未闭合引号赋值、通用 URI/DSN userinfo、敏感查询参数；
- HTTP(S)、`mysql+pymysql`、`postgresql+psycopg`、AMQP(S)、Redis(S) 八种代表协议的参数化回归，以及百分号编码 userinfo、IPv6、普通无认证 URI 原样保留和畸形 authority 安全失败；
- 深度、项数、节点、文本预算、循环引用、超长键和拒绝任意 `str`/`repr` 的未知对象；
- 真实 `logging.StreamHandler` + `JsonFormatter` 集成下的消息、参数、顶层/嵌套 extra 和异常；
- 实际 FastAPI `/api/v1/ai/calls` 路由下 VIEWER、项目负责人、管理员展示脱敏，项目外用户 404；
- JSON、非 JSON、嵌套 JSON、validation error 的脱敏，以及 Token/成本/ID/错误码保留和读取不改库。

验证结果：

| 验证 | 结果 |
|---|---|
| 主控 `.codex-validation/test_p5f_privacy_review.py` | `4 passed` |
| 主控原复现 + `.codex-validation/test_p5f_privacy_edge_review.py` | `16 passed` |
| 新增日志与 AI 历史隔离测试 | `15 passed` |
| 主控套件 + 新增隔离测试组合 | `31 passed` |
| Backend 全量 pytest | `412/412 passed`，退出码 0 |
| 相关 Ruff | `All checks passed!` |
| 受影响 Python 文件 `py_compile` | 通过，pycache 隔离到 `.codex-validation/p5f-privacy/pycache/` |

全量 pytest 最终执行使用 `--disable-warnings` 并以退出码 0 完成；未抑制的针对性套件只出现项目既有的 Starlette/httpx 弃用警告，无测试失败。

## 交付文件与 SHA256

| 文件 | SHA256 |
|---|---|
| `backend/app/core/redaction.py` | `458d05f7299b56cd277ec6b63ee3082527c34986b167f7c5c3e9432f7a74ddbb` |
| `backend/app/core/logging.py` | `4736ef0407ba5ba41473ef3d00b17f55f8acaa4dea6b9fad44e031675bebca43` |
| `backend/app/modules/prompt_center/service.py` | `2db4cfbbcc949fdb0ace73f2ce13f4c69b71715e6007ffada65397f5856a2c26` |
| `backend/tests/test_logging_redaction.py` | `e4e1bbacbc0cfbcb75b8f40d668a0f4cced3a9a3ffd9c15aa220ef16980120e1` |
| `backend/tests/test_ai_call_privacy.py` | `80a2be620afdae7aa48f2d072d1ceb67bdeea0db9d493225e27d400a96c7022e` |

## 后续边界

当前已运行的 Backend 没有因本包重启，本交付只证明磁盘代码、实际路由隔离测试和后端全量回归完成，不声称现有服务进程已经加载 P5F-L1。正式服务加载、真实页面只读验证以及 Prompt 输入、Evidence/Trace、Runtime 快照和完整 SSE 链路的剩余隐私验收仍由主控另行安排。
