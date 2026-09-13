# AI 原生智能测试平台——产品需求、系统架构与开发规格说明书

> 文档性质：项目总规格 / PRD + 系统架构 + 开发边界 + 版本计划  
> 目标读者：项目开发者、Codex / Claude Code 等编码助手、面试讲解使用者  
> 当前状态：功能范围已锁定  
> 技术主栈：FastAPI + Vue 3 + MySQL + Redis + RabbitMQ + MinIO + Windows Runner  
> 核心目标：用于求职展示测试开发能力，突出测试平台工程化、自动化测试、AI 原生能力、Runner 调度与可追溯性  
> 当前明确不做：App 自动化

---

## 0. 文档使用方式

本文件是整个项目的**唯一总规格基线**。后续开发应遵循以下规则：

1. 先实现本文件中定义的功能，再考虑扩展。
2. 编码助手不得因为“容易实现”而自行增加未定义模块。
3. 如果后续讨论修改了功能，应先更新本文件，再改代码。
4. 所有数据库模型、API、前端页面、Runner 协议、AI Prompt、任务状态机都应能追溯到本文件中的需求。
5. V1、V2 的边界必须严格执行，避免同时开工过多模块。
6. 所有“AI 自动修改正式资产”的行为默认禁止，AI 应提供建议，正式修改需人工确认。
7. 所有自动化执行都遵循“**AI 负责理解、生成、规划、分析；程序负责确定性执行**”原则。

---

# 1. 项目定位

## 1.1 一句话定位

> **面向需求到执行全过程的 AI 原生智能测试平台，将需求/接口资料、测试资产、自动化执行、AI 分析、执行证据与人工反馈连接成完整测试闭环。**

平台不是单纯的：
- AI 测试用例生成器；
- pytest/Playwright 脚本管理器；
- Postman/JMeter 的 Web 包装；
- 大模型聊天助手。

平台应体现：

```text
需求 / Swagger
      ↓
结构化解析
      ↓
AI 评审
      ↓
AI 生成 Case
      ↓
人工审核
      ↓
正式测试资产
      ↓
API / Web 自动化
      ↓
Runner 确定性执行
      ↓
规则断言 + AI 语义断言
      ↓
截图 / Trace / 请求响应 / 日志 / 变量
      ↓
AI 失败分析
      ↓
统一报告
      ↓
人工复核
      ↓
反馈数据沉淀
```

## 1.2 求职展示目标

项目首先服务于测试开发岗位求职，因此架构设计应能清晰体现以下能力：

### 测试能力
- 测试用例设计；
- API 自动化；
- Web UI 自动化；
- 数据驱动；
- 接口关联；
- 测试数据清理；
- 性能测试；
- SSE/LLM 流式接口性能测试；
- 断言体系；
- 报告和缺陷分析。

### 测试开发能力
- 测试平台设计；
- DSL 与执行器设计；
- Runner/Worker 设计；
- 队列与任务状态机；
- 多执行器统一抽象；
- 实时执行日志；
- 元素库、Locator、自愈；
- CI/CD 集成；
- 调试能力；
- 数据生命周期管理。

### 后端工程能力
- FastAPI 分层；
- SQLAlchemy 2.x；
- Alembic；
- RabbitMQ；
- Redis；
- MySQL；
- MinIO；
- WebSocket/SSE；
- RBAC；
- 审计；
- Secret 管理；
- Docker Compose。

### AI 工程能力
- Model Gateway；
- Prompt Center；
- Prompt 版本化；
- JSON Schema 结构化输出；
- Model fallback；
- Agent + MCP；
- Vision；
- RAG；
- AI Evaluation；
- Token/费用/延迟可观测性；
- 人工反馈闭环。

---

# 2. 项目来源与融合思路

## 2.1 现有 AI 智能体测试平台原型中应保留的核心思想

现有原型最值得继承的不是 Flask 代码，而是业务闭环与领域经验：

- 需求文档结构化；
- API 文档结构化；
- AI 需求评审；
- AI 接口文档评审；
- UI/API 测试用例生成；
- 测试数据生成；
- 参数化；
- Playwright MCP 智能执行；
- API 执行与 AI 断言；
- 执行任务、执行记录与证据；
- 性能结果 AI 分析；
- 流式 AI 交互；
- 平台级接入渠道与模型配置、项目级任务模型绑定；
- 提示词按任务分类；
- AI 输出结构化；
- 需求 → 用例 → 执行 → 判断 → 证据的闭环。

## 2.2 BrickCore 中值得吸收的工程思想

本项目借鉴而不直接依赖 BrickCore 的以下方向：

- FastAPI + Vue3 的平台形态；
- Web/API/性能统一入口；
- Web 录制回放；
- Locator 自愈；
- Swagger 导入；
- SSE/流式性能测试；
- Runner 执行模式；
- RabbitMQ / Redis / MinIO；
- RBAC；
- Test Plan；
- Dashboard；
- 报告；
- 通知。

**不直接依赖 BrickCoreRunner。** 本项目自行实现开放 Runner，避免执行核心受第三方闭源运行时限制。

## 2.3 融合后的核心差异

本项目不是简单复制 BrickCore，而是在其工程化思路上进一步突出：

1. Requirement → Case → Scenario → Run → Evidence 的测试资产链路；
2. AI 生成结果必须人工审核；
3. 确定性执行优先，Agent 只在复杂智能场景介入；
4. API 测试重点做“场景编排 + 变量传递 + Cleanup”；
5. Web 测试重点做“录制 + 结构化步骤 + 高级自愈 + Trace”；
6. 性能重点做“功能场景复用 + LLM/SSE 指标”；
7. AI 模型、Prompt、调用记录、评测独立成平台基础设施；
8. 所有执行均保存完整证据和版本信息；
9. 后续构建人工反馈 → AI 评测 → Prompt/模型优化的数据闭环。

---

# 3. 设计原则

## 3.1 AI 与确定性程序分工

### AI 适合负责
- 需求理解；
- 需求评审；
- 用例生成；
- Swagger 依赖关系推荐；
- 自然语言步骤结构化；
- Web 录制结果整理；
- Locator 智能修复；
- AI 语义断言；
- 失败归因；
- 性能分析；
- 缺陷草稿；
- 需求变更影响建议；
- Agent 智能执行。

### 程序必须负责
- HTTP 请求；
- JSONPath 提取；
- Header/Cookie 提取；
- 环境变量替换；
- IF/LOOP；
- SQL；
- 清理；
- 状态码断言；
- JSON Schema；
- Playwright 操作；
- Retry；
- Timeout；
- RabbitMQ 调度；
- Runner Slot；
- 任务状态；
- Evidence 存储。

禁止让 LLM 每一步临时猜测 Token 应该放在哪里、接口应如何串联。

## 3.2 正式资产人工确认原则

下列 AI 行为不得直接永久修改正式资产：

- AI 生成测试用例；
- AI 修改用例；
- AI 推荐 Scenario；
- AI 修复 Locator；
- AI 影响分析后的 Case 修改；
- AI 缺陷判断。

流程统一为：

```text
AI 建议
  ↓
预览差异
  ↓
人工 Accept / Reject / Edit
  ↓
生成正式版本
```

## 3.3 前端封装复杂测试开发操作

本平台的价值之一是把代码级操作封装为前端配置：

- 变量；
- 数据提取；
- IF；
- LOOP；
- WAIT；
- HTTP；
- SQL；
- Python Script；
- Cleanup；
- 断言；
- Token Pool；
- 性能压力模型。

普通用户无需编写 Python 测试框架代码；高级用户可以使用 Script/SQL 节点补充平台无法表达的逻辑。

## 3.4 可追溯性优先

任何一次执行应尽可能回答：

- 执行的是哪个 Case 版本？
- 来源于哪个 Requirement 版本？
- 使用哪个环境？
- 使用什么参数？
- 使用哪个 Runner？
- 使用哪个 Executor 版本？
- AI 使用哪个模型？
- 使用哪个 Prompt 版本？
- AI 原始输出是什么？
- 哪一步失败？
- 当时的截图、请求响应、日志是什么？
- AI 给了什么判断？
- 人工是否复核？

---

# 4. 范围与版本边界

## 4.1 V1：核心可演示版本

目标：完整跑通“资料 → AI → Case → API/Web → Runner → Evidence → Report”。

### V1 必做
- 登录与基础 RBAC；
- 项目管理；
- 环境、变量、Secret；
- Markdown 导入；
- Swagger/OpenAPI 导入；
- Requirement Tree 与 Requirement Version；
- API Definition；
- API Case；
- API Scenario；
- Scenario 列表编辑器；
- IF / LOOP / WAIT；
- Pre/Post Action；
- Python Script；
- MySQL SQL Node；
- 数据驱动；
- 变量提取与接口关联；
- API/SQL Cleanup；
- Resource Registry；
- 规则断言；
- AI 断言；
- Model Center；
- Prompt Center；
- Prompt Version；
- AI 调用日志；
- Token/成本；
- Model fallback；
- Web Case；
- 元素库；
- 多 Locator；
- Chrome；
- Playwright；
- Web 录制；
- AI 整理录制结果；
- 登录态复用；
- 高级 Locator 自愈链路；
- 截图、Trace、日志；
- AI 失败分析；
- Windows Runner；
- Runner Token/注册/心跳/Tag/Slot；
- RabbitMQ；
- Redis 实时状态；
- MinIO；
- Cancel / Force Stop；
- Timeout；
- Step Retry；
- 实时执行控制台；
- 统一报告；
- 简版 Dashboard；
- 项目归档；
- Case 版本数据结构；
- Requirement 关联 Case 的确定性影响范围。

### V1 不做
- 完整独立 Web Agent 测试模式；
- Flow 图 Scenario 编辑器；
- RAG；
- AI Evaluation Center；
- App 自动化；
- Linux Runner；
- 分布式压测；
- Scenario 调 Scenario；
- Runner 断点恢复/跨 Runner Failover；
- 移动 Web 设备模拟；
- Firefox/WebKit/Edge 多浏览器适配。

## 4.2 V1：测试开发增强能力（合并范围）

- 性能测试；
- JMeter；
- Python SSE/LLM Executor；
- API Scenario 转性能场景；
- TTFT / Tokens/s / Chunk 指标；
- SLA；
- Warm Up；
- Run 对比；
- AI 性能分析；
- Prometheus 接入；
- Test Plan；
- 定时任务；
- Webhook；
- CI/CD API；
- Scenario 流程图编辑器。

## 4.3 V2：AI 原生能力完整化

- 项目知识库；
- Embedding；
- RAG；
- 历史测试经验；
- 人工反馈 Dataset；
- AI Evaluation Center；
- Model/Prompt 对比；
- Prompt A/B；
- AI 采纳率；
- AI 漏测/修改率；
- AI 需求变更影响分析；
- Case 修改建议；
- 完整 Case 历史版本浏览；
- Linux Runner；
- 审计中心完整 UI；
- 邮件/Webhook 通知增强；
- 独立 Web Agent 模式；
- 自定义 Role/Permission。

---

# 5. 明确 Out of Scope

以下内容在当前总规划中明确不做，除非未来重新评审：

