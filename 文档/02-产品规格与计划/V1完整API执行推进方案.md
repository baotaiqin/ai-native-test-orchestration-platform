# V1 完整 API 执行推进方案

主控基于当前源码核查的后续开发顺序，尚未作为执行包派发；具体 wire 契约须在相应包派发前冻结。本方案不缩减总规格的 API/Scenario/数据驱动/AI 范围。

## 已确认实现缺口

- backend runs/service.py 的 _api_execution_support_issues 明确拒绝 Auth、Cookie、敏感Header/Query、Runtime、Pre/Post、Extractor、Data Source、Cleanup和AI断言。
- runner executors/api.py 的 ApiRequestTemplate只保存auth_type，执行器明确拒绝非NONE；现有Cookie传递代码不能证明正式后端允许使用。
- API ExecutionPlanResult只覆盖单一case_run_id和request，没有每行迭代计划。
- 资产schema已定义Bearer/Basic/API Key、Pre/Post与Extractor；runtime.py提供预览解释器，不得将预览结果当成Runner真实执行。
- Scenario Cleanup已有受限Secret/Runtime凭据解析与Auth构造，普通HTTP节点仍有限制。可复用已有安全逻辑，但不能简单删除拒绝条件就声称支持。

## 开发依赖顺序

1. 受认证请求底层：严格Auth四种形状、Cookie/Header/Query和内存Secret传递；同名凭据来源冲突明确拒绝，不在跨源重定向中泄漏Authorization、API Key、Cookie；保留有限重试预算与安全错误。同步检查Scenario普通HTTP与Cleanup，不破坏既有清理凭据路径。
2. 单用例真实执行流水线：锁定不可变CaseVersion，在Runner执行Pre→Request→Extractor→Post→Assertions→Cleanup；沿现有Action/Extractor/Registry语义复用实现。不能把Backend预览执行当Runner现场，不把未执行步骤标PASS。失败/取消/超时也按规定进入有界Cleanup，原错误与清理错误分开记录。
3. 每行数据驱动：创建时冻结DatasetVersion和稳定行序，对每行持久CaseRun与脱敏参数快照；单Slot串行执行而非无界并发。case_run_id/row_index是明确身份，重复派发/回传不能重复执行成功行；取消、统计和报告按实际迭代呈现。CSV/Excel/Faker/MySQL/Runtime按资产既有来源逐项接入，不能只支持一种后宣称完成。
4. AI断言与动态认证刷新：复用受审计Gateway/Prompt版本/成本日志，确定性失败优先，低置信/冲突REVIEW；执行中动态Token引用必须在内存可用且公开报告脱敏。401刷新最多一次与原步骤重试计数独立、有界，不无限循环；明确绑定的登录流程必须有版本身份。
5. Multipart与资源输入、Frontend配置及完整报告接入：受管上传资产/有界数据，不接受任意宿主路径读取；前端已有资产字段逐一对齐实际执行支持能力；未实现项保留准确提示直到接入。

## 横向必须保持

- 明文凭据只存在受授权的单次内存执行通道；计划/模型repr、异常、日志、落盘证据、公开报告与SSE不可泄漏。请求和响应的原始值可在内存用于提取/断言，持久化前生成脱敏副本，不能先脱敏破坏执行也不能直接持久化原文。
- 保持项目归属、锁定版本、授权Runner/claim、取消/总超时、0或1步骤重试、Evidence限制和幂等completion。变更执行计划形状须明确版本兼容，不能要求旧Runner猜测新字段。
- 现有Scenario SQL、Script、Cleanup、Resource Registry可复用共享实现；不复制成不一致的第二套执行语义。
- 平台A1认证与目标API认证是不同模块，不混用平台用户密码或JWT签名密钥作为目标凭据。

## 开发与验收

按用户指令，每包只基本检查与有限关键路径，完整真实API/数据集/401刷新/AI/跨模块矩阵集中后置。当前四个活跃任务不收到本方案追加消息；由主控待其交付后分别派包。本方案是排序依据，不是功能已实现证据。