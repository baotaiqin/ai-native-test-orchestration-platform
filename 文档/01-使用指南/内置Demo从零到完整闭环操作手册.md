# 内置 Demo 从零到完整闭环操作手册

> 适用版本：完整 V1 本地开发环境  
> 更新日期：2026-09-12  
> 目标：首次只填写一次真实模型配置，由平台自动准备其余资产，完成“AI 评审 → AI 用例 → 人工确认 → 执行/AI 断言 → 分析/缺陷 → 审计”的可重复演示

本手册面向第一次使用平台的演示者。操作以当前页面名称和按钮为准，所有目标数据均来自本机合成 Demo，不连接外部业务系统。

启动环境的完整解释参见[《PyCharm 启动指南》](./PyCharm启动指南.md)；Demo 的全部接口和安全边界参见[《演示系统使用说明》](./演示系统使用说明.md)。

---

## 1. 完成后会得到什么

按默认 AI 主线完成后，平台中应出现：

- 一个可运行项目和一个指向本机 Demo 的环境；
- 一棵自动导入的“资源管理”需求树；
- 一份自动导入的内置 Demo OpenAPI 固定版本；
- 八套开箱即用的 Prompt、Output Schema 和项目模型绑定；
- AI 需求评审和 AI API Case 建议，以及对应人工决策；
- 一个可执行的 API Case、AI 语义断言和至少一条 Run；
- 一个已批准的 Web Case，以及成功、失败两类 Run；
- API/Web 的步骤结果、运行事件、Evidence 和可下载报告；
- 一个旧版本失败、新版本成功且历史不被覆盖的版本链。

沿主线继续可以演示：

- Requirement 或 Swagger AI 建议及人工审核；
- Web 录制结果的 AI 整理；
- Locator Healing 候选、验证 Run、人工接受与新 DRAFT 版本；
- Web 失败分析、AI 调用审计和缺陷草稿。

### 1.1 两条演示路线

| 路线 | 是否需要真实模型 | 用途 |
|---|---|---|
| AI 主线（默认） | 只需首次填写 Base URL、模型名、API Key | 自动初始化资产，展示真实 AI 建议、断言、分析和审计 |
| 确定性保底线 | 不需要 | 模型服务临时不可用时，验证平台和 Runner 基础闭环 |

正式演示前应在本机完成一次 AI 初始化和一次真实调用预演。不要把真实 API Key 写进本文档、Case、截图或终端命令；初始化页提交成功后会立即清空输入框，后端只保存加密值且不回显。

---

## 2. 账号、地址与演示材料

### 2.1 两套账号不要混用

| 对象 | 地址 | 用户名 | 密码 |
|---|---|---|---|
| 测试编排平台 | `http://127.0.0.1:5173/login` | `admin` | `admin123` |
| 被测 Demo 网站 | `http://127.0.0.1:8765/login` | `demo` | `demo-pass` |

`admin / admin123` 仅是空白本地开发库的默认管理员。首次登录后应修改密码；若已经修改，以当前本地密码为准。

### 2.2 本手册使用的文件

| 文件 | 页面 | 用途 |
|---|---|---|
| `demo/fixtures/资源管理需求.md` | 需求管理 | 导入 Requirement Tree |
| `demo/fixtures/openapi.json` | API 定义 | 导入 Demo OpenAPI 3.0.3 契约 |
| `demo/fixtures/resources.csv` | 数据集 | 导入 5 行资源测试数据并预览 |

### 2.3 本机端口

| 服务 | 端口 | 快速检查 |
|---|---:|---|
| 平台前端 | 5173 | `http://127.0.0.1:5173` |
| 平台后端 | 8000 | `http://127.0.0.1:8000/health` |
| Demo 目标 | 8765 | `http://127.0.0.1:8765/health` |
| MySQL | 3306 | Alembic `current` |
| Redis | 6379 | Compose 状态或后端 readiness |
| RabbitMQ | 5672 | Compose 状态；管理台为 15672 |
| MinIO | 9000 | Compose 状态；Console 为 9001 |

所有地址只适用于运行项目的这台电脑。Demo 固定绑定 `127.0.0.1`，手机或局域网内其他电脑不能直接访问。

---

## 3. 启动前检查

### 3.1 启动中间件

先启动 Docker Desktop，等待 Engine 显示 Running。然后在项目根目录执行：

```powershell
docker compose -f .\deploy\docker-compose.dev.yml up -d
docker compose -f .\deploy\docker-compose.dev.yml ps
```

默认只启动 Redis、RabbitMQ 和 MinIO，不启动 Compose MySQL。平台默认使用本机 `127.0.0.1:3306` 的 MySQL。

预期结果：Redis、RabbitMQ、MinIO 最终显示 healthy。不要执行 `down -v`，它会删除持久卷。

### 3.2 核对数据库迁移

确认本机 MySQL 已启动，然后执行：

```powershell
cd backend
..\.venv\Scripts\python.exe -m alembic heads
..\.venv\Scripts\python.exe -m alembic current
```

预期结果：两条命令均显示：

```text
20260912_0056 (head)
```

若 `current` 落后，先确认连接的是本地开发库，再执行：

```powershell
..\.venv\Scripts\python.exe -m alembic upgrade head
```

不要手写 SQL 修改 Alembic 版本，也不要把历史 `.local-backups/` 数据导入当前演示库。

### 3.3 启动后端、前端和 Demo

在 PyCharm 中分别运行：

1. `后端 FastAPI`；
2. `前端 Vue`。

Demo 目前不属于复合运行配置，需要在项目根目录另开一个终端执行：

```powershell
.\.venv\Scripts\python.exe -m demo.server --port 8765
```

保持 Demo 终端运行。分别打开：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:5173/login
http://127.0.0.1:8765/health
http://127.0.0.1:8765/login
```

预期结果：两个 health 地址返回成功；平台和 Demo 分别显示登录页。

### 3.4 登录平台

打开 `http://127.0.0.1:5173/login`，输入：

```text
用户名：admin
密码：admin123
```

预期结果：进入“平台概览”。空白开发库的项目数、测试用例数、运行数为 0 是正常现象。日常操作先从左侧“项目门户”选择项目；平台级模型、Prompt、Runner 和用户配置保留在公共菜单中。

