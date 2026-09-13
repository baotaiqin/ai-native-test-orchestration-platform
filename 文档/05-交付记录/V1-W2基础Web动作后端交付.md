# V1-W2 基础 Web 动作后端交付

## 任务包

- 编号 / 修订：`V1-W2 / r1`
- 日期：2026-09-10
- 状态：**执行任务自测通过，待主控验收**
- 范围：Backend WebCase DSL 与既有通用 Web 执行计划序列化
- 机器可读证据：`.codex-validation/v1-w2/result.json`
- 正式服务加载：未授权、未执行

## 实施结论

Backend WebCase 正式版本 DSL 已接入 Runner W1 冻结的 13 种基础动作：

`RELOAD`、`BACK`、`FORWARD`、`DOUBLE_CLICK`、`RIGHT_CLICK`、`CLEAR`、
`HOVER`、`CHECK`、`UNCHECK`、`RADIO`、`ENTER`、`TAB`、
`WAIT_NETWORK_IDLE`。

产品代码只修改 `backend/app/modules/web_cases/schemas.py`。现有
`runs/service.py` 已逐项读取动作的既有属性并构造固定七字段计划，因此无需重构或增加
分支；队列、预算、取消、重试、历史完成兼容及录制输入路径均未修改。

## 契约与拒绝边界

- `RELOAD/BACK/FORWARD/WAIT_NETWORK_IDLE` 只接收 `type`、`timeout_ms`、
  `failure_policy`，不接收业务字段。
- 其余 9 种动作必须提供原有 `WebLocator`，继续支持 direct locator 或锁定
  `element_version_id` 二选一。
- 13 种动作统一沿用 `timeout_ms=100..600000` 与 `STOP/CONTINUE`。
- 新 locator 动作拒绝非适用的 `url/value/key`；因此 `ENTER/TAB` 的固定按键不能由
  payload 覆写。
- 每个动作模型继续继承 `extra=forbid`；未知 type、缺失/非法 locator、越界预算、
  非法 failure policy 和额外字段均 fail-closed。
- 未新增 payload 字段，执行计划继续使用 `schema_version=1` 和
  `type/timeout_ms/failure_policy/url/locator/value/key` 七字段；未使用字段显式为 `null`。
- 原 7 种动作与 3 种断言仍由 Backend 全量回归覆盖；本包没有接入 W4 新断言。

## 隔离验收

专项测试覆盖：

- 13 种动作分别通过合法 DSL；13 种动作分别拒绝上下界外预算；
- 9 种 locator 动作分别拒绝缺 locator，并分别拒绝 `url/value/key` 语义覆写；
- 4 种页面动作分别拒绝 `url/locator/value/key`；
- 未知 type、动作额外字段、locator 额外字段、残缺/双来源 locator、非法 failure
  policy 均拒绝；
- 真实保存和读取 v1、批准 v1、创建锁定 v1 的 Run、保存未批准 v2 并拒绝新 Run、
  批准 v2 后创建锁定 v2 的新 Run；
- v2 成为 current 后，旧 Run 下发的仍是 v1 的 URL、13 种动作和版本 ID；
- 计划逐动作核对精确七字段、页面动作空 locator、locator 候选和全部未用字段 `null`；
- 缺失、归档、跨项目 ElementVersion 均拒绝；归档 WebCase 不得创建新 Run；
- 隔离 HTTP 资产→批准→Run→dispatch→claim→plan 链的全部 13 种动作，实际通过冻结
  Runner `runner.protocol._web_actions` 解析。

所有写入仅发生于内存 SQLite。没有读取正式凭据，没有连接正式
MySQL/Redis/RabbitMQ/MinIO，没有启动 Backend/Worker 或调用 AI。

## 协议示例

页面动作：

```json
{
  "type": "RELOAD",
  "timeout_ms": 1000,
  "failure_policy": "CONTINUE",
  "url": null,
  "locator": null,
  "value": null,
  "key": null
}
```

固定按键 locator 动作：

```json
{
  "type": "ENTER",
  "timeout_ms": 2000,
  "failure_policy": "STOP",
  "url": null,
  "locator": {
    "element_version_id": null,
    "candidates": [
      {"strategy": "css", "value": "#target", "priority": 1}
    ]
  },
  "value": null,
  "key": null
}
```

## 验证结果

专项 Backend WebCase 测试：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  backend/tests/test_web_cases.py -q
```

结果：`110 passed in 1.77s`。

隔离 HTTP 与冻结 Runner 最终解析器兼容测试：

```powershell
$env:PYTHONPATH='backend;runner;backend/tests'
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  .codex-validation/test_v1_w2_controller.py -q
```

结果：`1 passed in 3.75s`。该用例由主控预置，本执行任务只读运行，未修改。

Backend 全量：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' backend/tests -q
```

结果：`556 passed, 38 warnings in 39.43s`，退出码 `0`。警告均为既有 Starlette
弃用提示及 SQLAlchemy 模型收集提示。

静态检查：

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check backend/app backend/tests
```

结果：`All checks passed!`

## SHA-256 与冻结依赖复核

- `backend/app/modules/web_cases/schemas.py`  
  `e157a5a83d19d53952a26a812c35baea7f74666ed214a67b2d8bcc01170ee101`
- `backend/tests/test_web_cases.py`  
  `550d8ff9274e645e7521ae1f3fe9535e5fd5d9875681490a7e24711f0ba9d217`
- 未修改的 `backend/app/modules/runs/service.py`  
  `2831476c4b504dcad9fe30b7b3cff7193dd25e9d36f6ef17863f66c86d3dc271`
- 未修改的 `backend/app/modules/runs/schemas.py`  
  `8254614331966da803bac5c2878f48c97278a8cf943b6c5afb80fffdc011c8f3`
- 冻结 `runner/runner/protocol.py` 前后均为  
  `dd9f92c0aa7bb63c3682a9eb5d39a25f574128522b4843186fbba464d7ebad83`
- 冻结 `runner/runner/models.py` 前后均为  
  `f33405453276d19bac96748bbf69aa93df55bfcc48b75e40a2dbd67c0880a16a`

## 修改清单与边界

修改：

- `backend/app/modules/web_cases/schemas.py`
- `backend/tests/test_web_cases.py`
- `.codex-validation/v1-w2/result.json`
- `文档/05-交付记录/V1-W2基础Web动作后端交付.md`

未修改 Runner、Frontend、迁移、I1 关联实现、正式环境或全局状态文档；未使用 Git、
Claude、内部 Agent 或跨任务消息工具。

## 最终结论

`V1-W2/r1` 执行任务自测通过，等待主控验收。13 种基础 Web 动作已经进入 Backend
正式 WebCase 版本、人工批准、新 Run 与锁定版本执行计划链，且实际通过冻结 Runner
最终协议解析。本结论不表示正式服务端到端验收、W4 隐私验收、Frontend 接入或完整
V1 已完成。
