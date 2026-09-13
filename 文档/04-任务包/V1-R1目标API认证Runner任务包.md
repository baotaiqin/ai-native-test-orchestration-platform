# V1-R1/r1 目标 API 认证底层

主控派发AI-Test-Runner_sol，Sol/xhigh。补ApiExecutor真实请求能力：NONE、BEARER、BASIC、API_KEY(HEADER/QUERY)，以及既有cookies/header/query准确传递。这是目标API认证，不是平台A1登录。只实现独立底层，后端正式门禁/执行流水线由后续包接入，不能简单删除Backend限制。

沿现有request.auth七个字段type/token/username/password/key_name/key_value/placement。允许现有序列化中的无关null/default HEADER，但拒绝无关非null值、额外字段、错误类型、缺必需值；长度沿现有Backend RequestAuth上限。值不trim、不改大小写。BASIC明确用UTF-8编码username:password，username不可含冒号，password允许冒号；HTTP控制字符不得进入头。BEARER注入Authorization；API_KEY注入明确header/query。不允许显式启用header/query/cookie与认证注入产生同名冲突而静默覆盖。

保留ApiRequestTemplate直接构造和from_mapping的边界校验：不能直接构造绕过认证和输入规则。新内存凭据对象/RequestTemplate/RequestValue/Body repr禁止输出值；错误消息不含凭据/URL敏感query。复用现有安全错误类型，不把底层httpx请求repr送入日志。响应原始数据仅供既有内存提取/断言，不为实现本包擅自改变completion wire或把敏感数据新增落盘。

重定向必须有界、遵守单次节点预算，不能因为follow_redirects把Authorization、API Key（含query）、手工Cookie或注入凭据发到不同origin。支持同源重定向；跨源跟随时明确剥离来源凭据及敏感显式头/参数，不让共享cookie jar/请求参数重加回去，HTTPS不得携带凭据降级HTTP。无法证明可安全跟随则受控拒绝，不能泄漏后再报告。旧无凭据请求及Scenario Cleanup经NONE+显式headers的路径保持兼容。

范围仅runner/runner/executors/api.py、必要新的内部HTTP认证/重定向helper，以及runner/tests对应专项。禁止改runner/protocol.py、models.py、web.py、worker/队列/credential/Backend/Frontend：W11 Backend正读取冻结Web protocol。现有RetryPolicy上限与执行器调用接口保持，不追加Runtime/Multipart/401刷新/AI等未授权范围。

基本检查：编译/lint，使用本地受控HTTP或httpx.MockTransport验证实际请求headers/query/cookie/body和返回值，包含四Auth类型、冲突、非法直接构造、同/跨源redirect与敏感repr不泄漏；不真实外部账号/网络/AI、不全量。只验证底层，明确正式API_CASE认证尚未开放。

独占.codex-validation/v1-r1/及文档/05-交付记录/V1-R1目标API认证Runner交付.md；短报告冻结新增构造接口、检查、未验项并结束。禁止跨任务消息/等待、Git、Claude、内部Agent和扩大范围。