### 3.5 首次一键初始化 AI 主演示

操作路径：

```text
左侧导航 → AI 主演示
```

页面只要求填写三项：

| 字段 | 填什么 |
|---|---|
| 模型服务地址 | OpenAI-compatible Base URL，例如 `https://api.openai.com/v1`；不要带 `/chat/completions` |
| 模型名 | 服务商实际支持的模型标识 |
| API Key | 当前模型服务的真实密钥；只在密码框中输入 |

点击“验证并准备演示素材”。平台先发起最小连通性探测；验证通过后自动创建或复用以下保留名资产：

- 项目 `AI 智能演示`（编码 `AI_DEMO`）；
- 默认环境 `DEMO_LOCAL`，Base URL 为 `http://127.0.0.1:8765`；
- 模型渠道和主模型配置；
- 需求评审、AI 测试设计、API 用例生成、API 场景编排、Web 录制转用例、AI 语义断言、定位器自愈、Web 失败分析、缺陷草稿九套 Prompt 和 Output Schema；
- 上述九类项目模型绑定；
- “智能商城完整演示需求”需求树：20 个章节，覆盖身份认证、商品、用户、订单、资源、慢响应、受控重试、下载和 Web 自动化；
- 内置 Demo OpenAPI：14 个与本机 8765 服务一致的 API 操作。

预期结果：页面初始化清单全部变为完成，并出现“打开需求工作台”。初始化器不会创建 AI 评审、AI 用例、测试场景、数据集、运行、报告或缺陷草稿，这些成果全部留给演示者现场生成和审核。重复提交不会复制同一版本的素材；它只补齐新版需求/API，或更新模型地址、模型名和绑定。

需求树的业务编号应为 `REQ-0001`～`REQ-0020`，与数据库内部记录号无关。后续在此项目新增需求时从 `REQ-0021` 继续；在其他新项目创建第一条需求时仍从 `REQ-0001` 开始。测试用例、场景和 Web 用例同样按项目分别使用 `TC-00001`、`SC-00001`、`WC-00001` 起始编号。删除资产不会回收旧编号，避免历史报告和审计产生歧义。

若模型验证失败，页面会保留安全错误摘要并停止后续建资产。修正模型配置后再次提交即可续建；页面和 API 响应都不会返回 API Key。首次成功保存后，后续更新或从旧版 Demo 补齐新版素材时可以把 API Key 留空，平台会沿用已加密保存的密钥，不要求重复填写。

### 3.6 AI 主演示的页面点击顺序

1. 在“AI 主演示”页先确认“完整需求树”为 20 个章节、“OpenAPI”为 14 个操作，再点击“打开需求工作台”。系统会直接进入 `AI 智能演示` 的独立项目工作区；左侧项目目录选中“需求管理”，顶部单行显示返回项目门户、当前项目切换器和项目状态，不再重复显示平台账号角色或全局“新建测试用例”。初始化导入的整份需求作为一个已发布的完整文档 V1，树中 `1`、`1.1` 等编号来自实际目录位置，不是数据库编号。首次进入不会自动替用户选择第一个需求，中间会提示“选择或创建一个需求”，右侧两个 AI 页签处于禁用状态。必须在需求树点击“智能商城完整演示需求”，或点击“创建订单”“受控重试”“登录页面”等子需求，左侧节点出现高亮后 AI 才会明确以该节点和当前完整文档版本为上下文。需求树是独立的固定高度滚动区：页头与刷新按钮保持可见，鼠标滚轮或键盘焦点只滚动树节点，不会再把整页向下撑开。
2. 右侧切换带 AI 图标的“AI 评审”，在醒目的“AI 需求评审”功能卡中点击“开始 AI 评审”；选择自动创建的 `[内置 Demo] 需求智能评审` Prompt。提交后平台立即创建后台评审记录，弹窗可用“关闭，后台继续”退出，也可以离开页面。右侧“本需求评审记录”只显示当前需求；页面顶部“AI 记录”打开居中记录弹窗，默认查看项目全部需求，并可切换“评审记录 / 设计记录”。选择章节或根需求时，AI 会同时读取其全部有效子需求。人工核对后可“确认评审”冻结结论或“拒绝”保留审计；确认本身不会修改需求、增加版本或生成用例。如果评审结论没有重大问题，直接在当前 V1 做 AI 测试设计；如果需要系统性修订，在已确认评审详情点击醒目的“AI 辅助修订并创建新版”。工作台会立即打开并在后台自动把建议归属为内容修订、新增需求、删除需求或待确认；规划期间关闭窗口不取消后台调用，重新从同一评审进入会恢复等待或直接展示结果。可使用“全屏查看”展开长需求树和左右对比。内容修订提供原文/AI 修订稿并排对比，新增和删除各自集中展示并支持逐项或一键加入草稿。完整树始终可见，但只允许修改所选需求及其全部下级，其他分支带锁只读。规划结果会保存在评审记录中，但尚未发布的手工草稿关闭后不会保存；所有应用操作均可在发布前撤销，最后统一发布 V2，之后可继续复审或直接测试设计。
3. 右侧切换带 AI 图标的“AI 用例”，在“AI 测试设计”功能卡中点击“开始 AI 设计”。弹窗会立即出现，同时创建一条后台设计记录；`[内置 Demo] AI 测试设计` 会真实调用已配置模型，阅读当前需求及其所有子需求，并结合项目内有效 API 契约提取 `CP-01` 等检查点、推荐接口、角色、理由及覆盖关系。模型返回后，平台还会校验接口编号和检查点引用，并按认证、创建与清理关系补充必要依赖。第一步核对检查点，第二步确认接口闭环并取消不相关项，第三步使用 `[内置 Demo] API 用例生成` 创建后台生成任务。

   设计分析期间可点击“关闭，后台继续”或直接关闭窗口，任务不会取消。“AI 用例”功能卡中的“本需求设计记录”只显示当前需求；页面顶部“AI 记录”切换到“设计记录”后，按当前项目汇总所有需求的设计任务，并显示等待分析、正在分析、分析完成或分析失败。因此先为需求 1 发起任务、关闭窗口，再为需求 2 发起任务时，两条记录会独立执行并在项目级记录中同时可见。点击记录的“查看详情”会直接恢复对应需求及其设计流程，关闭设计窗口后返回记录弹窗。已结束且尚未被用例生成记录引用的设计记录可删除；正在处理或已经进入后续生成链的记录不能删除。删除只移除需求侧设计结果，不会删除需求、正式用例或模型调用审计。需求版本、Prompt 和 API 契约未变化时会复用成功结果；点击“重新分析”才会发起新调用。若模型、绑定或结构化输出失败，页面会明确标记“规则候选”并显示安全原因，不能把规则匹配冒充成 AI 推荐。用户不需要先猜应该添加哪些 API；如果从“API 定义”的契约详情点击“用此 API 发起测试设计”，先选择业务需求，再自动进入同一流程，当前接口会被强制保留。

   AI 测试设计和需求评审使用模型配置中的后台超时，独立于浏览器普通请求超时；内置 Demo 默认写入 180 秒，若百炼当前响应更慢，可在模型中心按实际情况提高。对百炼等 OpenAI 兼容渠道，平台会在不使用原生 `json_schema` 时把完整输出结构直接加入模型消息，并在平台侧继续执行校验和一次修复，避免模型只看到“符合 Schema”却不知道具体字段。若服务仍超过配置时间，任务会保留明确的超时说明，可以在模型中心检查响应速度后重新分析，不需要停留在弹窗中等待。

   弹窗会立即关闭，“AI 生成记录”先出现“等待执行/正在生成”，同时保留本轮确认的 API 数量和检查点计划；此时可以离开页面。稍后返回或点击“刷新”，记录应变为“生成成功”，点击“查看结果”即可看到结构化建议。生成时只会把本轮确认的 API 契约提供给模型，并校验每条建议的请求方法和 `{{base_url}}` 地址确实来自所选契约；OpenAPI 路径参数（如 `{resource_id}`）会转换为 Runner 可解析的运行时变量（如 `{{resource_id}}`）。模型还必须用 `coverage:CP-01` 形式标记每条用例实际覆盖的检查点。页面按这些标记展示最新一轮的覆盖百分比、已覆盖项和待补充项；这里衡量的是需求检查点覆盖，不是 AI 自评置信度。

   缺少请求配置、有效断言或响应内容断言的结果会进入一次输出修复，仍不合格则任务失败，不会保存半成品。审核抽屉顶部默认展示来源需求、实际模型、生成规则及处理结论；展开“生成详情”可查看需求版本、输出结构、备用模型、输出修复和生成时间；需要排障时再展开“技术追踪信息”查看或复制内部记录 ID。检查请求地址、步骤、断言与覆盖标记后点击“接受”，正式测试用例才会入库。

   业务记录列表默认每页 10 条，并可切换为 20、50 或 100 条，覆盖项目、API、数据集及版本、测试用例及版本、完整需求文档版本、AI 设计/生成/评审记录、场景、模型、Runner、报告、证据、密钥、变量、数据库连接和 Web 资产等持续增长的数据。需求树保留独立滚动，因为分页会切断父子层级；编辑器中的步骤、断言、定位器及一次性导入预览属于当前配置或临时结果，也不按业务记录分页。
