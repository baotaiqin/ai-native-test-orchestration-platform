# V1-W5 基础 Web 断言后端交付

## 任务包

- 编号 / 修订：`V1-W5 / r1`
- 日期：2026-09-10
- 状态：**执行任务自测通过，待主控验收**
- 范围：Backend WebCase 规则断言 DSL、既有通用计划/完成/报告链验证
- 机器可读证据：`.codex-validation/v1-w5/result.json`
- 隔离 HTTP 验收：`.codex-validation/v1-w5/test_acceptance.py`
- 正式服务加载：未授权、未执行

## 实施结论

Backend WebCase 正式版本 DSL 已接入 Runner W4 冻结的五种规则断言：

- `ASSERT_EXISTS`：必须提交原有 `WebLocator`，资产不得提交 `expected`；
- `ASSERT_ENABLED`：必须提交原有 `WebLocator`，资产不得提交 `expected`；
- `ASSERT_TEXT_EQUAL`：必须提交 Locator 与字符串 `expected`，允许空字符串；
- `ASSERT_INPUT_VALUE`：必须提交 Locator 与字符串 `expected`，允许空字符串；
- `ASSERT_TITLE`：必须提交字符串 `expected`，允许空字符串，资产不得提交 Locator。

新断言继续继承 `extra=forbid`，timeout 默认 `30000`，严格接收整数
`100..600000`。`expected` 上限保持 10000 字符。旧 `ASSERT_TEXT` 仍保持原包含语义，
旧 `ASSERT_VISIBLE/ASSERT_URL` 的资产格式、原 13 种 W2 动作、内容 1MB、Secret、模板、
版本与 ElementVersion 归属检查均未改写。

产品代码只修改 `backend/app/modules/web_cases/schemas.py`。既有计划生成器已经通过
`getattr` 统一生成 `type/timeout_ms/locator/expected` 四字段并为空字段补 `null`；既有
完成回传按锁定 StepRun 节点精确匹配，报告读取锁定结果，因此无需修改 Run 或 Report
产品代码。

## 隔离验收事实

- 五种断言逐项通过合法 DSL；三种 expected 断言逐项验证空字符串合法。
- 五种断言逐项拒绝 99、600001、bool 与字符串 timeout。
- 元素断言逐项拒绝缺 Locator；存在/启用拒绝任何 `expected` 字段；标题拒绝任何
  Locator 字段；expected 缺失、非字符串和超长均拒绝。
- 未知断言、额外字段、残缺/额外字段/双来源 Locator 均 fail-closed。
- 保存并读取 v1、显式批准 v1、创建锁定 v1 的 Run；保存 v2 后，新 Run 在未批准时
  被拒绝，显式批准后新 Run 锁定 v2。
- v2 成为 current 后，旧 Run 下发的仍是 v1 的 URL、五断言、ElementVersion 候选和
 版本 ID。
- 执行计划逐项核对精确四字段；存在/启用 `expected=null`，标题 `locator=null`，空
  expected 原样保留。
- 实际 HTTP 计划的五断言全部通过冻结 Runner `runner.protocol._web_assertions` 解析。
- 缺失、归档、跨项目 ElementVersion 均拒绝；无项目权限无法读取版本；归档 WebCase
  不能创建新 Run。
- 合成完成回传先证明缺一个 assertion trace 会被精确节点匹配拒绝，再以上报完整六节点
  trace 完成 FAILED Run；五个 assertion StepRun 名称、状态及报告 trace 节点均准确。
- completion 与报告中均未出现合成敏感错误标记。

全部写入仅发生于内存 SQLite。没有读取正式凭据，没有连接正式
MySQL/Redis/RabbitMQ/MinIO，没有启动 Backend/Worker、访问真实网络或调用 AI。

## 协议示例

存在断言的资产格式不含 `expected`，执行计划补齐为空：

```json
{
  "type": "ASSERT_EXISTS",
  "timeout_ms": 500,
  "locator": {
    "element_version_id": null,
    "candidates": [
      {"strategy": "css", "value": "#synthetic", "priority": 1}
    ]
  },
  "expected": null
}
```

空标题断言的计划格式：

```json
{
  "type": "ASSERT_TITLE",
  "timeout_ms": 900,
  "locator": null,
  "expected": ""
}
```

## 最终验证