- App 自动化；
- Android/iOS 真机；
- macOS Runner；
- 分布式 Performance Worker；
- Runner 自动故障转移；
- 运行中任务跨 Runner 迁移；
- 生产级断点续跑；
- Scenario 嵌套调用；
- Kubernetes；
- 微服务化拆分；
- 多租户 SaaS 计费；
- Oracle；
- SQL Server；
- PostgreSQL（仅接口预留）；
- 第一阶段 Firefox/WebKit/Edge；
- 第一阶段移动端 Web 模拟；
- 第一阶段完整企业级权限矩阵；
- 第一阶段 Jira/禅道直接写入；
- 第一阶段复杂邮件通知渠道；
- 通用低代码工作流平台能力。

---

# 6. 用户角色

## 6.1 V1 角色

### Admin
- 系统所有权限；
- 管理用户；
- 管理全局模型；
- 管理 Prompt；
- 管理 MCP；
- 管理 Runner；
- 管理系统配置；
- 危险物理删除。

### Project Owner
- 项目配置；
- 项目成员；
- 环境；
- Case；
- Scenario；
- Run；
- Report；
- 项目模型绑定；
- Project Archive。

### Tester
- 查看项目；
- 创建/编辑 Requirement；
- 创建/编辑 Case；
- 创建 Scenario；
- 执行；
- 调试；
- 查看 Report；
- 审核 AI Case；
- 接受 Locator 修复；
- 生成缺陷草稿。

### Viewer
- 只读；
- 不可修改；
- 不可执行；
- 不可查看 Secret 明文。

## 6.2 V2
扩展自定义 Role + Permission。

---

# 7. 信息架构与完整菜单

## 7.1 系统级菜单

```text
首页
项目
Runner 中心
模型中心
Prompt 中心
MCP 中心
用户与角色
系统设置
```

## 7.2 项目内菜单

```text
项目概览 / AI 测试工作台

测试资产
├── 需求
├── API 定义
├── API 用例
├── Web 用例
├── 测试数据
└── 元素库

测试编排
├── API Scenario
├── Test Plan          [V1]
└── 定时任务           [V1]

测试执行
├── 执行中心
├── API 测试
├── Web 测试
└── 性能测试           [V1]

AI
├── AI 工作台
├── 知识库             [V2]
├── AI 调用
└── AI 评测            [V2]

质量
├── 测试报告
├── 缺陷草稿
└── 需求影响分析

配置
├── 环境
├── 变量与 Secret
├── 数据库
├── 模型绑定
└── 项目成员
```

---

# 8. 项目首页 / AI 测试工作台

进入项目后默认展示工作流而非空 Dashboard：

```text
导入资料
   ↓
AI 评审
   ↓
生成测试用例
   ↓
人工审核
   ↓
创建/选择 Scenario
   ↓
选择环境与 Runner
   ↓
执行
   ↓
查看报告
```

页面卡片：
- Requirement 数量；
- API Definition 数量；
- API Case 数量；
- Web Case 数量；
- 待审核 Case；
- 今日 Run；
- 今日 Fail；
- 在线 Runner；
- 最近执行记录。

快捷入口：
- 导入 Markdown；
- 导入 Swagger；
- AI 生成 Case；
- 新建 API Scenario；
- 新建 Web Case；
- 开始录制；
- 执行最近 Scenario。

---

# 9. 总体系统架构

```text
┌─────────────────────────────────────────────────────────┐
│                        Vue 3 Web                        │
│ Element Plus / Pinia / Vue Router / ECharts            │
└──────────────────────────┬──────────────────────────────┘
                           │ REST / WebSocket / SSE
                           ▼
┌─────────────────────────────────────────────────────────┐
│                       FastAPI API                       │
│                                                         │
│ Auth / Project / Asset / Scenario / Run / AI / Report  │
└──────────────┬─────────────┬─────────────┬──────────────┘
               │             │             │
               ▼             ▼             ▼
           MySQL          Redis        RabbitMQ
        持久化数据       实时状态       任务队列
               │
               ▼
             MinIO
          Evidence 文件

               ┌────────────────────────────────┐
               │       Scheduler / Dispatcher   │
               └──────────────┬─────────────────┘
                              │
                              ▼
                  ┌─────────────────────┐
                  │   Windows Runner    │
                  │                     │
                  │ API Executor        │
                  │ Web Executor        │
                  │ Agent Executor      │
                  │ SQL Executor        │
                  │ Script Executor     │
                  │ Perf Executor (V1)  │
                  └─────────┬───────────┘
                            │
             ┌──────────────┼────────────────┐
             ▼              ▼                ▼
          被测 API        Chrome           MySQL
                            │
                            ▼
                       Playwright
```

AI 基础设施：

```text
业务模块
   ↓
AI Service
   ↓
Model Gateway
   ├── Model Center
   ├── Prompt Center
   ├── Output Schema
   ├── Retry/Fallback
   ├── Usage Log
   ├── MCP Tool Permission
   └── Vision
```

---

# 10. 技术栈

## 10.1 前端
- Vue 3；
- TypeScript；
- Vite；
- Element Plus；
- Pinia；
- Vue Router；
- Axios；
- ECharts；
- CodeMirror/Monaco：SQL、Python、JSON；
- Markdown Editor；
- 后续 Flow Editor 可采用 Vue Flow。

## 10.2 后端
- Python 3.12；
- FastAPI；
- Pydantic v2；
- SQLAlchemy 2.x；
- Alembic；
- httpx；
- aiohttp（必要时）；
- JSONPath 库；
- APScheduler（V1 Scheduler 外围，可按需要替换）；
- WebSocket；
- SSE。

## 10.3 基础设施
- MySQL 8；
- Redis 7；
- RabbitMQ；
- MinIO；
- Docker Compose。

## 10.4 Runner
- Python；
- Playwright；
- Chrome；
- JMeter（V1）；
- Java（JMeter 所需）；
- httpx/aiohttp；
- Python Script Sandbox；
- SQLAlchemy/数据库驱动。

## 10.5 AI
- OpenAI-compatible SDK；
- 可封装官方/兼容 Provider；
- LangGraph：复杂 Agent；
- MCP；
- Vision；
- JSON Schema；
- 后续 Embedding / Vector Store。

---

# 11. 后端代码分层

推荐：

```text
backend/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── exceptions.py
│   │   ├── logging.py
│   │   └── constants.py
│   ├── api/
│   │   ├── deps.py
│   │   └── v1/
│   ├── modules/
│   │   ├── auth/
│   │   ├── users/
│   │   ├── projects/
│   │   ├── requirements/
│   │   ├── api_assets/
│   │   ├── web_assets/
│   │   ├── scenarios/
│   │   ├── runs/
│   │   ├── runner/
│   │   ├── reports/
│   │   ├── ai/
│   │   ├── prompts/
│   │   ├── models/
│   │   ├── mcp/
│   │   ├── environments/
│   │   ├── secrets/
│   │   └── performance/
│   ├── infrastructure/
│   │   ├── db/
│   │   ├── redis/
│   │   ├── rabbitmq/
│   │   ├── minio/
│   │   └── websocket/
│   └── shared/
│       ├── schemas/
│       ├── enums/
│       └── utils/
├── migrations/
└── tests/
```

每个业务模块内部：

```text
module/
├── router.py
├── schemas.py
├── service.py
├── repository.py
├── models.py
├── enums.py
└── domain.py
```

禁止在 Router/Controller 中混合：
- DB；
- AI；
- 文件；
- RabbitMQ；
- 复杂业务流程。

---

# 12. 核心领域模型

平台围绕以下关系设计：

```text
Project
 ├── Requirement
 │    └── RequirementVersion
 │
 ├── ApiDefinition
 │    └── ApiCase
 │         └── ApiCaseVersion
 │
 ├── WebCase
 │    └── WebCaseVersion
 │
 ├── Scenario
 │
 ├── Environment
 │
 ├── TestData
 │
 ├── TestRun
 │    └── CaseRun
 │         └── StepRun
 │              └── Evidence
 │
 └── Report
```

核心语义：

```text
Requirement
    ↓ relates
Test Case
    ↓ compose
Scenario / Plan
    ↓ execute
Test Run
    ↓
Case Run
    ↓
Step Run
    ↓
Evidence
```

---

# 13. Project 模块

## 13.1 字段建议

`projects`

| 字段 | 说明 |
|---|---|
| id | 主键 |
| name | 项目名 |
| code | 唯一项目编码 |
| description | 描述 |
| status | ACTIVE / ARCHIVED |
| owner_id | Owner |
| created_at | 创建时间 |
| updated_at | 更新时间 |
| archived_at | 归档时间 |

## 13.2 行为
- 创建；
- 编辑；
- 归档；
- 恢复；
- 查看成员；
- 查看统计。

不默认物理删除项目。

---

# 14. Requirement 模块

## 14.1 V1 输入来源
- Markdown；
- 手工创建；
- Swagger 作为 API 资料来源，不与 Requirement Markdown 强制混为一类。

## 14.2 Markdown 导入流程

```text
上传 .md
  ↓
解析标题层级
  ↓
生成 Requirement Tree
  ↓
预览
  ↓
用户确认
  ↓
写入 Requirement + Version
```

## 14.3 Requirement 字段

`requirements`

- id；
- project_id；
- parent_id；
- code；
- title；
- type；
- order_index；
- status；
- current_version_id；
- created_by。

`requirement_versions`

- id；
- requirement_id；
- version_no；
- markdown_content；
- content_hash；
- source_type；
- source_file_id；
- change_summary；
- created_by；
- created_at。

## 14.4 版本行为

不能覆盖旧内容：

```text
REQ V1
  ↓ Edit
REQ V2
```

UI 支持 Markdown Diff。

## 14.5 AI Requirement Review

输入：
- 当前 Requirement；
- 可选择关联父/兄弟节点；
- 用户附加说明；
- Prompt Version；
- Model Binding。

输出结构：
- clarity_issues；
- ambiguity；
- missing_rules；
- exception_gaps；
- testability；
- acceptance_criteria_suggestions；
- overall_summary。

结果：
- 保存 AI 原始响应；
- 保存结构化响应；
- 人工可确认/修改；
- 不直接修改原 Requirement。

---

# 15. Requirement ↔ Case 精确关联

表：

`requirement_case_links`

- requirement_id；
- requirement_version_id（可选）；
- case_type；
- case_id；
- relation_type；
- source：MANUAL / AI；
- confidence；
- created_at。

目的：
1. Coverage；
2. Requirement 变更影响；
3. Case 生成来源；
4. 历史 Run 可追溯。

## 15.1 V1 影响分析

V1 仅做确定性影响范围：

```text
Requirement V1 → V2
      ↓
Diff
      ↓
找到与该 Requirement 关联的 Case
      ↓
标记 POSSIBLY_OUTDATED
```

## 15.2 V2

AI 分析：
- 哪些修改会影响哪些 Case；
- 为什么；
- 建议如何修改；
- 用户 Accept 后生成 Case 新版本。

---

# 16. API Definition

## 16.1 定位

API Definition 表示“接口定义”，不表示测试用例。

例如：

```text
POST /api/login
```

一个 Definition 可被多个 API Case 引用：

```text
登录成功
密码错误
用户名为空
权限不足
```

## 16.2 字段

`api_definitions`

- id；
- project_id；
- folder_id；
- name；
- method；
- path；
- summary；
- description；
- request_schema；
- response_schema；
- auth_info；
- tags；
- source；
- source_version；
- status；
- created_at；
- updated_at。