4. 点击项目工作区左侧“测试用例”，无需再次筛选项目。如需语义判断，在“数据与断言”添加“AI 语义断言”，Prompt 选择 `[内置 Demo] AI 语义断言`，“判定标准”填写业务标准；保存后创建运行。
5. 在同一项目工作区进入“运行中心”投递运行。预期执行结果包含确定性断言及 AI 断言的是否通过、置信度和理由；模型调用失败或低置信度进入“待复核”，不会伪装成通过。
6. 对失败 Run，在详情中选择 `[内置 Demo] Web 失败分析`或进入“缺陷草稿”选择 `[内置 Demo] 缺陷草稿生成`。两者都必须人工审阅，V1 不会自动提交外部缺陷系统。
7. 进入当前项目的“AI 输出与审计”，无需再次选择项目；查看每次真实调用的模型、Prompt 版本、Schema、Token、耗时、备用模型切换/输出修复和脱敏结果。

这条链路的 AI 建议、人工确认、正式资产、执行结果和调用审计相互关联；AI 不会绕过人工接受、版本批准或 Run 投递门禁。

#### 推荐的功能展示组合

| 想展示的能力 | 选择的内置输入 | 现场操作 |
|---|---|---|
| 需求质量与人工决策 | “演示验收边界” | 生成 AI 评审，重点查看排序字段、性能阈值、下载体积三个待澄清项，再接受或拒绝建议 |
| 契约驱动用例 | “创建订单” | 生成 API 用例，查看请求方法、`{{base_url}}` 地址、请求体和响应内容断言，再人工接受 |
| 边界值与异常分支 | “创建订单”或“创建和查询资源” | 让 AI 覆盖数量 0、21、库存不足、空白名称、超长名称和未认证请求 |
| 数据提取与场景编排 | 登录、创建订单、查询订单、删除订单 | 将接受后的用例组合为登录 → 提取 Token → 创建 → 提取 ID → 查询 → 清理的场景 |
| 重试与超时 | “慢响应与超时”“受控重试” | 为慢接口设置响应时间断言；为幂等 GET 配置最多一次额外重试，观察 503 → 200 |
| Web 录制与自愈 | “登录页面” | 录制登录，切换目标按钮定位器，制造失败后生成分析/自愈建议，并人工审核新版本 |
| 证据、报告和缺陷 | 任一失败运行 | 查看请求/响应和截图证据，生成失败分析或缺陷草稿，并在 AI 输出页核对审计记录 |

这里的“内置”只表示需求、API 契约、Prompt 和输出结构已经准备好；表中的评审、用例、场景、运行与分析都不是预置结果。

#### 需求页和 API 页的 AI 入口如何分工

- 需求管理中的“AI 测试设计”是生成 API 测试用例的唯一主入口：先确定验收检查点，再确认完整接口闭环，最后异步生成和审核。
- API 定义详情中的“用此 API 发起测试设计”只是带上下文的快捷入口，不直接生成另一批重复用例。点击后先选择业务需求，再带着当前 API 打开同一条 AI 设计主线；当前接口作为起点强制保留，其余依赖仍由 AI 推荐并由用户确认。
- API 定义页顶部的“AI 场景编排”只负责多个接口之间的调用顺序、变量传递和清理关系，不再与单需求用例生成竞争。
- 是否完整以检查点覆盖情况为准。平台质量评分用于检查单条用例结构是否可执行，AI 自评只供参考，三者不能混为同一个百分比。