专项 Backend WebCase 测试：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  backend/tests/test_web_cases.py -q
```

结果：`166 passed in 1.66s`。

隔离 HTTP、冻结 Runner 解析、完成与报告验收：

```powershell
$env:PYTHONPATH='backend;runner;backend/tests'
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  .codex-validation/v1-w5/test_acceptance.py -q
```

结果：`1 passed in 4.73s`。

W2 13 动作实际 HTTP 与冻结 Runner 解析回归：

```powershell
$env:PYTHONPATH='backend;runner;backend/tests'
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  .codex-validation/test_v1_w2_controller.py -q
```

结果：`1 passed in 3.50s`。该 W2 探针由主控预置，本任务只读运行。

Backend 全量：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' backend/tests -q
```

结果：`612 passed, 38 warnings in 45.49s`，退出码 `0`。警告均为既有 Starlette
弃用提示及 SQLAlchemy 模型收集提示。

静态检查：

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check backend/app backend/tests
& '.\.venv\Scripts\python.exe' -m ruff check `
  .codex-validation/v1-w5/test_acceptance.py
```

两项结果均为 `All checks passed!`

## 验证过程中的测试夹具纠正

首次专项组合运行得到 `1 failed, 165 passed` 及 Ruff `I001`：合成用例把已经
`RUNNING` 的未执行节点设为 `SKIPPED`，现有状态机正确拒绝该非法转移，同时新增 import
顺序不符合 Ruff。测试改为合法 `SUCCESS` 终态并整理 import；没有修改产品状态机。

第二次专项运行得到 `1 failed, 165 passed`：Web 专用内存 fixture 没有创建通用报告
读取器会查询的既有 API/Scenario 结果表。只在内存 fixture 补齐这两个表后，真实报告链
及全部专项通过；没有修改 Report 产品代码。

## SHA-256 与冻结复核

- `backend/app/modules/web_cases/schemas.py`  
  `47106d6d748f653cef98ef5bb38497e3a25d93d6b7b51efc1d70b442982d2215`
- `backend/tests/test_web_cases.py`  
  `56cf4f9fe69445d088cce5995874eb95c0b5c89e75bb28f593baa095770b25ce`
- `.codex-validation/v1-w5/test_acceptance.py`  
  `f5c7bad168093c14a18884f8bc0a76eee0c1dae988c72e0baafabbd35c564c58`
- 未修改的 `backend/app/modules/runs/service.py`  
  `2831476c4b504dcad9fe30b7b3cff7193dd25e9d36f6ef17863f66c86d3dc271`
- 未修改的 `backend/app/modules/runs/schemas.py`  
  `8254614331966da803bac5c2878f48c97278a8cf943b6c5afb80fffdc011c8f3`
- 冻结 `runner/runner/protocol.py` 前后均为  
  `dd9f92c0aa7bb63c3682a9eb5d39a25f574128522b4843186fbba464d7ebad83`
- 冻结 `runner/runner/models.py` 前后均为  
  `f33405453276d19bac96748bbf69aa93df55bfcc48b75e40a2dbd67c0880a16a`

W2 起始 `schemas.py` 与 `test_web_cases.py` 指纹分别为任务包指定的
`e157a5a83d19d53952a26a812c35baea7f74666ed214a67b2d8bcc01170ee101` 与
`550d8ff9274e645e7521ae1f3fe9535e5fd5d9875681490a7e24711f0ba9d217`。

## 修改清单与边界

修改或新增：

- `backend/app/modules/web_cases/schemas.py`
- `backend/tests/test_web_cases.py`
- `.codex-validation/v1-w5/test_acceptance.py`
- `.codex-validation/v1-w5/result.json`
- `文档/05-交付记录/V1-W5基础Web断言后端交付.md`

未修改 Runner、Frontend、Run/Report 产品代码、迁移、I1 关联实现、正式环境或全局状态
文档；未使用 Git、Claude、内部 Agent 或跨任务消息工具，也未读取正在修改的 Runner
执行器。

## 最终结论

`V1-W5/r1` 执行任务自测通过，等待主控验收。五种基础 Web 规则断言已经进入 Backend
正式 WebCase 版本、人工批准、新 Run、锁定执行计划、完成回传及报告匹配链，并实际
通过冻结 Runner 最终协议解析。本结论不表示正式服务端到端验收、W4 隐私门禁、
Frontend 接入或完整 V1 已完成。