## 16.3 Swagger 导入 V1 四层能力

### 第一层：解析接口
- path；
- method；
- tag；
- summary。

### 第二层：解析契约
- path param；
- query param；
- header；
- body；
- schema；
- response；
- status code；
- auth scheme。

### 第三层：AI 生成 Case
覆盖：
- 正常场景；
- 必填；
- 类型；
- 边界；
- 空值；
- 枚举；
- 身份认证；
- 权限；
- 错误码；
- 响应字段。

### 第四层：AI 推荐 Scenario
根据：
- URL；
- request/response schema；
- operationId；
- 字段名；
- token/id/order_id/user_id；
- API 描述。

生成候选依赖：

```text
/login response.token
  ↓
/orders Authorization

/orders response.orderId
  ↓
/orders/{id}
```

必须人工审核后保存。

---

# 17. API Case

## 17.1 基本结构

```text
Case Meta
   ↓
Pre Actions
   ↓
Request
   ↓
Post Actions
   ↓
Assertions
   ↓
Cleanup（可选）
```

## 17.2 字段

- id；
- project_id；
- api_definition_id；
- title；
- description；
- priority；
- type；
- status；
- current_version_id；
- tags；
- created_by。

状态：
- DRAFT；
- APPROVED；
- OUTDATED；
- DISABLED。

## 17.3 Case Version

`api_case_versions`

保存：
- request template；
- pre actions；
- post actions；
- assertions；
- variables；
- data source；
- cleanup；
- source requirement；
- AI source metadata。

历史 Run 必须保存执行的 `case_version_id`。

---

# 18. AI Case 生成与审核

流程：

```text
Requirement / Swagger
        ↓
选择生成规则
        ↓
Model + Prompt
        ↓
结构化 JSON
        ↓
Schema Validation
        ↓
AI Generated Draft
        ↓
批量审核页面
        ↓
单条编辑
        ↓
APPROVED
```

批量审核支持：
- 全选；
- 批量通过；
- 批量拒绝；
- 批量删除；
- 批量 Priority；
- 批量 Tag；
- 批量关联 Requirement。

单条详情同时展示：
- AI 原始内容；
- 当前编辑值；
- Diff；
- 来源；
- Prompt Version；
- Model。

---

# 19. API Scenario

## 19.1 定位

API Scenario 表示业务流程，不等于单接口 Case。

例如：

```text
登录
 ↓
创建订单
 ↓
提取 order_id
 ↓
查询订单
 ↓
IF 未支付
 ├─ 支付
 └─ 跳过
 ↓
删除订单
 ↓
Cleanup
```

## 19.2 V1 编辑器

采用**列表式高级编排器**。

支持：
- 拖动排序；
- 缩进表示 IF/LOOP；
- 节点配置抽屉；
- 调试当前节点；
- 从当前节点调试；
- 运行到当前节点；
- 查看 Runtime Context。

V1 包含 Flow 图视图。

## 19.3 节点类型

### Request
- HTTP；
- SSE；
- WebSocket（可规划；若实现周期紧可延后到 V1 后半）。

### Logic
- IF；
- ELSE；
- LOOP；
- WAIT。

### Data
- SET VARIABLE；
- EXTRACT；
- Faker；
- File Data；
- Database Query；
- AI Data。

### Assertion
- Status；
- JSONPath；
- Schema；
- Regex；
- Header；
- Response Time；
- AI Semantic。

### Database
- SQL Query；
- SQL Execute；
- SQL Cleanup。

### Script
- Python Script。

### Control
- Start；
- End；
- Fail；
- Stop；
- Retry（由引擎控制）。

### Cleanup
- API Cleanup；
- SQL Cleanup。

---

# 20. Scenario DSL

数据库不保存 Python 代码作为主流程，而保存统一 DSL。

推荐总体结构：

```json
{
  "version": "1.0",
  "nodes": [],
  "edges": [],
  "settings": {
    "stop_on_failure": true,
    "cleanup_policy": "ALWAYS"
  }
}
```

HTTP 节点示例：

```json
{
  "id": "node_login",
  "type": "HTTP_REQUEST",
  "name": "用户登录",
  "config": {
    "method": "POST",
    "url": "{{base_url}}/api/login",
    "headers": {},
    "body": {
      "username": "{{username}}",
      "password": "{{password}}"
    },
    "timeout_ms": 30000
  },
  "on_failure": "STOP"
}
```

Extract：

```json
{
  "id": "node_extract_token",
  "type": "EXTRACT",
  "config": {
    "source": "RESPONSE_BODY",
    "method": "JSONPATH",
    "expression": "$.data.access_token",
    "target": "token"
  }
}
```

IF：

```json
{
  "id": "node_if",
  "type": "IF",
  "config": {
    "left": "{{order_status}}",
    "operator": "EQ",
    "right": "UNPAID"
  }
}
```

SQL：

```json
{
  "id": "node_sql",
  "type": "SQL_QUERY",
  "config": {
    "connection_id": 12,
    "sql": "SELECT status FROM orders WHERE id={{order_id}}",
    "extract": {
      "status": "rows[0].status"
    }
  }
}
```

---

# 21. 变量系统

## 21.1 变量作用域

从低到高建议：

```text
Global
  ↓
Project
  ↓
Environment
  ↓
Plan / Scenario
  ↓
Runtime
  ↓
Step
```

解析时越具体作用域优先级越高。

## 21.2 类型

- STRING；
- NUMBER；
- BOOLEAN；
- JSON；
- SECRET；
- FILE；
- LIST。

## 21.3 动态变量

```text
{{$uuid}}
{{$timestamp}}
{{$random_int}}
{{$random_email}}
{{$faker:name}}
```

## 21.4 Runtime Context

每次 Run 都拥有独立 Runtime Context：

```json
{
  "token": "***",
  "order_id": 9527,
  "status": "UNPAID"
}
```

V1 实时上下文可主要放 Redis，同时关键快照持久化至 MySQL。

---

# 22. 接口数据提取

支持：

## Response Body
- JSONPath；
- XPath；
- Regex；
- Raw Text。

## Response Metadata
- Header；
- Cookie；
- Status；
- Response Time。

示例：

```text
token ← $.data.access_token
session_id ← Header.X-Session-ID
csrf ← Cookie.csrf_token
order_id ← $.data.orderId
```

提取失败行为：
- FAIL；
- SET_NULL；
- CONTINUE；
由节点配置。

---

# 23. 接口关联

核心原则：**AI 可推荐关联，但 Runner 用确定性变量执行。**

示例：

```text
POST /login
  ↓ extract token

POST /orders
Authorization: Bearer {{token}}
  ↓ extract order_id

GET /orders/{{order_id}}
```

前端必须提供：
- 变量选择器；
- 可用变量提示；
- 变量来源；
- Run 中实时值；
- Secret 自动隐藏。

---

# 24. 前置/后置动作

单 API Case：

```text
Pre Actions
  ↓
Request
  ↓
Post Actions
  ↓
Assertions
```

Pre:
- 设置变量；
- 生成 Faker；
- SQL Query；
- Python Script；
- 获取 Token。

Post:
- 提取变量；
- Python Script；
- 写 Runtime Context；
- 注册 Resource。

断言建议在 Post 之后执行，避免业务结果所需字段无法提取。

---

# 25. 数据驱动

支持来源：
- 固定值；
- Environment；
- Runtime；
- Faker；
- CSV；
- Excel；
- MySQL；
- 上一步 API；
- AI；
- 人工运行时输入。

## 25.1 CSV/Excel

例如：

| username | password | expected |
|---|---|---|
| admin | 123456 | success |
| admin | wrong | fail |
| empty | 123456 | fail |

执行时每行生成一次 Case Iteration。

保存：
- dataset_id；
- row_index；
- 实际参数快照；
- iteration result。

---

# 26. MySQL 数据库测试

V1 只实现 MySQL。

`database_connections`
- name；
- host；
- port；
- database；
- username；
- encrypted_password；
- environment_id；
- ssl；
- enabled。

功能：
- Test Connection；
- Query；
- Execute；
- Result Extract；
- Cleanup。

前端允许直接写 SQL，但不要求用户写连接代码。

敏感连接信息不返回明文。

---

# 27. Python Script Node

V1 只支持 Python。

目标：
处理平台 DSL 难以表达的自定义逻辑。

例如：

```python
result = sha256(f"{timestamp}:{user_id}:{secret}")
```

Script 输入：
- Runtime Context 的只读/授权变量；
- helper API。

Script 输出：
- 指定变量。

安全原则：
- 禁止直接暴露平台服务器 shell；
- Script 默认在 Runner 执行；
- 需要执行超时；
- 限制模块；
- 限制文件系统范围；
- 禁止读取 Runner 敏感配置。

---

# 28. Secret Management

Secret 类型：
- Password；
- Token；
- API Key；
- DB Password；
- Client Secret。

规则：
1. 数据库存加密值；
2. API 默认不返回明文；
3. UI 显示 `••••••`；
4. 执行时通过 Secret Resolver 获取；
5. 日志自动脱敏；
6. Runtime Context 快照脱敏；
7. AI Prompt 默认不包含 Secret，除非明确需要并通过策略允许。

---

# 29. 动态 Token 与认证

Environment 可配置认证策略。

例如：

```text
Auth Type: DYNAMIC_LOGIN
Login API: /api/login
Username: {{username}}
Password: {{password}}
Extract: $.data.token → token
```

请求自动使用：

```text
Authorization: Bearer {{token}}
```

401 策略：
- 刷新/重新登录一次；
- 原请求重试一次；
- 仍失败则 FAIL。

---

# 30. Cleanup 与测试数据生命周期

## 30.1 Scenario 生命周期

```text
SETUP
  ↓
EXECUTE
  ↓
VERIFY
  ↓
TEARDOWN
```

TEARDOWN 原则：
- Scenario 中间失败仍尝试；
- Cancel 时仍尝试；
- Timeout 时尽量尝试；
- Force Stop 可能无法保证。

## 30.2 策略

- ALWAYS（默认）；
- ON_SUCCESS；
- ON_FAILURE；
- NEVER。

## 30.3 类型
- API Cleanup；
- SQL Cleanup。

## 30.4 Resource Registry

执行中创建的资源：

```text
USER 10086
ORDER 9527
COUPON 123
```

保存：
- run_id；
- resource_type；
- resource_id；
- cleanup_type；
- cleanup_config；
- status；
- created_at；
- cleaned_at。

清理默认 LIFO：

```text
Coupon
 ↓
Order
 ↓
User
```

## 30.5 run_id 标识

推荐测试数据携带可追踪前缀：

```text
qat_{run_short_id}_{sequence}
```

便于异常时定位和批量清理。

---

# 31. API 断言体系

## 31.1 确定性断言
- Status Code；
- JSONPath Equal；
- Contains；
- Regex；
- Header；
- Cookie；
- JSON Schema；
- Response Time；
- Exists / Not Exists；
- Array Length；
- Type。

## 31.2 AI 语义断言

用于：
- 自然语言回答是否解决问题；
- 文本语义是否满足业务；
- 复杂非固定响应；
- LLM 输出质量。

## 31.3 最终状态
- PASS；
- FAIL；
- REVIEW。

原则：
- 确定性断言失败 → FAIL；
- AI 高置信正常 → PASS；
- AI 低置信或与确定性结果冲突 → REVIEW。