#### AI 用例质量评分怎么看

AI 用例卡片显示的是平台“质量评分”，不是用例正确率。平台按五项可验证规则计算：基础内容完整 20 分、请求配置完整 25 分、基础断言有效 20 分、响应验证充分 20 分、测试上下文完整 15 分。打开建议后可以逐项查看“通过”或“待完善”及对应依据。

“AI 自评”与平台质量评分分开显示。只有模型在原始结构化结果中明确返回 `confidence` 时才显示百分比；模型未返回时显示“未评估”，平台不会再用默认 `0.8` 冒充 80%。AI 自评仅供人工审核参考，不会自动接受建议。

当前本地 `AI_DEMO` 已于 2026-09-12 再次定向重置：AI 测试设计记录、用例生成任务、生成批次、AI 正式用例及对应调用记录均为 0，打开需求右侧“AI 用例”时应从空状态开始；项目、真实模型配置、环境、20 节点需求树、14 个 API 和 9 组内置 Prompt 均保留。旧版 4 节点需求树已在确认没有评审、关联和运行快照引用后删除，新版 20 节点树从 `REQ-0001` 连续编号。清理不会在日常模型更新时自动重复执行，避免误删你后续现场生成的结果。

---

## 4. 确定性保底线：手工创建演示项目和环境（可选）

只有模型服务临时不可用，或需要单独讲解平台基础资产时才执行第 4～10 节。已经完成第 3.5 节后，不要再用相同编码手工重复创建项目或环境。

### 4.1 创建项目

操作路径：

```text
左侧导航 → 项目门户 → 创建项目
```

填写：

| 字段 | 建议值 |
|---|---|
| 项目名称 | `内置 Demo 演示` |
| 项目编码 | `DEMO_V1` |
| 描述 | `使用本机 8765 Demo 验证 V1 API、Web、Evidence 与报告闭环` |

点击“创建项目”。

预期结果：列表出现“内置 Demo 演示”，状态为有效；当前管理员成为项目 Owner。

### 4.2 创建环境

在项目行进入“设置”，打开“环境”页签，点击“创建环境”。

填写：

| 字段 | 值 |
|---|---|
| 环境名称 | `本机 Demo` |
| 环境编码 | `DEMO_LOCAL` |
| Base URL | `http://127.0.0.1:8765` |
| 描述 | `本机回环 Demo，不连接外部系统` |

保存后，确认环境为“启用”；若页面提供“设为默认”，将其设为默认环境。

预期结果：环境列表显示 Base URL 为 `http://127.0.0.1:8765`。

### 4.3 保存 Demo 密码 Secret

进入项目工作区的“运行配置”，选择刚创建的环境，打开“密钥”页签，点击“添加密钥”。

填写：

| 字段 | 值 |
|---|---|
| Secret 名称 | `DEMO_PASSWORD` |
| 类型 | `PASSWORD` |
| Secret 值 | `demo-pass` |

点击“加密保存”。

预期结果：列表只显示 Secret 名称、类型和安全元数据，不回显明文。

后续 Web Case 使用 `{{secret.DEMO_PASSWORD}}`，不要把密码明文写进 Case、日志或截图。

---

## 5. 注册并启动 Windows Runner

当前开发库是空白基线，旧 Runner 身份即使还留在本机，也不能假定有效。

### 5.1 创建一次性 Registration Token

操作路径：

```text
左侧导航 → Runner 中心 → 创建 Registration Token
```

有效期可留空使用默认值，或填写 `600` 秒。点击“创建并显示 Token”。

预期结果：页面只显示一次 Token 明文。先不要关闭结果窗口。

### 5.2 交互式注册

在项目根目录终端执行：

```powershell
cd runner
..\.venv\Scripts\python.exe -m runner.main register --backend-url http://127.0.0.1:8000 --name windows-runner --tag windows --api-slots 1 --web-slots 1
```

命令提示时粘贴 Token。不要把 Token 写进命令参数、脚本或文档。

注册成功后执行：

```powershell
..\.venv\Scripts\python.exe -m runner.main heartbeat-once
```

预期结果：心跳返回 `ACTIVE`。

### 5.3 启动 Worker

优先使用 PyCharm 的 `Runner Worker` 运行配置。也可以在 `runner` 目录手工执行：

```powershell
$env:AI_TEST_RABBITMQ_URL = "amqp://test_platform:test_platform@127.0.0.1:5672/"
..\.venv\Scripts\python.exe -m runner.main worker --web-slots 1
```

保持 Worker 前台运行。回到“Runner 中心”，等待自动刷新。

预期结果：

- Runner 状态为 `ACTIVE`；
- 在线状态为 `ONLINE`；
- API 与 WEB Capability 为 `READY`；
- API、WEB Slot 至少各有 1 个可用。

若显示 `UNKNOWN`，优先检查 Redis；若没有 Runner 可选，检查 Worker 是否正在运行、RabbitMQ 是否健康以及 Capability/Slot 是否满足要求。

---

## 6. 导入需求、OpenAPI 和数据集

这三步用于准备可追溯的演示资产，不会调用外部模型。

### 6.1 导入 Requirement Tree

操作路径：

```text
项目门户 → 进入“内置 Demo 演示” → 需求管理 → 导入 Markdown
```

选择文件：

```text
demo/fixtures/资源管理需求.md
```

页面先显示 Markdown 解析预览。确认能看到“演示资源管理需求 V1”“登录与会话”“资源管理”等节点，然后点击“确认导入”。

预期结果：

- 页面提示已导入需求节点；
- 左侧形成 Requirement Tree；
- 选择节点可查看 Markdown 内容和版本历史。

### 6.2 导入 OpenAPI

操作路径：

```text
项目门户 → 进入“内置 Demo 演示” → API 定义 → 导入 OpenAPI
```

选择文件：

```text
demo/fixtures/openapi.json
```

