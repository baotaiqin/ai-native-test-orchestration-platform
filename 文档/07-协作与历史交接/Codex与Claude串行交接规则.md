# Codex 与 Claude 串行交接规则

> Claude 是用户手动触发的备用 Coding Agent，不是 Sol 可调度的 Luna。  
> 本规则不授权当前或未来任何 Agent 自动启用 Claude。
> 当前 Claude 入口为用户在 VSCode 中安装的 Claude 插件，直接操作同一项目工作区。

---

## 1. 状态模型

```text
NORMAL_CODEX
    │ 用户明确要求“准备切 Claude”
    ▼
PREPARING_CLAUDE_HANDOFF
    │ Sol 完成交接资料，Codex/Luna 停止相关文件写入
    ▼
CLAUDE_ACTIVE
    │ Claude 完成并停止；用户明确要求返回 Codex
    ▼
CODEX_REVIEW
    │ Sol 验收、修复或交给原 Luna 修复
    ▼
NORMAL_CODEX
```

除用户明确指令外，不允许任何自动状态切换。

## 2. NORMAL_CODEX：正常开发

- Sol 可自主决定自己处理、分配给现有 Luna，或按 `AGENTS.md` 创建新的长期 Luna；
- Sol 不得调用、建议、询问或预先调度 Claude；
- `当前开发状态.md` 只按重要节点轻量更新；
- 长期 Luna 仍优先复用 AI-Test-Backend、AI-Test-Frontend、AI-Test-Runner、AI-Test-DevOps。

## 3. PREPARING_CLAUDE_HANDOFF：用户触发后的准备

仅当用户明确说出“准备切 Claude”“Codex Token 快没了，准备交接”“我要让 Claude 接着开发”“把当前工作交接给 Claude”“现在切到 Claude”或语义等价指令时进入。

Sol 必须按顺序完成：

1. 停止分配新的 Luna 开发任务；
2. 检查现有 Luna 状态，等待会修改同一文件的工作结束；
3. 以磁盘实际内容为准检查当前实现，不依赖聊天摘要猜测；
4. 更新 `当前开发状态.md`；
5. 创建或更新 `Claude本轮交接任务.md`；
6. 写明项目背景、当前任务、已完成内容、首先阅读的文件、重点源码和实现逻辑；
7. 写明允许修改的文件/模块、禁止修改范围、已知问题、测试命令和验收标准；
8. 生成一份可直接复制给 Claude 的本轮专用接手提示词；
9. 确认 VSCode 资源管理器打开的是项目根目录，且 Claude 读取的是根目录 `AGENTS.md`；
10. 明确宣布 Codex/Luna 已停止写入本轮 Claude 负责文件。

若 Luna 仍在修改重叠文件，不得宣布交接完成。

## 4. CLAUDE_ACTIVE：Claude 工作期间

- Codex、Sol、Luna 不得修改 `Claude本轮交接任务.md` 所列的 Claude 负责文件；
- Claude 通过 VSCode 插件直接编辑同一工作区，不创建项目副本；插件能力不扩大交接资料规定的权限；
- 采用串行协作，不使用 Git、branch、worktree 或 commit 交接；
- Claude 只能完成本轮交接资料明确的工作；
- Claude 不得扩大任务范围、大范围重构、改变核心架构、增加未经批准的生产依赖、删除已有功能或决定下一阶段任务；
- Claude 若发现任务边界外问题，只记录，不直接修改；
- Claude 不得执行全项目格式化、批量换行转换、自动 Git、修改无关 `.vscode`/`.idea` 配置或读取输出 Secret；
- 用户未明确要求返回 Codex 前，Sol 不进入验收或继续写入。

Claude 完成时必须创建或更新 `文档/07-协作与历史交接/Claude本轮交接结果.md`，至少包含：

1. 实际修改文件；
2. 每项主要修改；
3. 已执行测试及结果；
4. 未执行测试及原因；
5. 遗留问题、潜在风险和未完成部分；
6. 给 Sol 的下一步建议；
7. 明确结束语：“本轮 Claude 开发已结束，已停止继续修改，等待 Codex / Sol 接管。”

## 5. CODEX_REVIEW：用户要求返回后的接管

用户发送“Claude 完成，重新接管项目”或语义等价指令后，Sol 必须：

接管前必须先确认 VSCode 中 Claude 已结束本轮执行并停止继续编辑，然后：

1. 读取 `AGENTS.md`、`当前开发状态.md`、`Claude本轮交接任务.md` 和 `文档/07-协作与历史交接/Claude本轮交接结果.md`；
2. 检查 Claude 实际修改的源码，不依赖旧聊天记忆；
3. 对照任务边界检查是否存在无关修改、架构违规或依赖越界；
4. 运行风险相称的必要测试；
5. 决定接受、由 Sol 修复小问题、交给原 Luna 修复，或报告不可接受；
6. 更新 `当前开发状态.md` 和必要的项目进度文档；
7. 验收完成后恢复 NORMAL_CODEX。

即使 Claude 实现有问题，Sol 也不得自行再次调用 Claude；再次使用仍需用户明确授权。

## 6. 文件与备份规则

- `当前开发状态.md`：持续维护的最小恢复入口；
- `Claude本轮交接任务.md`：只在用户触发正式交接后由 Sol 创建/更新；
- `文档/07-协作与历史交接/Claude本轮交接结果.md`：由 Claude 完成本轮开发后创建/更新；
- `文档/07-协作与历史交接/Claude紧急接手提示词.md`：Codex 来不及生成本轮专用提示词时的备用入口；
- `文档/07-协作与历史交接/Claude完成后返回Codex提示词.md`：用户返回 Codex 时可直接复制；
- 重要或高风险修改前按现有本地备份规则处理，不通过 Git 绕过本规则；
- 交接文档禁止记录任何 Secret。