---

# 32. API Step Failure 策略

每一步：
- STOP；
- CONTINUE。

默认 STOP。

自动 Retry：
- 仅 Step-Level；
- 最多 1 次；
- 仅针对可重试错误；
- 不默认自动重跑整个 Scenario。

---

# 33. Web Case

## 33.1 双层结构

每条 Web Case 同时保存：

### 人类可读自然语言
```text
1. 打开登录页
2. 输入用户名
3. 输入密码
4. 点击登录
5. 验证进入首页
```

### 结构化步骤
```text
GOTO
FILL
FILL
CLICK
ASSERT_VISIBLE
```

这两层是独立但关联的。

AI 可从自然语言生成结构化步骤，必须人工确认。

## 33.2 字段
- title；
- description；
- precondition；
- natural_steps；
- structured_steps；
- parameters；
- assertions；
- cleanup；
- status；
- current_version_id；
- related_requirements。

---

# 34. Web 元素库

结构：

```text
Page
├── username_input
├── password_input
├── login_button
└── forgot_password
```

元素字段：
- name；
- page；
- description；
- primary_locator；
- alternate_locators；
- locator_type；
- screenshot（可选）；
- last_verified_at；
- status。

元素库是推荐能力，不强制所有步骤必须引用元素库。

允许：
- `element://login_button`
- 直接 Locator。

---

# 35. 多 Locator

一个元素可保存多个：

```text
1. getByRole(button, name=登录)
2. getByText(登录)
3. #login
4. xpath=...
```

按 Priority 尝试。

这是自愈前的确定性容错层。

每个 Locator 保存：
- locator；
- type；
- priority；
- source；
- success_count；
- fail_count；
- last_success_at。

---

# 36. Web 录制

V1 必须做。

流程：

```text
平台点击开始录制
  ↓
Runner 启动 Chrome
  ↓
用户正常操作
  ↓
Recorder 捕获事件
  ↓
生成原始步骤
  ↓
AI 整理
  ↓
用户预览
  ↓
保存 Web Case
```

记录：
- click；
- fill；
- select；
- navigation；
- keypress；
- upload；
- assertion suggestion；
- DOM context；
- candidate locator。

---

# 37. AI 整理录制结果

输入：
- 原始录制动作；
- Locator；
- DOM 片段；
- 页面 title/url；
- 关键截图。

输出：
- 自然语言步骤；
- 结构化步骤；
- 元素命名；
- Locator 优化建议；
- Assertion 建议；
- 参数化建议。

例如：

```text
原始：
click div:nth-child(3)

整理：
点击【登录按钮】

Locator：
getByRole("button", name="登录")
```

必须预览后保存。

---

# 38. Web 结构化 Action

V1 规划完整常用操作：

## 页面
- GOTO；
- RELOAD；
- BACK；
- FORWARD；
- NEW_TAB；
- SWITCH_TAB；
- CLOSE_TAB。

## 元素
- CLICK；
- DOUBLE_CLICK；
- RIGHT_CLICK；
- FILL；
- CLEAR；
- HOVER；
- DRAG_DROP；
- UPLOAD；
- DOWNLOAD。

## 表单
- SELECT；
- CHECK；
- UNCHECK；
- RADIO。

## 键盘
- PRESS；
- ENTER；
- TAB。

## 等待
- WAIT_TIME；
- WAIT_ELEMENT；
- WAIT_URL；
- WAIT_TEXT；
- WAIT_NETWORK_IDLE。

## 浏览器状态
- COOKIE；
- LOCAL_STORAGE；
- SESSION_STORAGE；
- JS_EVAL。

---

# 39. Web 断言

- Exists；
- Visible；
- Hidden；
- Enabled；
- Clickable；
- Text Equal；
- Text Contains；
- Attribute；
- Input Value；
- URL；
- Title；
- Element Count；
- Download Success；
- Network Request；
- Screenshot Visual Compare；
- AI Semantic。

统一使用 Assertion Result：
- PASS；
- FAIL；
- REVIEW。

---

# 40. 登录态 / Session

提供 Session Profile。

保存：
- Cookie；
- LocalStorage；
- Storage State；
- source environment；
- updated_at；
- expires_at（若可知）。

Case 可选择：
- 不使用登录态；
- 使用 Session Profile。

登录态失效时：
- 自动执行绑定登录流程；
- 更新 Storage State；
- 原步骤重试一次。

---

# 41. Web 浏览器范围

V1：
- Chrome；
- headed；
- headless。

支持配置：
- window size；
- language；
- user-agent；
- proxy；
- download path。

暂不做：
- Edge；
- Firefox；
- WebKit；
- mobile emulation。

Executor 接口需预留 browser type。

---

# 42. Web Evidence

默认保存：
- 执行日志；
- Step start/end；
- 失败截图；
- 关键截图；
- Console Error；
- Network Error；
- Playwright Trace；
- Final URL；
- Page Title；
- Browser Version；
- Runner Version。

Video：
- 可选；
- 默认关闭。

---

# 43. 高级 Locator 自愈

完整链路：

```text
Primary Locator
   ↓ failed
Alternate Locator
   ↓ failed
DOM Similarity
   ↓ failed
LLM DOM Analysis
   ↓ failed
Vision Screenshot Analysis
   ↓ failed
Agent Locate
```

如果找到候选：

```text
执行候选 Locator
   ↓
验证操作后页面状态
   ↓
本次 Run 临时使用
   ↓
生成 Healing Proposal
```

不能自动永久写入元素库。

页面显示：

```text
旧 Locator:
#login-btn

新 Locator:
getByRole("button", name="登录")

证据：
截图 / DOM / AI 原因

[接受修复] [拒绝]
```

Accept 后创建元素版本/修改记录。

---

# 44. Web Agent

V1 不提供完整独立 Agent Case 模式。

V1 使用 Agent 的场景：
- 自愈最后阶段；
- 失败定位辅助。

V2 再提供：
- Standard；
- Agent；
- Auto。

---

# 45. AI Web 失败分析

输入：
- Case；
- expected；
- failed step；
- Playwright exception；
- DOM；
- screenshot；
- before/after screenshot；
- console；
- network errors；
- locator；
- runtime variables（脱敏）；
- environment；
- historical locator。

输出 Schema：

```json
{
  "failure_type": "ELEMENT_NOT_FOUND",
  "summary": "...",
  "root_cause_candidates": [],
  "confidence": 0.91,
  "suspected_product_bug": false,
  "self_healable": true,
  "suggestion": "...",
  "defect_draft_recommended": false
}
```

Failure Type：
- ELEMENT_NOT_FOUND；
- ASSERTION_FAILED；
- NETWORK_ERROR；
- ENVIRONMENT_ERROR；
- TEST_DATA_ERROR；
- SESSION_EXPIRED；
- TIMEOUT；
- SUSPECTED_PRODUCT_BUG；
- SCRIPT_ERROR；
- PLATFORM_ERROR；
- UNKNOWN。

---

# 46. 缺陷草稿

失败后 AI 可生成：

```text
标题
模块
环境
前置条件
复现步骤
预期结果
实际结果
证据
AI分析
关联Run
```

V1：
- 编辑；
- 复制；
- Markdown 导出。

未来：
- Jira；
- 禅道。

AI 只生成草稿，不能自动提交外部缺陷系统。

---

# 47. Model Center

## 47.1 接入渠道

接入渠道是平台级 API 配置，一个渠道可承载多个模型。字段：
- name；
- access_type：DIRECT / AGGREGATOR / SELF_HOSTED；
- provider：实际提供 API 的服务商，例如 OpenAI、阿里云百炼、OpenRouter、Ollama；
- protocol_type：V1 为 OPENAI_COMPATIBLE；
- base_url；
- 加密 API Key、指纹和轮换时间；
- enabled。

API Key 只属于接入渠道，不属于项目或单个模型；响应不得返回明文、密文和指纹。一个服务商允许创建多个具名渠道，以表达不同账号或环境。

## 47.2 模型配置

字段：
- name；
- connection_id；
- model_vendor：模型实际归属公司；
- model_name：渠道要求的实际模型标识；
- model_type：TEXT / VISION / EMBEDDING；
- supports_tool_call；
- supports_structured_output；
- max_context；
- timeout；
- input_price；
- output_price；
- enabled。

`model_vendor` 与渠道 `provider` 必须分离。例如 DeepSeek 模型可通过阿里云百炼渠道调用。项目不保存模型 API Key，只通过 Task Model Binding 选择模型。

## 47.3 Provider 与协议

V1 必须支持 OpenAI-Compatible 抽象，并允许官方直连、聚合平台和本地/自托管渠道。后续可以增加 Anthropic、Gemini 等原生协议适配，不得用“模型公司”字段代替传输协议或接入服务商。

---

# 48. Task Model Binding

不同任务绑定不同模型：

```text
Requirement Review → Model A
API Case Generate   → Model A
Web Case Generate   → Model A
AI Assertion        → Model B
Web Healing         → Vision/Model C
Failure Analysis    → Model A
Performance Analyze → Model B
```

Binding 层级：
- System Default；
- Project Override；
- Task Override（高级）。

---

# 49. Model Fallback

仅在以下错误触发：
- Timeout；
- Rate Limit；
- Service Unavailable；
- Network/Provider unavailable。

不因为：
- 结果质量低；
- AI 回答“不喜欢”；
而自动切换。

配置：

```text
Primary Model
Fallback Model
Max Fallback = 1
```

必须记录实际使用模型。

---

# 50. Prompt Center

Prompt 不得散落在业务代码。

分类：
- REQUIREMENT_REVIEW；
- API_DOC_REVIEW；
- API_CASE_GENERATE；
- WEB_CASE_GENERATE；
- RECORDING_CLEANUP；
- AI_ASSERTION；
- LOCATOR_HEALING；
- WEB_FAILURE_ANALYSIS；
- PERFORMANCE_ANALYSIS；
- DEFECT_DRAFT；
- IMPACT_ANALYSIS。

功能：
- 创建；
- 编辑；
- 复制；
- 测试；
- 启用；
- 禁用；
- 回滚。

---

# 51. Prompt Version

`prompt_versions`
- prompt_id；
- version_no；
- system_prompt；
- user_template；
- output_schema_id；
- change_note；
- created_by；
- created_at。

每次 AI Call 必须保存 `prompt_version_id`。

---

# 52. Structured Output

AI 返回必须尽量 Schema-first。

流程：

```text
LLM response
  ↓
JSON parse
  ↓
Pydantic / JSON Schema validation
  ↓ invalid
自动 Repair 一次
  ↓ still invalid
AI TASK FAILED
```

禁止长期使用“从 Markdown ```json 代码块里靠正则提取 JSON”作为主实现。

---

# 53. AI Call Log

每次调用记录：

- project_id；
- task_type；
- entity_type；
- entity_id；
- model_config_id；
- actual_model；
- prompt_version_id；
- input_token；
- output_token；
- total_token；
- estimated_cost；
- latency_ms；
- success；
- fallback_used；
- retry_count；
- error_type；
- response_id；
- created_at。

大原始响应可放 MinIO 或压缩字段。

---

# 54. AI 原始结果与人工结果

AI 资产至少保留：

```text
Raw Response
  ↓
Parsed Structured Result
  ↓
Human Edited Result
  ↓