检查预览后点击“确认导入”。

预期结果：导入版本中出现 14 个操作，至少包括：

- `POST /api/login`；
- `GET/POST /api/resources`；
- `DELETE /api/resources/{id}`；
- 商品、订单、下载、慢请求和首次失败重试接口。

再次导入同一文件时，页面应通过差异摘要显示新增、变更、未变化和移除数量，而不是静默覆盖旧版本。

### 6.3 导入 CSV 数据集

操作路径：

```text
项目门户 → 进入“内置 Demo 演示” → 数据集 → 新建数据集
```

填写：

| 字段 | 值 |
|---|---|
| 数据集名称 | `Demo 资源数据` |
| 类型 | `CSV` |
| 文件 | `demo/fixtures/resources.csv` |
| 分隔符 | 逗号 |

点击“预览数据”，确认显示 5 行及 `row_key`、`name`、`expected_status` 三列，再点击“确认保存快照”。

预期结果：数据集状态为有效；详情中可以查看固定版本和 Iteration Context 预览。

本步骤只建立数据资产。把它绑定到创建资源的 API Case 属于进阶数据驱动演示，必须同时配置认证、逐行断言和精确 Cleanup，不能只批量创建而不清理。

---

## 7. 确定性 API 闭环

先创建一个不需要认证、没有外部副作用的健康检查 Case。这是现场演示的保底 Case。

### 7.1 创建 API Case

操作路径：

```text
项目门户 → 进入“内置 Demo 演示” → 测试用例 → 新建用例
```

弹窗采用左侧步骤菜单。可以按“下一步”顺序填写，也可以直接点击左侧菜单切换；切换步骤不会丢失已输入内容。

#### 步骤 1：基础信息

| 字段 | 值 |
|---|---|
| 用例名称 | `Demo 健康检查` |
| 类型 | `API` |
| 优先级 | `P0` |
| 置信度 | `1` |
| 前置条件 | `本机 Demo 正在监听 8765` |
| 测试数据 JSON | `{}` |

“步骤 JSON”填写：

```json
[
  {
    "order": 1,
    "action": "调用 Demo 健康检查",
    "expected": "HTTP 200 且服务标识正确"
  }
]
```

#### 步骤 2：请求配置

点击左侧“请求配置”，填写 API 请求模板：

| 字段 | 值 |
|---|---|
| 方法 | `GET` |
| URL | `{{base_url}}/health` |
| Query | `[]` |
| Headers | `[]` |
| Cookies | `[]` |
| Body | `NONE` |
| 认证 | `NONE` |
| 超时 | `30000` 毫秒 |
| 最大额外重试次数 | `0` |

#### 步骤 3：动作与清理

本健康检查没有外部副作用，`Pre Actions`、`Post Actions` 和 `Cleanup` 均保持为空。

#### 步骤 4：数据与断言

点击左侧“数据与断言”。数据集保持“不绑定数据集”，然后在 Assertions 区点击“添加断言”，配置两条确定性断言：

| 类型 | 名称 | 表达式 | Expected |
|---|---|---|---|
| Status Code | `status_200` | 无 | `200` |
| JSONPath Equal | `service_name` | `$.service` | `v1-demo` |

Expected 输入框会即时同步，不需要先点击空白处。字符串可以直接输入；数字、布尔值、对象和数组也可以填写标准 JSON。

#### 步骤 5：结果与说明

点击左侧“结果与说明”，填写：

| 字段 | 值 |
|---|---|
| 预期结果 | `HTTP 200，响应 service 为 v1-demo` |
| 标签 | `demo,smoke,api` |
| 创建说明 | `内置 Demo 确定性保底用例` |

点击“创建 V1”。

预期结果：测试用例列表出现“Demo 健康检查”，状态为 `ACTIVE`，详情中可查看 V1 固定内容。

### 7.2 创建并投递 Run

操作路径：

```text
当前项目工作区 → 运行中心
```

依次选择：

| 字段 | 选择 |
|---|---|
| 当前项目 | `内置 Demo 演示` |
| 运行环境 | `本机 Demo` |
| 运行类型 | `API_CASE` |
| 测试用例 | `Demo 健康检查` |
| 执行 Runner | `windows-runner` |

点击“校验并创建”。

预期结果：先显示“运行校验通过，可以创建”，然后生成状态为 `CREATED` 的 Run。

创建不等于执行。继续点击成功提示中的“投递”。

预期状态顺序通常为：

```text
CREATED → QUEUED → ASSIGNED → RUNNING → SUCCESS
```

不要因为页面几秒没有刷新就重复投递。页面优先使用实时推送，异常时会降级为 8 秒轮询。

### 7.3 检查 Run 详情

在运行列表打开刚才的 Run，至少检查：

- Run 状态为 `SUCCESS`；
- CaseRun 和 StepRun 为成功；
- 实际 HTTP 状态为 200；
- 两条断言均通过；
- 固定 Case Version、Environment 和 Runner 信息正确；
- 请求或日志中没有 Secret 明文。

### 7.4 可选：演示 Step Retry

复制健康检查 Case 创建新版本或新建 Case：

```text
GET {{base_url}}/api/flaky?key=demo-retry-001
```

配置：

- 最大额外重试次数：`1`；
- 重试间隔：`100` 毫秒；
- 重试条件：`HTTP_5XX`；
- Status Code 断言：`200`。

同一个 `key` 第一次返回 503，第二次返回 200。预期 Run 最终成功，并记录一次额外尝试。重复演示时更换 `key`，或重启 Demo 清空进程内计数。

---

## 8. 确定性 Web 闭环

为了保证 Locator 失败可以被稳定复现，本节先手工创建一个固定使用旧按钮 ID 的 Web Case。

### 8.1 确认 Locator 为初始状态

在项目根目录的 PowerShell 执行：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/control/locator" -ContentType "application/json" -Body '{"changed":false}'
Invoke-RestMethod -Uri "http://127.0.0.1:8765/control/state"
```

预期结果：`changed_locator` 为 `false`。登录按钮 ID 为 `#login-btn`。

### 8.2 创建 Web Case

操作路径：

```text
项目门户 → 进入“内置 Demo 演示” → Web 自动化 → Web 用例 → 新建
```

