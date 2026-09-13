# V1-W10 多标签页 Runner 交付

## 状态

- 编号 / 修订：`V1-W10 / r1`
- 日期：2026-09-10
- 结论：**已实现，基本检查通过，待集中验收**
- 机器证据：`.codex-validation/v1-w10/result.json`
- 正式服务：未访问、未加载

## 冻结接口

保持 `schema_version=1` 和既有动作七字段
`type/timeout_ms/failure_policy/url/locator/value/key`，没有修改 models 或增加字段。

- `NEW_TAB`：url 为既有 http(s) URL，value 为标签别名，locator/key 为 null。
- `SWITCH_TAB`、`CLOSE_TAB`：仅 value 为标签别名，url/locator/key 为 null。
- 别名满足 `[A-Za-z][A-Za-z0-9_-]{0,63}`；初始页固定为 `main`，NEW_TAB 不允许
  使用 `main`。

非法别名、缺字段、额外语义字段继续由协议固定拒绝。重复存活别名、未知或已关闭别名、
超过 16 个受管存活页及关闭最后一个受管页均为安全的固定运行错误，不使用浏览器索引
猜测目标。

## 实现

执行器新增仅限单次 Web 执行的 `_ManagedTabs`：

- 只登记初始 `main` 和显式 NEW_TAB 创建的页面；不遍历 `context.pages` 自动接管业务
  popup，也不触碰用户外部浏览器。
- NEW_TAB 创建后立即登记并成为当前页，再执行导航；因此导航成功或失败后状态都明确。
- SWITCH_TAB 显式 bring-to-front 后更新当前别名。
- CLOSE_TAB 关闭非当前页不改变当前；关闭当前页时优先回到仍存活的 main，否则选择
  最近激活且仍存活的受管页。
- 每个后续 action/assertion 都重新读取受管当前页，定位、导航和失败 healing 不再固定
  使用初始 page。
- 新受管页接入既有 Console/Network Evidence 监听；最终摘要、截图和失败 Evidence 使用
  最终当前页，并记录受管存活页数量。
- 浏览器 context 仍是唯一资源所有者，结束时统一关闭其页面；旧动作、预算、取消、session
  和 W4 planned/observed 隐私逻辑未改语义。

安全错误不包含别名对应 URL、原始浏览器错误或 DOM 内容。

## 基本检查

协议、状态机及唯一有界真实 Chrome 流程：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -o addopts='' `
  runner/tests/test_v1_web_tabs.py -q
```

结果：`15 passed in 3.31s`。

真实 Chrome `152.0.7977.83` / Playwright `1.62.0` 流程完成 16 个动作和 3 个断言：
NEW_TAB、当前页上的 FILL/CLICK、业务 popup、SWITCH_TAB、关闭非当前页、关闭当前页回 main、
关闭 main 后保持 alpha，以及最终 alpha 上的 value/text/url 断言全部成功。最终受管当前页
为 alpha；业务 popup 仍在同一 context 中但未进入受管映射。Evidence 的 final URL/title
均来自 alpha。context 关闭后剩余 alpha 与业务 popup 均已关闭。

状态机基础检查另覆盖：重复存活别名、未知/已关闭别名、16 页上限、禁止关闭最后一页、
main 已关闭时的最近存活页回退，以及 NEW_TAB 导航失败后仍保留新当前页。

相关 Ruff 与编译：

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check `
  runner/runner/protocol.py runner/runner/executors/web.py runner/tests/test_v1_web_tabs.py
& '.\.venv\Scripts\python.exe' -m py_compile `
  runner/runner/protocol.py runner/runner/executors/web.py runner/tests/test_v1_web_tabs.py
```

结果均通过。首次编译/lint 在测试执行前发现一处误置的 `urlsplit` import，修复后协议可
正常导入；测试侧 lambda 与空行 lint 随后修正。最终上方命令全部为退出码 0。

## 文件与指纹

- `runner/runner/protocol.py`  
  `2418BF252126780C6CCB85C49ACB8F9ED19FAEBE24EB07E4A3635D5746269720`
- `runner/runner/executors/web.py`  
  `81A913FD4D10D0E1D1E26EDEC8EDCC7EDB2F9B8238C4C984DD9427A3AD93EB9D`
- `runner/runner/models.py`（未修改）  
  `F33405453276D19BAC96748BBF69AA93DF55BFCC48B75E40A2DBD67C0880A16A`
- `runner/tests/test_v1_web_tabs.py`  
  `B69215A91B9F70AD145E517C783E78DA24A287B8D295F21413F8FAFBDBCDD449`
- `runner/pyproject.toml`（未修改）  
  `0F9CDF53E6454267AC2D7185197DDB9916730619B0EA508E498ACF829F23EE08`

## 待集中验收

按当前“功能优先、集中验收后置”规则，本包未运行 Runner 全量、W1/W4 浏览器矩阵、
截图审计矩阵或正式环境联调。这些不属于本次基本检查通过的含义，后续由主控纳入集中
验收。

未修改 Backend、Frontend、Worker、队列、credential 或全局文档；未使用正式网络、
数据库、AI、Git、Claude、内部 Agent 或跨任务消息。