Approved Final Version
```

为 V2 统计：
- 采纳率；
- 修改率；
- 删除率；
- 漏测补充率；
打基础。

---

# 55. MCP Center

字段：
- name；
- endpoint；
- transport；
- enabled；
- health_status；
- available_tools；
- last_check_at。

Agent 不硬编码 MCP 地址。

## 55.1 Tool Permission

按 Agent Type 配置白名单。

例如 Web Healing Agent：

允许：
- browser_snapshot；
- browser_screenshot；
- browser_click；
- browser_fill；
- browser_locator。

禁止：
- shell_exec；
- unrestricted_sql；
- filesystem outside workspace。

---

# 56. Vision 使用边界

Vision 仅用于：
- Markdown 中需求图片理解；
- Web 失败截图；
- Locator 自愈；
- UI 视觉断言；
- 页面异常理解。

不默认把每一步截图送 Vision。

---

# 57. RAG [V2]

Project Knowledge Base 来源：
- Requirement；
- API 文档；
- Case；
- 缺陷；
- Report；
- 人工知识。

流程：

```text
Source
 ↓
Chunk
 ↓
Embedding
 ↓
Index
 ↓
Retrieve
 ↓
Rerank（可选）
 ↓
Prompt Context
```

要求记录：
- 检索片段；
- source id；
- score；
- 被哪个 AI Call 使用。

---

# 58. Feedback Dataset [V2]

采集：
- AI 生成 Case；
- 人工删除；
- 人工修改；
- 人工新增；
- AI Assertion；
- 人工复核；
- AI Failure；
- 最终结论。

形成结构化 feedback sample，为 Evaluation Center 使用。

---

# 59. AI Evaluation Center [V2]

对比维度：

```text
Model A + Prompt V1
Model A + Prompt V2
Model B + Prompt V2
```

指标：
- JSON Schema 成功率；
- Case 采纳率；
- Case 修改率；
- Case 重复率；
- 漏测率（人工标注集）；
- AI Assertion 准确率；
- 延迟；
- Token；
- Cost。

---

# 60. Runner 总体设计

统一一个 Runner：

```text
AI Test Runner
├── API Executor
├── Web Executor
├── Agent Executor
├── SQL Executor
├── Script Executor
└── Performance Executor [V1]
```

Runner 是执行节点，不负责业务数据库核心逻辑。

---

# 61. Runner 注册

流程：

```text
平台创建 Runner Registration Token
  ↓
Token 仅展示一次
  ↓
Runner register
  ↓
后端验证
  ↓
生成 runner_id + runner credential
  ↓
Runner Heartbeat
```

Runner 页面显示：
- Name；
- ID；
- IP；
- OS；
- CPU；
- RAM；
- Disk；
- Python；
- Chrome；
- Playwright；
- Java；
- JMeter；
- Capabilities；
- Tags；
- Slots；
- Status；
- Last Heartbeat。

## 61.1 V1 Runner 控制面 API 与数据边界

V1 Phase 4.1 先实现注册与心跳控制面，暂不实现任务调度、执行器和正式 Run 生命周期。

管理员 API：

```http
POST /api/v1/runners/registration-tokens
GET  /api/v1/runners/registration-tokens
POST /api/v1/runners/registration-tokens/{token_id}/revoke
GET  /api/v1/runners
GET  /api/v1/runners/{runner_id}
POST /api/v1/runners/{runner_id}/revoke
```

Runner API：

```http
POST /api/v1/runners/register
POST /api/v1/runners/{runner_id}/heartbeat
```

Registration Token 只返回创建响应一次，数据库仅保存 HMAC 摘要；注册成功后 credential 只返回一次，数据库仅保存摘要。Registration Token 通过数据库行锁与条件消费保证一次性语义，credential 使用独立 Bearer 认证、撤销后立即失效。

Runner 元数据持久化到 MySQL 的 `runners`、`runner_registration_tokens`、`runner_tags`、`runner_capabilities` 和 `runner_slots` 表。tags 归一化去重；Capability 支持 API/WEB/SQL/SCRIPT/SSE/JMETER 及 READY/UNAVAILABLE；Slot 支持 API/WEB/PERFORMANCE，并校验 `0 <= available <= total`。

Redis 保存命名空间化的 heartbeat 与 Run Stream 临时数据并设置 TTL；管理员读取 Runner 时 Redis 不可用只能返回 `UNKNOWN`，不得伪报 `ONLINE`。MySQL 保存 `last_heartbeat_at` 和完整系统元数据作为最终记录。Runner 控制面之后，Phase 4.4A 已新增 Run/CaseRun/StepRun 持久化与生命周期基础，Phase 4.4B 已收口当前受限 API Executor 的创建前可执行一致性，Phase 4.5A～4.5C 已落地 RabbitMQ 可靠投递、消费认领、真实 HTTP 执行和前台 Worker，后续小步已增加 RabbitMQ 有界自动重连、动态 API Slot、最小 MinIO Evidence、Redis/SSE、API_CASE Timeout、协作 Cancel 与 Step Retry。Windows Service、RabbitMQ 停止→恢复真实验收、Scenario/SQL/Script/Web Executor、Force Stop 和断线任务恢复仍属于后续 Phase 4 范围。

---

# 62. Runner Tag

例如：
- windows；
- web；
- api；
- performance；
- internal-network；
- high-memory。

Run 可要求：

```text
required_tags:
- windows
- internal-network
```

Scheduler 只选择满足条件 Runner。

---

# 63. Runner Capability

启动检测：
- API；
- WEB；
- SQL；
- SCRIPT；
- SSE；
- JMETER。

例如：

```text
WEB READY
JMETER UNAVAILABLE: JMeter not installed
```

调度前 Capability Check，而不是执行后才失败。

---

# 64. Runner Slot

Runner 配置：

```text
API Slots: 5
WEB Slots: 2
PERFORMANCE Slots: 1
```

Scheduler 判断：
- online；
- capability；
- tags；
- available slot。

Performance 默认独占/slot=1。

当前 Windows Runner 的 API Slot 使用线程安全的 `SlotState`/`SlotLease` 管理：合法 API_CASE 进入执行周期后从 available 中占用 1 个 Slot，所有成功、失败、超时、取消、重排和异常路径都必须在 `finally` 中释放。Slot 变化会唤醒独立 heartbeat 线程尽快上报 `available`，heartbeat 不得访问 RabbitMQ channel。当前只实现单 Worker、`prefetch=1` 下的 API Slot 动态观察；Backend 原子预留、多任务并发和断线租约回收尚未实现。

---

# 65. RabbitMQ / Redis / MySQL / MinIO 职责

## RabbitMQ
- Task Queue；
- Run Dispatch；
- Executor Queue。

Phase 4.5A 已实际落地可靠投递与消费认领控制面：后端使用 pika 声明 durable topic exchange `ai_test.tasks.v1`，启用 publisher confirms、mandatory publish 和持久化消息；`run_dispatch_outbox` 在 MySQL 保存稳定 `message_id`、Run 关联、信封、投递状态和有限重试信息。Runner 声明固定 durable queue、DLX/死信队列和 Slot routing binding，以 `prefetch=1`、手动 ACK/NACK 严格消费 V1 信封，并通过 `consume-once` 调用受保护 claim 接口。Phase 4.5B 在此基础上增加 `execute-once`：对受限 API_CASE 完成 plan/start/HTTP/complete，只有服务端完成终态聚合后才 ACK。Phase 4.5C 再增加前台 `worker`：独立线程持续 heartbeat，主线程持续轮询并执行任务，SIGINT/SIGTERM 等待 in-flight 任务收口后退出。后续已增加 RabbitMQ 瞬时故障有界自动重连和 API Slot 动态占用/释放；当前仍未实现 Windows Service、真实停服恢复验收、Scenario/SQL/Script/Web Executor、Backend 原子 Slot 预留或通用上报协议。

## Redis
- Runner heartbeat；
- 在线状态；
- Slot；
- 实时进度；
- Runtime Context；
- 分布式锁；
- 临时缓存。

## MySQL
- 所有最终业务状态；
- Run；
- CaseRun；
- StepRun；
- Run Dispatch Outbox；
- AI Call；
- Runner metadata；
- Report；
- Resource Registry。

## MinIO
- screenshot；
- trace；
- video；
- large log；
- import；
- export；
- report file；
- AI attachment；
- performance raw file。

原则：

```text
RabbitMQ = 工作怎么排
Redis    = 现在发生什么
MySQL    = 最终发生过什么
MinIO    = 大文件证据
```

---

# 66. Run 状态机

2026-09-09 V1 收尾决策：Web Evidence 处理失败属于平台执行失败，使用严格的 `FAILED + WEB_EVIDENCE_ERROR` completion，并保留真实完整步骤 trace；允许实际动作均成功而 CaseRun/Run 因证据缺失失败，不伪造动作失败。普通执行失败的节点一致性校验不变，取消先提交仍优先。对具备可靠 `started_at + 生效总超时`、且已超过上报宽限的 RUNNING/CANCELLING，服务端在行锁后重新校验并安全收敛；不以 Runner 在线与否替代执行截止，不覆盖已提交终态，不宣称远端进程已终止或 Cleanup 已完成。具体契约和验收见 `文档/02-产品规格与计划/V1收尾开发与验收计划.md`。

V1：

```text
CREATED
  ↓
QUEUED
  ↓
ASSIGNED
  ↓
RUNNING
  ├── SUCCESS
  ├── FAILED
  ├── CANCELLED
  └── TIMEOUT