基本字段：

| 字段 | 值 |
|---|---|
| 名称 | `Demo 登录成功` |
| 起始 URL | `{{base_url}}/login` |
| 变更说明 | `创建稳定登录演示用例` |
| 总超时 | `60000` 毫秒 |
| 浏览器 | Chrome |
| 运行模式 | Headless；现场想观察浏览器时可改为“有界面” |
| 窗口 | `1280 × 720` |
| Session Profile | 不使用 |

依次点击“添加 Action”，配置：

| 顺序 | Action | 主要字段 |
|---:|---|---|
| 1 | `GOTO` | URL：`{{base_url}}/login` |
| 2 | `FILL` | 直接 Locator；strategy=`test_id`；value=`username`；输入值=`demo` |
| 3 | `FILL` | 直接 Locator；strategy=`test_id`；value=`password`；输入值=`{{secret.DEMO_PASSWORD}}` |
| 4 | `CLICK` | 直接 Locator；strategy=`css`；value=`#login-btn` |
| 5 | `WAIT_URL` | URL：`{{base_url}}/app` |

每项超时可保留 `30000` 毫秒，失败策略选择“失败停止”。

依次点击“添加 Assertion”，配置：

| Assertion | Locator / Expected |
|---|---|
| `ASSERT_URL` | Expected：`{{base_url}}/app` |
| `ASSERT_VISIBLE` | strategy=`test_id`，value=`welcome` |
| `ASSERT_TEXT` | strategy=`test_id`，value=`welcome`，Expected=`登录成功` |

点击“创建 V1”。创建完成后，检查内容，再点击“批准执行”。

预期结果：Web Case 状态从 `DRAFT` 变为 `APPROVED`。只有已批准版本才会出现在运行中心。

### 8.3 执行 Web Case

进入“运行中心”，选择：

| 字段 | 选择 |
|---|---|
| 当前项目 | `内置 Demo 演示` |
| 运行环境 | `本机 Demo` |
| 运行类型 | `WEB_CASE` |
| Web Case | `Demo 登录成功` |
| 版本 | 刚批准的版本 |
| Runner | `windows-runner` |

点击“校验并创建”，再点击“投递”。

预期结果：Run 最终为 `SUCCESS`；详情中可看到 GOTO、FILL、CLICK、WAIT_URL 和断言结果，并产生 Web Evidence 元数据。

### 8.4 查看 Evidence 和报告

Evidence 路径：

```text
当前项目工作区 → 证据中心 → 输入运行 ID → 应用过滤
```

预期结果：能看到属于该 Run 的受保护 Evidence 元数据；点击“下载”会经过后端鉴权，不会暴露 MinIO Bucket 或对象路径。

报告路径：

```text
当前项目工作区 → 测试报告 → 找到运行 → 查看报告
```

至少检查：

- Run、CaseRun、StepRun 状态与耗时；
- 固定 Web Case Version 和 Runner；
- Action、Assertion 和 Evidence；
- 错误字段为空或明确显示未记录；
- “下载完整 Markdown”和“下载完整 HTML”均可用。

---

## 9. 制造失败并演示版本修复

### 9.1 切换 Demo Locator

在 PowerShell 执行：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/control/locator" -ContentType "application/json" -Body '{"changed":true}'
Invoke-RestMethod -Uri "http://127.0.0.1:8765/control/state"
```

预期结果：`changed_locator` 为 `true`。按钮 ID 从 `#login-btn` 变为 `#signin-confirm`，但按钮文字和 `aria-label=登录` 保持不变。

### 9.2 重跑旧版本

回到运行中心，仍选择之前批准的旧 Web Case Version，重新“校验并创建”并投递。

预期结果：

- Run 最终为 `FAILED`；
- 失败节点是使用 `#login-btn` 的 CLICK；
- 错误类型属于 Locator 未找到或对应超时；
- 旧版本和第一次成功 Run 仍然保留，没有被覆盖。

如果旧版本仍成功，先确认本次 Run 固定引用的确实是旧版本，并检查其 CLICK 是否使用 `#login-btn`，而不是 `test_id`、role 或 Element 多候选。

### 9.3 无 AI 时手工修复

操作路径：

```text
Web 自动化资产 → Web Case → Demo 登录成功 → 选择旧版本
```

把 CLICK 的 CSS Locator 从：

```text
#login-btn
```

改为更稳定的：

```text
[aria-label="登录"]
```

变更说明填写：

```text
登录按钮 ID 变化，改用稳定语义 Locator
```

点击“保存新版本”，检查后点击“批准执行”。回到运行中心选择新批准版本，创建并投递新 Run。

预期结果：新 Run 为 `SUCCESS`；旧失败 Run、旧 Locator 和旧版本仍可审计。这就是不依赖模型的“失败 → 新版本 → 重新执行”闭环。

---

## 10. 可选：使用录制工作台生成 Web Case

前置条件：Runner 为 `ONLINE`，WEB Capability 为 `READY`，至少有一个可用 Web Slot。

操作路径：

```text
Web 自动化资产 → 录制工作台
```

填写：

| 字段 | 值 |
|---|---|
| WEB Runner | `windows-runner` |
| 起始 URL | `http://127.0.0.1:8765/login` |
| Environment | `本机 Demo` |
| Session Profile | 不使用 |
| 录制完成后保存新 Session Profile | 首次演示可不勾选 |

点击“创建并投递录制”。在 Runner 打开的受控浏览器中：

1. 用户名输入 `demo`；
2. 密码输入 `demo-pass`；
3. 点击“登录”；
4. 等待进入“资源工作台”；
5. 返回平台录制详情，点击“请求停止”。

预期结果：录制状态进入终态，详情显示经过后端脱敏的事件草稿，不显示 Cookie、Token、原始 DOM 或密码明文。

检查并删除无关事件后，点击“确认生成 Web Case”，填写名称 `Demo 录制登录`。

预期结果：生成 DRAFT Web Case。转到“Web Case”页签检查 Action 和 Locator，点击“批准执行”后再到运行中心执行。

录制生成草稿不需要外部模型；“AI 整理建议”才需要 `WEB_CASE_GENERATE` Prompt、Output Schema 和模型绑定。

---

