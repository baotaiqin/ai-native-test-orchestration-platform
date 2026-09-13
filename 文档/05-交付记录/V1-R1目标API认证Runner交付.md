# V1-R1/r1 目标 API 认证 Runner 交付

状态：已实现、基本检查通过、待集中验收。正式 API_CASE 认证尚未开放，仍待后端执行计划与领域门禁后续接入。

## 实现

- `ApiExecutor` 支持目标 API 的 `NONE`、`BEARER`、UTF-8 `BASIC`、`API_KEY`（`HEADER` / `QUERY`），保留显式 Header、Query、Cookie 与既有请求体传输。
- 七字段认证对象严格校验字段组合、类型和后端既有长度上限；无关字段只允许 `null`，非 API Key 的 `placement` 只允许默认 `HEADER`。Basic 用户名禁止冒号，密码允许冒号。
- 显式请求项与认证注入按 HTTP 名称语义检查冲突，Header/Cookie 控制字符在发送前拒绝。
- 重定向由 Runner 最多逐跳 10 次，所有跳转共用节点 `timeout_ms`。同源保留认证、手工 Cookie 和 307/308 请求体；跨源剥离认证、Cookie、敏感查询和非安全白名单 Header，并通过原始 `httpx.Request` 绕过客户端共享 Cookie、默认参数及默认认证。跨源保留请求体或无法证明安全发送时受控拒绝。
- `RequestAuth`、`ApiRequestTemplate`、`RequestValue`、`RequestBody` 默认 `repr` 不输出凭据或正文；目标网络、非法 URL 和跳转错误只返回安全消息。
- `NONE + 显式 Authorization Header` 仍可供 Scenario Cleanup 使用；RetryPolicy、completion wire 和响应内存快照未扩展。

## 冻结构造接口

```python
from runner.executors.api import ApiRequestTemplate, RequestAuth

auth = RequestAuth(
    type="API_KEY",
    key_name="X-Target-Key",
    key_value="resolved-in-memory-value",
    placement="HEADER",
)
request = ApiRequestTemplate(method="GET", url="https://target.example/api", auth=auth)
```

`ApiRequestTemplate.from_mapping()` 继续接收原七字段 `request.auth` JSON 对象；`template.auth_type` 保留为只读兼容属性。新对象从 `runner.executors.api` 导入，本包未改公共 re-export 文件。

## 基本检查

- 定向 Pytest：223 passed（9.07s）。覆盖真实 `httpx.MockTransport` 出站 Header/Query/Cookie/Body、四类 Auth、严格校验与冲突、直接构造、同源/跨源/HTTPS 降级、总超时预算、敏感 `repr`、协议/Consumer/隔离/重试上限/Scenario Cleanup 兼容。
- Ruff：通过。
- `py_compile`：使用 `.codex-validation/v1-r1/pycache` 独占缓存通过。
- 结果：`.codex-validation/v1-r1/result.json`。

冻结文件未修改：`runner/runner/protocol.py`、`runner/runner/models.py`、`runner/runner/executors/web.py`、`runner/runner/worker.py`。

## 待后续

- 后端正式 API_CASE 创建/执行门禁及执行计划的凭据解析接入。
- 集中阶段的 Runner 全量、真实外部目标与正式验收；本包未运行这些检查。