```

辅助：
- CANCELLING。

Phase 4.4A 已落地上述状态机的 MySQL 持久化基础：状态更新按 Run 行锁保护并支持重复提交幂等，终态不允许回退。公开入口按调用方隔离：项目用户只能将尚未调度的 `CREATED` Run 取消；绑定 Runner credential 只能从 `ASSIGNED` 开始上报 `RUNNING` 及与子任务聚合结果一致的终态，不能伪造 `QUEUED/ASSIGNED`；CaseRun/StepRun 执行状态仅接受绑定 Runner credential。创建入口当前只创建 `CREATED`，不代表已进入 RabbitMQ 或开始执行；运行中取消的 `CANCELLING→CANCELLED` 仍需后续调度/执行控制链路接入。

Phase 4.5A 增加两条受保护控制面路径：项目可写用户通过 dispatch 重新校验 Runner 当前在线、启用状态、Capability/Tag/Slot 后，原子推进 `CREATED→QUEUED` 并创建/复用 Outbox；只有 Outbox 已确认发布且绑定 Runner credential 通过校验时，Runner claim 才能原子推进 `QUEUED→ASSIGNED`。重复 dispatch/claim 幂等，失败投递保留可重试状态，不伪报 `PUBLISHED`，本小步不推进 `RUNNING`。

Phase 4.5B 增加受保护的 execution-plan/start/complete：已认领的绑定 Runner 获取固定 Case Version 计划后，原子推进 `ASSIGNED→RUNNING`，执行真实 HTTP，并由服务端复用确定性断言引擎聚合 `SUCCESS/FAILED`。当前协议仅接受 auth=NONE、无 Runtime 模板和高级 Action/Data Source/Cleanup/AI Assertion 的 API Case；不支持的计划 fail closed。重复 start/complete 幂等，Runner 仅在完成上报成功后 ACK。

不加入：
- MIGRATING；
- RECOVERING；
- RUNNER_LOST 自动恢复；
- FAILOVER。

---

# 67. Run / CaseRun / StepRun

## TestRun
一次顶层执行。

字段：
- run_code；
- run_type；
- project_id；
- environment_id；
- runner_id；
- status；
- trigger_type；
- started_at；
- ended_at；
- total；
- pass；
- fail；
- review；
- timeout；
- runtime_snapshot_id。

## CaseRun
- run_id；
- case_id；
- case_version_id；
- status；
- duration；
- retry_count。

## StepRun
- case_run_id；
- node_id；
- step_name；
- step_type；
- status；
- started_at；
- ended_at；
- duration；
- error_type；
- error_message；
- retry_count。

Phase 4.4A 实际落地：迁移 `20260824_0017` 新增 `runs`、`case_runs`、`step_runs`，后续 `20260824_0018` 将执行资产外键收紧为 `RESTRICT` 并补齐 Run/CaseRun 目标与版本一致性约束，避免历史执行记录失去 Case/Scenario Version 追溯。`runs.id` 使用稳定字符串 `run_id`，可与 Resource Registry 的外部 `run_id` 关联；数据库列 `pass_count/fail_count/review_count/timeout_count` 对外映射为 `pass/fail/review/timeout`。CaseRun/StepRun 的状态、时间戳、错误信息和重试次数均持久化，Run 汇总由服务端从 CaseRun 状态确定性重算。

Phase 4.5A 新增迁移 `20260825_0019` 的 `run_dispatch_outbox`：每个 Run 只有一个稳定 `message_id`，payload 严格遵循根目录 `contracts/run-task-envelope-v1.schema.json` 的 V1 信封；保存 `routing_key`、`schema_version`、`PENDING/PUBLISHED/FAILED`、`attempt_count`、有界脱敏 `last_error`、`published_at`、`claimed_at` 和认领 Runner。`message_id` 与 `run_id` 均唯一，Run/Runner 使用外键追溯，投递失败不把正式历史只放在 RabbitMQ 或 Redis。

Phase 4.5B 新增迁移 `20260825_0020` 的 `run_api_execution_results`：以 Run/CaseRun/message 唯一约束和 FK 保存 API 执行终态、结果类型、完成时间、非敏感响应 Header、正文大小/SHA-256 与脱敏断言摘要。响应正文、Cookie、敏感 Header、断言 expected/actual 不写入 MySQL；完整响应只在 Runner 内存和认证完成上报请求中短暂存在，用于服务端断言，后续正式 Evidence 由 MinIO 承担。

---

# 68. 实时执行控制台

必须支持实时查看：

```text
时间
Level
Case
Step
Event
Status
Duration
```

WebSocket/SSE 推送示例：

```json
{
  "event": "STEP_END",
  "run_id": "...",
  "step_id": "...",
  "status": "PASS",
  "duration_ms": 420
}
```

前端：
- 自动滚动；
- Pause Auto Scroll；
- Filter Level；
- 查看当前变量；
- 点击 Step 查看 Evidence。

---

# 69. Cancel / Force Stop

## Cancel
```text
RUNNING
 ↓
CANCELLING
 ↓
停止新步骤
 ↓
安全终止当前动作
 ↓
Cleanup
 ↓
CANCELLED
```

## Force Stop
- 终止 Runner 子进程；
- 标记 Force Stop；
- Cleanup 不保证立刻完成；
- 报告明确写出 Cleanup 未完成风险。

---

# 70. Timeout

三层：
1. System default；
2. Test type default；
3. Run override。

越具体优先。

超时：
```text
TIMEOUT
 ↓
Cancel Executor
 ↓
Cleanup
```

---

# 71. Retry

仅自动 Step-Level Retry。

V1 最大 1 次。

典型：
- HTTP 网络瞬时错误；
- Web Locator 自愈后；
- MCP Tool Timeout；
- Token 刷新后。

不默认整体自动重跑 Scenario。

手工 Rerun：
- 从头；
- Scenario Editor 可调试指定节点；
- 不做真正跨 Run Runtime 恢复。

---

# 72. Runner 断线简化

V1：
- Heartbeat 超时 → Runner OFFLINE；
- 正在执行的 Run 标记异常失败；
- 用户手动 Rerun；
- 不做自动断点续跑；
- 不做自动迁移。

---

# 73. Evidence Center

`artifacts`
- id；
- project_id；
- run_id；
- case_run_id；
- step_run_id；
- artifact_type；
- file_name；
- mime；
- size；
- minio_key；
- metadata；
- created_at。

类型：
- SCREENSHOT；
- TRACE；
- VIDEO；
- LOG；
- REQUEST；
- RESPONSE；
- DOM；
- NETWORK；
- AI_RAW；
- REPORT；
- PERFORMANCE_RAW。

---

# 74. 统一 Report Framework

层级：

```text
Run Summary
  ↓
Case
  ↓
Step
  ↓
Evidence
```

## API Detail
- URL；
- method；
- request；
- response；
- status；
- headers；
- extraction；
- assertion。

## Web Detail
- action；
- locator；
- screenshot；
- trace；
- console；
- network；
- healing proposal。

## Performance Detail [V1]
- metrics；
- charts；
- SLA；
- time series。

---

# 75. Report Summary

显示：
- total；
- pass；
- fail；
- review；
- skip；
- success rate；
- duration；
- environment；
- runner；
- trigger；
- AI analysis summary。

支持：
- Web 查看；
- Markdown/HTML 导出可逐步实现。

---

# 76. Dashboard V1

只做够用版：
- 项目数；
- Case 数；
- 今日 Run；
- 通过率；
- Fail；
- 待审核；
- Runner Online；
- 最近 Run。

V2 再加：
- AI 采纳率；
- AI cost；
- Performance regression；
- Failure categories；
- Prompt performance。

---

# 77. Project Archive 与软删除

Project：
- ACTIVE；
- ARCHIVED。

归档后：
- 默认列表隐藏；
- 不允许继续 Run；
- 历史 Report 可查看。

Requirement / Case / Scenario：
优先：
- DISABLED；
- ARCHIVED；
而不是物理 DELETE。

物理删除仅 Admin 危险操作。

---

# 78. Test Plan [V1]

Test Plan 是执行集合：

```text
Regression Plan
├── API Scenario A
├── API Scenario B
├── Web Case C
└── Web Case D
```

配置：
- environment；
- runner strategy；
- variables；
- failure strategy；
- notification。

触发：
- manual；
- schedule；
- CI/CD。

---

# 79. Schedule [V1]

支持：
- Daily；
- Weekly；
- Cron。

Scheduler 创建 Run，不直接在调度线程执行测试。

---

# 80. CI/CD [V1]

提供：

```http
POST /api/v1/test-plans/{id}/run
```

返回：
- run_id；
- status URL。

提供：
```http
GET /api/v1/runs/{run_id}
```

CI 根据最终状态决定 Pipeline 成败。

---

# 81. Notification

V1：
- 站内通知。

V1/V2：
- Webhook；
- Email。

Webhook 可接：
- 飞书；
- 企业微信；
- 钉钉；
但不为每家写重型专用适配器作为 V1 核心。

---

# 82. Audit [V2 UI / V1 可埋数据]

建议从 V1 开始写基础 AuditEvent 数据：

- actor；
- action；
- entity；
- before；
- after；
- time；
- ip。

重要行为：
- 修改环境；
- 修改 Prompt；
- 修改模型；
- 删除/归档 Case；
- Accept Locator Heal；
- Accept AI Case；
- Secret 变更；
- Runner 注册。

V2 再做完整 Audit Center 页面。

---

# 83. 性能测试 [V1]

## 83.1 两种执行器

### JMeter
用于：
- 普通 HTTP；
- 业务链路；
- 常规性能。

### Python Streaming Executor
用于：
- SSE；
- LLM Streaming。

---

# 84. 功能 Scenario → 性能复用

选择已有 API Scenario：

```text
Scenario
 ↓
Compatibility Check
 ↓
Performance Mode
```

不支持性能的节点（例如 Agent）必须在开始前报错。

---

# 85. 压力模型

前端模式：
- Fixed Concurrency；
- Step Load；
- Fixed RPS；
- Fixed Iterations。

高级参数：
- Ramp Up；
- Ramp Down；
- Duration；
- Iterations；
- Warm Up。

---

# 86. 性能认证策略

用户选择：

- Shared Auth；
- Per-VU Login；
- Token Pool。

本项目选择的重点模式：**Token Pool**。

---

# 87. 性能数据源

与功能测试复用：
- CSV；
- Excel；
- Faker；
- DB；
- Variables；
- Pre-generated Dataset。

---

# 88. 普通性能指标

- request count；
- success；
- fail；
- success rate；
- error rate；
- RPS；
- TPS；
- min；
- max；
- average；
- P50；
- P75；
- P90；
- P95；
- P99；
- active users；
- received bytes；
- sent bytes。

趋势：
- concurrency；
- RPS；
- latency；
- error rate。

---

# 89. LLM / SSE 指标

核心：
- TTFT；
- total response latency；
- streaming duration；
- output tokens；
- input tokens；
- tokens/s；
- chunk count；
- average chunk gap；
- max chunk gap；
- stream interruption rate；
- first-byte/first-token failure；
- complete response success rate。

示例报告：

```text
Concurrent: 50
TTFT P50: 620ms
TTFT P95: 1.21s
Latency P95: 8.4s
Output speed: 42.6 tokens/s
Success rate: 99.3%
Stream interruption: 0.7%
```

---

# 90. Performance SLA

用户配置：

```text
Error Rate < 1%
P95 < 500ms
RPS > 200
TTFT P95 < 1.5s
```

确定性判断：

```text
P95 430ms      PASS
Error 0.4%     PASS
RPS 215        PASS
TTFT 1.8s      FAIL
```

AI 分析不能覆盖 SLA 结果。

---

# 91. Performance Cleanup

模式：
- NONE；
- RESOURCE_CLEANUP；
- BATCH_CLEANUP。

大规模数据推荐：
- run_id 标识；
- SQL Batch Cleanup；
- 专用 cleanup API。

---

# 92. Performance Warm Up

支持：
- warmup duration；
- warmup 请求不进入最终统计。

---

# 93. Run Comparison

选择两个 Run：

| 指标 | Baseline | Current | Change |
|---|---:|---:|---:|
| P95 | 850ms | 620ms | -27% |
| RPS | 230 | 310 | +35% |
| Error | 1.2% | 0.3% | -75% |
| TTFT | 1.4s | 0.9s | -36% |

用于性能回归。

---

# 94. Prometheus [V1]

监控：
- CPU；
- Memory；
- Load；
- 业务自定义指标。

性能报告可把时间序列对齐。

---

# 95. AI Performance Analysis

输入：
- test config；
- metrics；
- time series；
- error distribution；
- SLA；
- baseline；
- server metrics（若有）。

输出：
- summary；
- bottleneck candidates；
- evidence；
- regression/improvement；
- recommendations。

必须注明：
> AI 为分析建议，原始指标与 SLA 是最终确定性数据。

---

# 96. 前端目录结构建议

```text
frontend/
├── src/
│   ├── api/
│   ├── assets/
│   ├── components/
│   │   ├── common/
│   │   ├── editor/
│   │   ├── runner/
│   │   ├── report/
│   │   └── ai/
│   ├── layouts/
│   ├── router/
│   ├── stores/
│   ├── composables/
│   ├── utils/
│   ├── types/
│   └── views/
│       ├── auth/
│       ├── dashboard/
│       ├── projects/
│       ├── requirements/
│       ├── api/
│       ├── web/
│       ├── scenarios/
│       ├── runs/
│       ├── reports/
│       ├── runners/
│       ├── models/
│       ├── prompts/
│       ├── mcp/
│       └── settings/
```

---

# 97. Runner 目录结构建议

```text
runner/
├── runner/
│   ├── main.py
│   ├── config.py
│   ├── registration/
│   ├── heartbeat/
│   ├── dispatcher/
│   ├── runtime/
│   │   ├── context.py
│   │   ├── variables.py
│   │   ├── artifacts.py
│   │   └── cleanup.py
│   ├── executors/
│   │   ├── base.py
│   │   ├── api_executor.py
│   │   ├── web_executor.py
│   │   ├── sql_executor.py
│   │   ├── script_executor.py
│   │   ├── agent_executor.py
│   │   └── performance/
│   ├── capabilities/
│   ├── protocol/
│   └── logging/
├── tests/
└── pyproject.toml
```

Executor 接口：

```python
class BaseExecutor:
    async def prepare(self, task, context): ...
    async def execute(self, node, context): ...
    async def cancel(self): ...
    async def cleanup(self, context): ...
    async def collect_artifacts(self): ...