## 11. AI 主线的高级扩展：Swagger、录制与自愈

### 11.1 AI 前置条件

完成第 3.5 节后，需求评审、API 用例生成、API 场景编排、Web 录制转用例、AI 断言、定位器自愈、Web 失败分析和缺陷草稿已经自动配置，无需再到模型中心、Prompt 中心逐项创建。先在“AI 主演示”确认初始化清单全部完成，再执行一次需求评审验证真实调用和审计。

API 场景建议、录制事件 AI 整理和定位器自愈所需的 Prompt、Output Schema 与项目绑定也已内置；初始化器只完成配置，不会替演示者生成结果或接受建议。演示时直接在对应页面选择 `[内置 Demo]` Prompt 即可。

本演示可能使用的任务类型：

| 功能 | 任务类型 | 初始化方式 |
|---|---|---|
| Requirement AI 评审 | `REQUIREMENT_REVIEW` | 自动 |
| Requirement/API Case 建议 | `API_CASE_GENERATE` | 自动 |
| API/Web AI 断言 | `AI_ASSERTION` | 自动 |
| Web 失败分析 | `WEB_FAILURE_ANALYSIS` | 自动 |
| 缺陷草稿 | `DEFECT_DRAFT` | 自动 |
| Swagger Scenario 建议 | `API_SCENARIO_GENERATE` | 高级，手工补充 |
| 录制事件 AI 整理 | `WEB_CASE_GENERATE` | 高级，手工补充 |
| Locator 自愈 | `LOCATOR_HEALING` | 高级，手工补充 |

真实 AI 调用会消耗额度并写入审计。需求“AI 用例”采用持久任务记录：提交后页面不再等待模型响应，可以离开后稍后查看；同一需求已有“等待执行/正在生成”任务时不能重复创建。其他尚未切换为任务记录的 AI 功能如超过页面等待时间，应先刷新历史，不要连续重复点击生成。

### 11.2 Requirement 或 Swagger AI 建议

Requirement 路径：

```text
需求管理 → 选择固定需求版本 → AI 评审 / AI 用例
```

Swagger 路径：

```text
API 定义 → 选择刚导入的固定版本 → AI Case / Scenario
```

生成后必须人工检查、编辑并选择“接受”或“拒绝”。接受只生成 API Case 或 DRAFT Scenario，不会自动批准、投递或执行。

预期结果：页面保留 Prompt Version、Output Schema、实际模型、Fallback/Repair 和人工决策审计。

### 11.3 Locator Healing 完整流程

使用第 9.2 节产生的 Locator 失败 Run：

1. 打开 Run 详情；
2. 选择失败的 CaseRun 和 CLICK Trace；
3. 展开 Locator Healing 区域；
4. 选择 `LOCATOR_HEALING` Prompt；
5. 如所选阶段需要截图，选择该 Run 已有的 Screenshot Evidence；
6. 点击“生成 Healing 提案”；
7. 在候选 Locator 中选择语义稳定的候选；
8. 点击“验证所选候选”；
9. 等待临时验证 Run 为 `SUCCESS`；
10. 点击“接受并创建 DRAFT”；
11. 点击“前往审核新版本”；
12. 在 Web Case 页检查新版本并“批准执行”；
13. 回到运行中心，明确选择新批准版本，创建并投递新 Run。

预期结果：

- 提案先处于待人工审核状态；
- 未通过验证 Run 的候选不能被接受；
- 接受后只创建 DRAFT，不自动批准或重跑；
- 新 Run 成功；
- 原失败 Run、旧版本、候选、验证 Run、人工决定和 AI Call 均可追溯。

### 11.4 Web 失败分析与缺陷草稿

在失败 Run 详情中进入“AI Web 失败分析”，选择匹配 Prompt 后生成分析。完成后检查：

- 分析关联的是正确 Run、CaseRun 和失败节点；
- 页面只展示安全摘要；
- AI Call、Prompt Version、Schema、模型和 Repair/Fallback 可追溯。

如已配置 `DEFECT_DRAFT`，进入“缺陷草稿”，选择失败 Run 和 Prompt，生成草稿后人工编辑，再复制或导出 Markdown。V1 不会自动提交到外部缺陷系统。

---

## 12. 演示结束后的精确恢复

### 12.1 恢复 Demo Locator

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/control/locator" -ContentType "application/json" -Body '{"changed":false}'
```

### 12.2 查看 Demo 进程内状态

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8765/control/state"
```

该接口只返回资源数、订单数、登录次数和 Locator 模式，不返回凭据。

如果演示创建了资源或订单，应使用响应中的精确 ID 调用对应 DELETE 接口，或在 Demo 页面逐项删除。不要写批量删除脚本猜测 ID。

关闭 Demo 进程会清空它的内存资源、订单、Session 和重试计数，但不会删除平台中的项目、Case、Run、Evidence 或审计记录。

### 12.3 停止进程

- Demo：在 Demo 终端按 `Ctrl+C`；
- Runner：在 Worker 终端按 `Ctrl+C`，等待安全退出；
- 前后端：使用各自 PyCharm Stop；
- 中间件如需停止：执行 `docker compose -f .\deploy\docker-compose.dev.yml down`。

不要使用 `down -v`。不要删除 `.local-backups/`，也不要手写 SQL 删除平台历史 Run。

---

## 13. 常见问题排查

### 13.1 `127.0.0.1:8765` 拒绝连接

原因：Demo 进程没有启动或已经退出。

处理：

```powershell
.\.venv\Scripts\python.exe -m demo.server --port 8765
```

保持终端运行，再访问 `/health`。若端口被占用，不要让 Demo 随意换端口后继续使用旧环境；先查明占用者，或同步修改环境 Base URL。

### 13.2 平台可以打开，但登录失败

依次检查：

1. `http://127.0.0.1:8000/health`；
2. Alembic `current` 是否为 `20260912_0055`；
3. 浏览器网络面板中的 401 或代理错误；
4. 当前管理员密码是否已经从 `admin123` 修改。

### 13.3 Runner 中心没有可用 Runner

检查：

- Token 是否已经被正确消费；
- `heartbeat-once` 是否返回 ACTIVE；
- Worker 是否保持运行；
- Redis 和 RabbitMQ 是否 healthy；
- Runner 是否 ACTIVE、ONLINE；
- 对应 API/WEB Capability 是否 READY；
- 对应 Slot 是否至少有 1 个 available。

数据库重建后，旧 credential 会失效，应创建新 Token 重新注册，不要尝试恢复或打印旧 credential。

### 13.4 Run 一直是 CREATED

创建和投递是两个操作。点击新 Run 提示或运行列表中的“投递”。

### 13.5 Run 一直是 QUEUED

检查 Worker、RabbitMQ、Runner 在线状态、Capability、Tag 和 Slot。不要重复创建同一个 Run 来掩盖队列问题。

### 13.6 API Case 校验失败

重点核对：

- Environment 是否启用；
- URL 是否使用 `{{base_url}}` 或合法的 `http(s)` 地址；
- API Case 是否为 ACTIVE；
- JSON 编辑框是否是合法 JSON；
- Retry 是否严格为 0 或 1；
- Secret 是否只通过 `{{secret.NAME}}` 引用；
- Runner API Capability 和 Slot 是否可用。

### 13.7 Web Case 不出现在运行中心

只有 `APPROVED` 且存在批准版本的 Web Case 才能执行。录制或 Healing 生成的结果都是 DRAFT，必须先到 Web Case 页检查并批准。

### 13.8 Web 登录 Case 第一次就失败

检查：

1. Demo `/control/state` 的 `changed_locator` 是否为 `false`；
2. CLICK 是否使用 `css` + `#login-btn`；
3. 密码是否使用 `{{secret.DEMO_PASSWORD}}`；
4. Secret 是否属于当前项目和环境且为启用状态；
5. Runner 是否安装可用 Chrome/Playwright；
6. Demo、Runner 是否在同一台 Windows 主机。

### 13.9 Locator 切换后仍然成功

Case 可能使用了稳定的 `test_id`、role、`aria-label` 或多候选 Element，而不是旧 `#login-btn`。为了演示确定性失败，旧批准版本的 CLICK 必须只使用 CSS `#login-btn`。

### 13.10 Evidence 有记录但不能下载

检查当前用户是否有项目权限、MinIO 是否 healthy、对象是否存在，以及后端下载接口日志。不要把 Bucket 改成公开，也不要绕过平台直接读取对象目录。

### 13.11 AI 按钮不可用或生成失败

先阅读页面显示的具体错误，不再把所有异常都归并为“检查模型绑定”。然后回到“AI 主演示”刷新状态；再检查模型渠道的加密 API Key、模型启用状态、Prompt 任务类型、Output Schema、项目模型绑定和外部网络。

需求“AI 用例”点击后只创建任务记录，不再让浏览器等待真实模型响应。“等待执行”和“正在生成”会每 3 秒自动刷新，也可手工点击“刷新”；任务完成后点击“查看结果”。任务失败会在记录中保留经过脱敏的原因，可修正配置后重新创建。其他真实 AI 生成请求暂使用 180 秒等待上限，模型连通性验证最多等待 75 秒，普通页面请求仍保持 15 秒故障上限。

“模型连接验证成功”只代表基础文本请求成功。业务生成还会携带 Prompt，并执行平台侧 Output Schema 校验。若兼容服务拒绝原生 `json_schema` 参数，平台会自动去掉该参数重试一次，生成结果仍必须通过相同 Schema；不会把非结构化结果直接保存为建议。AI 主演示新建或更新保留名模型时默认使用这种兼容模式。

若自动兼容后仍失败，可先看“AI 生成记录”的失败原因，再到当前项目“AI 输出与审计”查看错误类型：HTTP 400 通常需要核对模型名或服务商限制；后端模型超时需要适当调高模型配置中的“超时（秒）”；`AI_STRUCTURED_OUTPUT_INVALID` 表示模型经一次修复后仍不符合 Schema。

部分 OpenAI 兼容模型会把平台的 `JSONPATH_EQUAL` 输出为 `json_path`。平台只对语义明确的别名做规范化：`EQ` 映射为 `JSONPATH_EQUAL`，`CONTAINS` 映射为 `CONTAINS + source=JSONPATH`；随后仍执行完整正式用例领域校验。其他未知断言类型不会自动猜测或落库。

若任务生成成功但质量评分仍提示“请求配置完整：待完善”，先确认查看的是不是修复前的历史记录。历史结果保持不可变；重新生成的新建议才会携带当前项目的 API 契约。新任务如果无法生成合法请求，会直接记录为失败并给出脱敏原因，而不会再以缺少 `request` 的建议成功落库。项目确实没有导入 API 定义时，应先进入“API 定义”导入 OpenAPI，再生成 API 用例。

---

## 14. 现场演示建议

### 14.1 12 分钟完整版

| 时间 | 内容 |
|---:|---|
| 1 分钟 | 展示架构与本机 Demo 边界 |
| 2 分钟 | Requirement/OpenAPI 固定版本导入 |
| 2 分钟 | API Case 校验、创建、投递与实时状态 |
| 2 分钟 | Web Case 执行和 Evidence |
| 2 分钟 | 切换 Locator，展示旧版本失败 |
| 2 分钟 | Healing 或手工新版本修复并重跑 |
| 1 分钟 | 报告导出、Dashboard 和审计 |

### 14.2 5 分钟保底版

提前准备项目、环境、Runner 和批准版本，现场只演示：

```text
API Run 成功
→ Web Run 成功
→ Locator 切换后旧版本失败
→ 新批准版本成功
→ 查看 Evidence 和报告
```

### 14.3 演示前最后检查

- [ ] Demo `/health` 返回成功；
- [ ] `changed_locator=false`；
- [ ] 后端、前端、Redis、RabbitMQ、MinIO 正常；
- [ ] Runner ACTIVE、ONLINE、API/WEB READY、Slot 可用；
- [ ] 保底 API Case 已验证；
- [ ] Web Case 的旧、新版本均明确可选；
- [ ] 需要 AI 时已完成一次最小真实调用；
- [ ] 不在屏幕上显示 Token、API Key、Cookie、数据库密码或 Runner credential；
- [ ] 不把“预览成功”说成“真实 Runner 执行成功”；
- [ ] 不修改或删除历史 Run 来美化演示结果。