```

---

# 98. 后端 API 分组建议

```text
/api/v1/auth
/api/v1/users
/api/v1/projects
/api/v1/projects/{id}/members

/api/v1/requirements
/api/v1/requirement-versions

/api/v1/api-definitions
/api/v1/api-cases
/api/v1/web-cases
/api/v1/elements

/api/v1/scenarios
/api/v1/environments
/api/v1/variables
/api/v1/secrets
/api/v1/database-connections

/api/v1/runs
/api/v1/runs/{id}/events
/api/v1/runs/{id}/cancel
/api/v1/runs/{id}/force-stop
/api/v1/runs/{id}/rerun

/api/v1/artifacts
/api/v1/reports

/api/v1/runners
/api/v1/runners/register
/api/v1/runners/heartbeat

/api/v1/models
/api/v1/prompts
/api/v1/prompt-versions
/api/v1/ai/calls
/api/v1/mcp

/api/v1/performance [V1]
/api/v1/test-plans [V1]
/api/v1/schedules [V1]
```

---

# 99. 数据库核心表清单

V1 建议至少：

### 用户权限
- users
- roles
- user_roles
- project_members

### 项目
- projects
- environments
- environment_variables
- secrets
- database_connections

### Requirement
- requirements
- requirement_versions
- requirement_case_links

### API
- api_folders
- api_definitions
- api_cases
- api_case_versions

### Web
- web_cases
- web_case_versions
- web_pages
- web_elements
- element_locators
- locator_healing_proposals
- session_profiles

### Scenario
- scenarios
- scenario_versions
- scenario_nodes（可选，若 DSL JSON 全存版本表则不必单独）
- data_sources

### Run
- test_runs
- case_runs
- step_runs
- runtime_snapshots
- resources
- artifacts
- reports

### Runner
- runners
- runner_tokens
- runner_tags
- runner_capabilities
- runner_slots

### AI
- model_provider_connections
- model_configs
- model_bindings
- prompts
- prompt_versions
- ai_calls
- ai_outputs
- mcp_servers
- mcp_tools

### 其他
- notifications
- audit_events（V1 可建表，V2 UI）
- defect_drafts

---

# 100. 核心索引建议

必须重点索引：
- project_id；
- status；
- created_at；
- run_code；
- run_id；
- case_id；
- case_version_id；
- requirement_id；
- current_version_id；
- runner_id；
- last_heartbeat；
- ai_calls.task_type；
- artifacts.run_id；
- resource.run_id。

避免大 JSON 字段参与频繁查询过滤。

---

# 101. 版本号策略

### Requirement
`REQ-001 V1/V2`

### Case
`TC-001 V1/V2`

### Prompt
`PROMPT_API_CASE V1/V2`

### Scenario
建议也版本化：
`SCN-001 V1/V2`

### Run
保存所有实际版本 ID，不保存“当前版本”引用作为唯一依据。

---

# 102. 前端关键页面规格

## 102.1 Requirement 页面
左：Tree  
中：Markdown  
右：AI Review / Case Coverage

按钮：
- Import；
- Add；
- Edit；
- New Version；
- AI Review；
- Generate Case；
- View Diff；
- Impact。

## 102.2 Swagger 导入
步骤：
1. Upload；
2. Parse Preview；
3. Diff with existing；
4. Import；
5. AI Generate Cases；
6. AI Recommend Scenarios；
7. Review。

## 102.3 API Case
Tabs：
- Basic；
- Pre；
- Request；
- Post；
- Assertions；
- Data；
- Cleanup；
- Versions；
- AI Source。

## 102.4 Scenario
主体：
- Step List；
- Node Drawer；
- Runtime Variables；
- Debug Console。

顶部：
- Save；
- Validate；
- Debug；
- Run。

## 102.5 Web Case
Tabs：
- Natural Steps；
- Structured Steps；
- Elements；
- Assertions；
- Parameters；
- Evidence / History。

按钮：
- AI Structure；
- Record；
- Debug；
- Run。

## 102.6 Runner Center
表格：
- Runner；
- Online；
- OS；
- Capability；
- Slot；
- Tag；
- Last Heartbeat。

详情：
- Environment Check；
- Running Tasks；
- Logs；
- Version。

## 102.7 Run Detail
左：
- Case/Step tree。

中：
- realtime console。

右：
- context；
- evidence；
- AI analysis。

---

# 103. Scenario 编辑交互要求

V1 列表编辑器要做到：
- 拖拽排序；
- Add Step；
- Duplicate；
- Delete；
- Enable/Disable；
- IF 子节点；
- LOOP 子节点；
- Collapse；
- Error Validation；
- Variable autocomplete；
- Node test；
- Run to here；
- Run from here。

保存前 Validator：
- 未定义变量；
- 空 URL；
- IF 没有条件；
- Loop 非法；
- Cleanup 引用不存在；
- SQL connection missing；
- Secret missing；
- 循环深度异常。

---

# 104. 执行前 Validate

任何 Run 创建前先校验：

- Case Version 存在；
- Environment 有效；
- Secret 可解析；
- Runner online；
- Runner capability；
- Runner tags；
- Slot；
- Data Source 可读取；
- DB connection；
- Scenario DSL；
- 未定义变量；
- Browser；
- required file；
- AI model（若需要）。

Validation Failure 不进入 RabbitMQ。

Phase 4.4A 实现项目与环境、Case/Scenario 版本、Scenario DSL、Data Source、Secret、Database Connection、Runner 启用状态与 credential 摘要存在性、Redis 在线状态、Capability、Tag 和 Slot 等创建前校验。Phase 4.4B 进一步让当前受限 API Executor 的 `/runs/validate`、Run 创建和 execution-plan 共用同一能力判定：Runtime 模板、非 NONE Auth、Cookie、敏感 Header/Query、Pre/Post Action、Extractor、Data Source、Cleanup、AI Assertion、Browser/Web、required file/model 等尚不可执行配置必须在创建前以结构化安全问题 fail closed；校验失败不写 Run。普通非敏感 Header/Query/Body 与确定性断言保持可执行。Phase 4.5A 在 dispatch 前再次检查 Runner 在线、启用状态、Capability、Tag 和 Slot；失败不创建 Outbox、不推进 `QUEUED`。其他 Executor 上线时必须扩展同一共享判定，不能重新形成 Validate 与执行协议漂移。

---

# 105. 日志规范

统一结构化日志：

```json
{
  "timestamp": "...",
  "level": "INFO",
  "run_id": "...",
  "case_run_id": "...",
  "step_run_id": "...",
  "runner_id": "...",
  "event": "HTTP_REQUEST",
  "message": "...",
  "metadata": {}
}
```

禁止只用大量 `print()`。

Secret Mask：
- Authorization；
- Cookie；
- password；
- token；
- api_key；
- custom secret variables。

---

# 106. 错误码分类

平台级：
- VALIDATION_ERROR；
- AUTH_ERROR；
- PERMISSION_DENIED；
- RUNNER_UNAVAILABLE；
- RUNNER_CAPABILITY_MISSING；
- QUEUE_ERROR；
- STORAGE_ERROR；
- AI_PROVIDER_ERROR；
- AI_SCHEMA_ERROR；
- EXECUTOR_ERROR；
- CLEANUP_ERROR。

API:
- HTTP_ERROR；
- EXTRACT_ERROR；
- ASSERTION_ERROR；
- SQL_ERROR；
- SCRIPT_ERROR。

Web:
- LOCATOR_ERROR；
- TIMEOUT；
- NAVIGATION_ERROR；
- ASSERTION_ERROR；
- SESSION_ERROR；
- BROWSER_ERROR。

Phase 4.5A 的 dispatch/claim 结构化日志只记录 `run_id`、`runner_id`、`message_id`、状态、尝试次数和事件，不记录 Secret、credential、完整执行请求或数据库密码；publisher/Outbox 错误使用有界脱敏文本。任务信封只允许协议定义的字段，禁止通过额外字段携带敏感数据。

---

# 107. 安全设计

V1 必须：
- JWT/Session Auth；
- 密码 Hash；
- Secret 加密；
- RBAC；
- API Key 不明文返回；
- 日志脱敏；
- Runner Token 一次展示；
- Runner credential 可撤销；
- SQL Connection 密码加密；
- Script Timeout；
- 文件上传限制；
- MinIO private bucket；
- Download 走授权 API / presigned URL。

---

# 108. Docker Compose 部署

服务器：

```text
frontend/nginx
backend
mysql
redis
rabbitmq
minio
```

Runner V1 不放服务器 Compose，Windows 单独运行。

建议：

```text
docker-compose.yml
.env
```

`.env` 只放部署配置，不把真实 Secret 提交 Git。

---

# 109. 本地开发模式

开发者 Windows：

```text
Vue
FastAPI
MySQL
Redis
RabbitMQ
MinIO
Runner
Chrome
```

中间件可 Docker。

推荐 `docker-compose.dev.yml` 启动：
- MySQL；
- Redis；
- RabbitMQ；
- MinIO。

前端/后端本机热更新。

---

# 110. V1 开发顺序

绝对不要同时开发全部页面。

## Phase 0：工程骨架
- repo；
- backend；
- frontend；
- Compose；
- DB；
- Alembic；
- auth；
- layout；
- error/log。

验收：
- 登录；
- health；
- migration；
- frontend 调 backend。

## Phase 1：Project / Environment / Secret
- project；
- member；
- environment；
- variable；
- secret；
- MySQL connection。

验收：
- 创建项目；
- 切环境；
- secret 不回显；
- DB test connection。

## Phase 2：Requirement / Swagger / AI Center 基础
- Markdown；
- Requirement tree；
- Version；
- Swagger；
- Model Center；
- Prompt Center；
- AI Call；
- structured output。

验收：
- Markdown → Requirement；
- Swagger → API Definition；
- AI Review；
- AI Generate Case。

## Phase 3：API Case / Scenario
- Case；
- Pre/Post；
- assertion；
- extraction；
- variable；
- IF；
- LOOP；
- WAIT；
- SQL；
- Python；
- Cleanup；
- data-driven。

验收：
完成真实：
```text
login → token → create → order_id → query → cleanup
```

## Phase 4：Runner
- register；
- heartbeat；
- capability；
- tags；
- slots；
- MQ；
- Redis；
- run state；
- console；
- cancel；
- timeout。

验收：
平台创建 Run → Runner 执行 → 日志实时显示。

## Phase 5：Web
- element；
- locator；
- Web Case；
- Playwright；
- evidence；
- recording；
- AI cleanup；
- auth state；
- self-healing。

验收：
录制 → AI 整理 → 保存 → 执行 → Locator 变化 → 自愈建议 → 人工接受。

## Phase 6：Report / Dashboard / Polish
- report；
- defect；
- dashboard；
- archive；
- version trace。

验收：
完整演示闭环。

---

# 111. V1 演示用 Demo System 建议

为了面试时稳定展示，建议项目自带一个被测 Demo Web/API 系统或固定使用一个可控测试系统。

必须包含：
- 登录；
- 用户；
- 商品；
- 订单；
- 查询；
- 删除；
- 可故意改变 Locator；
- SSE/LLM Mock（V1）。

这样不依赖外部服务是否稳定。

---

# 112. V1 最终验收场景

## 场景 A：Requirement → Case
1. 导入 Markdown；
2. Requirement Tree；
3. AI Review；
4. AI Generate Cases；
5. 批量审核；
6. 单条编辑；
7. Approve；
8. Requirement ↔ Case 关联可查看。

## 场景 B：Swagger → API
1. 上传 Swagger；
2. 生成 API Definition；
3. AI Case；
4. AI Scenario 推荐；
5. 人工审核；
6. Scenario 保存。

## 场景 C：API 链路
1. 登录；
2. 提取 Token；
3. 创建资源；
4. 提取 ID；
5. IF；
6. SQL Verify；
7. AI Assertion；
8. Cleanup；
9. Report。

## 场景 D：Web
1. Start Recording；
2. 手工登录；
3. AI 整理；
4. 保存 Web Case；
5. Runner Chrome 执行；
6. 保存 Screenshot/Trace；
7. 修改 DOM/Locator；
8. 原 Locator fail；
9. Self Healing；
10. 人工 Accept 新 Locator。

## 场景 E：Runner
1. 注册；
2. 心跳；
3. capability；
4. tags；
5. slots；
6. Run；
7. realtime log；
8. cancel/timeout。

---

# 113. V1 增强能力开发顺序

1. Test Plan；
2. Scheduler；
3. CI API；
4. JMeter Executor；
5. Streaming Executor；
6. Performance Metrics；
7. SLA；
8. Warm Up；
9. Run Comparison；
10. AI Performance Analysis；
11. Prometheus；
12. Flow Editor。

---

# 114. V2 开发顺序

1. Knowledge Base；
2. Chunk/Embedding/Retrieval；
3. Feedback Dataset；
4. AI Evaluation；
5. Prompt/Model Experiment；
6. AI Impact Analysis；
7. Case Version UI；
8. Audit Center；
9. Linux Runner；
10. 独立 Web Agent。

---

# 115. 面试讲解重点

不要按“我做了 20 个菜单”讲。

建议重点讲 6 个技术点：

## 1. AI 测试闭环
Requirement → AI → Human Review → Execution → Evidence → Feedback。

## 2. API Scenario DSL
前端无代码编排 IF/LOOP/SQL/Script/Extract/Cleanup，Runner 解释执行。

## 3. 测试数据生命周期
Runtime Context + Resource Registry + LIFO Cleanup。

## 4. Web 双执行思想
结构化 Playwright 稳定执行 + AI/Vision 自愈，正式修改人工确认。

## 5. Runner 调度
RabbitMQ + Redis + Slot + Capability + realtime console。

## 6. AI 可观测性
Model/Prompt Version + Structured Output + Token/Cost + Evaluation。

---

# 116. 简历可提炼方向（完成后再根据实际实现修改）

示例，不建议在未完成前直接使用：

> 设计并开发 AI 原生智能测试平台，基于 FastAPI、Vue3、RabbitMQ、Redis、MySQL 与 MinIO 搭建测试资产、任务调度与执行证据体系；自研 Windows Runner 统一执行 API、Playwright Web、SQL 与脚本任务，并实现 Runner 心跳、能力检测、标签路由、并发槽位和实时日志。

> 设计 API Scenario DSL，将接口关联、JSONPath 提取、运行时变量、IF/LOOP、SQL、Python Script、数据驱动与 Cleanup 封装为前端可视化配置，支持跨接口参数传递和 Resource Registry 测试数据生命周期管理。

> 构建 AI 测试能力层，实现 Model/Prompt 统一管理、Prompt 版本化、结构化输出、模型 fallback、Token/成本统计；支持需求评审、Swagger 用例生成、AI 语义断言和失败分析。

> 基于 Playwright 实现 Web 录制与结构化自动化，结合多 Locator、DOM、LLM、Vision 与 Agent 设计定位器自愈链路，并通过人工确认机制控制正式测试资产变更。

只有实际完成后才将相应能力写入简历。

---

# 117. Scope Lock

从此文档生效开始：

## In Scope
严格以 V1 / V2 标注为准。

## Future
只有进入对应版本后才实施。

## Out of Scope
不允许编码助手自行实现。

若开发过程中出现新想法：

```text
提出需求
  ↓
判断是否必要
  ↓
更新本规格
  ↓
更新 Version Scope
  ↓
再编码
```

---

# 118. 开发过程中的 Definition of Done

任何功能只有同时满足以下条件才算完成：

1. 数据库 Migration；
2. Backend API；
3. Pydantic Validation；
4. 权限；
5. Frontend；
6. Loading/Error/Empty State；
7. 日志；
8. 单元或核心集成测试；
9. 文档；
10. Demo 验证。

Runner 功能额外：
11. Cancel；
12. Timeout；
13. Retry；
14. Evidence；
15. Secret Mask。

AI 功能额外：
16. Prompt Version；
17. Model；
18. Structured Output；
19. Raw Output；
20. AI Call Log；
21. Error/Fallback。

---

# 119. 开发助手执行约束

给 Codex/Claude Code 的总原则：

1. 不得修改 Scope；
2. 不得跳版本开发；
3. 不得引入未批准的重大框架；
4. 不得直接把业务逻辑写在 Router；
5. 不得绕过 Alembic 直接手工改生产 DB；
6. 不得把 Secret 输出到日志；
7. 不得把 AI Prompt 硬编码在 Service；
8. 不得让 LLM 替代确定性 IF/变量/断言；
9. 不得自动永久修改 Case/Locator；
10. 每个模块开发完成需附迁移、API、测试和使用说明。

---

# 120. 最终架构摘要

```text
                          ┌─────────────────────┐
                          │      Vue 3 UI       │
                          │ Workbench / Editor  │
                          └─────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │        FastAPI         │
                       │                        │
                       │ Asset / Run / AI / RBAC│
                       └──────┬──────┬──────┬──┘
                              │      │      │
                    ┌─────────┘      │      └──────────┐
                    ▼                ▼                 ▼
                  MySQL            Redis            MinIO
             持久化/版本/结果     实时状态          Evidence
                                     │
                                     ▼
                                 RabbitMQ
                                     │
                                     ▼
                            ┌─────────────────┐
                            │ Windows Runner  │
                            ├─────────────────┤
                            │ API Executor    │
                            │ Web Executor    │
                            │ SQL Executor    │
                            │ Script Executor │
                            │ Agent Executor  │
                            │ Perf [V1]       │
                            └────────┬────────┘
                                     │
                  ┌──────────────────┼─────────────────┐
                  ▼                  ▼                 ▼
                API                Chrome             DB
                                     │
                                Playwright
```

AI：

```text
Requirement / API / Web / Run
             │
             ▼
         AI Service
             │
     ┌───────┼────────┐
     ▼       ▼        ▼
   Prompt   Model    MCP/Vision
   Center   Center
     │       │
     └── Structured Output
             │
          AI Call Log
             │
      Human Review / Feedback
             │
       Evaluation Center [V2]
```

---

# 121. 最终产品核心卖点

如果整个规划最终实现，最值得突出的是：

1. **AI 测试全生命周期闭环**  
   从 Requirement/Swagger 到 Case、执行、失败分析、人工反馈。

2. **API 可视化高级编排**  
   前端封装接口关联、变量、IF、LOOP、SQL、Script、断言与 Cleanup。

3. **测试数据生命周期管理**  
   Runtime Context + Resource Registry + LIFO Cleanup，解决自动化产生脏数据的问题。

4. **Web 录制 + 多层自愈**  
   Playwright Recorder + AI 整理 + Locator/DOM/LLM/Vision/Agent 自愈链路。

5. **统一 Runner 与任务调度**  
   RabbitMQ、Redis、Capability、Tag、Slot、实时执行控制台。

6. **双层断言体系**  
   确定性断言负责稳定判断，AI 负责复杂业务语义。

7. **LLM/SSE 性能测试**  
   TTFT、Tokens/s、Chunk、流中断、SLA 和 Run 对比。

8. **AI 可评测与可追溯**  
   Model、Prompt、版本、Token、成本、原始结果、人工修改、Evaluation。

---

# 122. 结论

该项目应被开发成一个**深度优先而非功能数量优先**的求职型测试开发平台。

第一优先级不是“支持多少种测试”，而是把以下链路真正跑通：

```text
Requirement / Swagger
      ↓
AI 评审与用例生成
      ↓
人工确认
      ↓
API Scenario / Web Case
      ↓
Runner
      ↓
确定性执行
      ↓
AI 辅助
      ↓
Evidence
      ↓
Report
```

V1 只要上述闭环稳定、界面专业、任务执行真实、证据完整，就已经具有很强的面试展示价值。

V1 同时通过性能测试、LLM/SSE 指标、CI/CD 与 Test Plan 补强传统测试开发能力。

V2 最后通过 RAG、Feedback Dataset、AI Evaluation 与需求影响分析，把平台从“使用 AI 的自动化测试平台”进一步升级成“**能够持续评测和优化自身 AI 测试能力的 AI 原生测试平台**”。

---

# 附录 A：外部参考

- BrickCoreTest GitHub：`https://github.com/banzhuan-Ke/BrickCoreTest`
- BrickCore 当前公开 README 中可参考的方向：FastAPI + Vue3、一体化 Web/API/性能/AI、Web 录制与定位器自愈、Swagger、SSE/性能、RBAC、Runner、RabbitMQ/Redis/MinIO。
- 本项目仅借鉴架构思想与公开功能方向，不直接依赖 BrickCoreRunner。

# 附录 B：项目总原则速查

```text
AI 做理解，程序做执行。
AI 给建议，人确认资产。
变量靠 Runtime Context，不靠 AI 猜。
关联靠 Extract + Template。
测试必须 Cleanup。
Runner 是执行核心。
MQ 排队，Redis 看现在，MySQL 记历史，MinIO 存证据。
自动 Retry 只到 Step，一次。
Chrome V1。
Windows Runner V1。
API + Web + Performance V1。
RAG/Evaluation V2。
App 不做。
Scope 不擅自扩。
```
