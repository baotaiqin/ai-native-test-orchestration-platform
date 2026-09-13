# P5-F Runner 真实 Chrome 异常矩阵记录

> 执行日期：2026-09-09  
> 执行任务：`AI-Test-Runner_sol`  
> 范围：第二轮 P5-E/P5-F Runner 真实 Chrome、spawn 隔离、控制与 Evidence 隐私矩阵

## 1. 边界与环境

- 测试只启动自己的 `ThreadingHTTPServer`，绑定 `127.0.0.1` 随机端口；未访问共享 demo `8765`，未启动正式 Worker，未连接 RabbitMQ、MinIO 或业务数据库。
- 每个用例使用独立 Chrome Context、随机合成敏感标记和独立临时目录；记录与命令输出不包含标记值、Cookie 值或 Storage State 内容。
- Force Stop 只通过 Windows Toolhelp 跟踪本轮隔离 Python PID 的后代，并比较本轮新建的 `playwright_chromiumdev_profile-*` 目录；未终止、修改或统计用户其他 Chrome 会话。

## 2. 真实矩阵结果

| 验证项 | 实际结果 | 结论 |
|---|---|---|
| Chrome headless | 真实 Chrome 启动、页面动作、断言成功 | 通过 |
| Chrome headed | 真实有头 Chrome 启动、页面动作、断言成功 | 通过 |
| 确定性多 Locator | 第一候选不存在时记录 `NOT_FOUND`，第二候选成功；顺序与优先级稳定 | 通过 |
| 延迟 Locator 共享节点预算 | 单候选延迟 1.5 秒、多候选分别延迟 2.5/3.5 秒，均在 4 秒节点预算内命中；实际 Click 只执行一次 | 通过 |
| 全 Locator 缺失与 Healing | 有 `element_version_id` 且 Run 总预算尚在时，真实输出 `FAILED / WEB_LOCATOR_NOT_FOUND`、全 `NOT_FOUND` 及安全 Healing Context；直接通过后端 `WebExecutionTrace.model_validate` | 通过 |
| Locator 未完整探测 | 10 个版本化候选在 1 秒节点预算内只能完成部分实际探测时，输出 `TIMEOUT / WEB_NODE_TIMEOUT`；只报告已尝试项，不声称“全部 NOT_FOUND”且不携带 Healing Context，后端模型校验通过 | 通过 |
| 有效 Session StorageState | 仅使用随机合成 Cookie，真实 Chrome Context 成功复用会话 | 通过 |
| Cancel | 真实 spawn 子进程已进入浏览器请求后发出 cancel event，有界等待后返回 `CANCELLED / CANCEL_REQUESTED` | 通过 |
| Force Stop | 真实 spawn 子进程已拥有 Chrome 后代后强制终止；隔离进程、本轮 Chrome 后代和专属 profile 目录在 10 秒窗口内全部消失 | 通过 |
| Run 总 Timeout | 1 秒总预算下的 3 秒页面请求有界收口为 `TIMEOUT`，未继续执行后续动作 | 通过 |
| 单 Web Slot | `1 -> 0`后第二次获取被拒，隔离执行结束后恢复为 `1` | 通过 |
| spawn / TempDir 清理 | 正常、Cancel、Force Stop、Timeout 路径均无存活的本轮隔离进程；专属 Evidence/profile 临时目录已删除 | 通过 |
| Evidence 隐私 | 真实产生 Screenshot、Playwright Trace、Console Error、Web Summary；合成敏感标记泄漏类别数为 0，Trace 安全审计通过 | 通过 |
| Screenshot 视觉遮罩 | 直接解码真实 PNG，输入与 `data-sensitive` 区域的 Playwright 紫红遮罩像素安全计数不少于 1000；未以“PNG 不含明文字节”替代视觉验证 | 通过 |

## 3. 实测发现并修复的缺陷

1. **Locator 候选吞掉节点预算**
   - 修复前，headed/headless 真实 Chrome 均在第一个不存在候选上耗尽整个节点预算，结果为 2 failed（均返回 `TIMEOUT`）。
   - `runner/runner/executors/web.py` 现使用共同 deadline 下的有界就绪轮询；仅重试无副作用的 Locator 就绪检查，已就绪候选的实际动作最多执行一次。
   - 版本化 Locator 的纯 `NOT_FOUND` 与 Run 总超时、实际动作超时已分类，不会把 Healing Context 挂到后端禁止的 `TIMEOUT` trace 上。
2. **Console Evidence 可回显已知 Web 输入值**
   - 修复前，非通用敏感字段名格式的控制台消息可回显 FILL 合成标记；真实用例只报告泄漏类别为 `CONSOLE_ERROR`，没有输出标记值。
   - Console Error 现在除通用凭证模式外，还使用本次计划中已解析的 FILL/SELECT 值进行精确脱敏。修复后 Screenshot/Trace/Console/Summary 四类证据的合成标记泄漏类别数为 0。
3. **Windows Force Stop 可遗留 Playwright/Chrome 后代**
   - 最终重复矩阵曾真实观察到：只终止隔离 Python 根进程时，本轮 Playwright/Chrome 后代可在 10 秒窗口后仍存活。该次观察未终止用户其他 Chrome。
   - `runner/runner/isolation.py` 在 Windows 上现对已确认仍存活的本次 spawn 根 PID 执行 `taskkill.exe /PID <pid> /T /F`，命令不经 shell，只定位该任务进程树；调用不可用时才回退原有 terminate。
   - 修复后 Force Stop 真实用例连续执行 3 次均通过，每次的本轮后代 PID 与新建 Playwright profile 目录均在有界窗口内归零。

## 4. 明确未通过/未实现

- **Session 过期自动恢复仍未实现**：当 Storage State 失效时，“自动执行登录流程 -> 更新 Storage State -> 原动作仅重试一次”仍是 V1 缺项。本轮只证明“有效 Session StorageState 复用”，不得将其替代、删除或弱化为过期恢复验收。
- 本轮未运行正式 RabbitMQ/MinIO/Worker/共享 demo/业务数据库链路，因此不以本矩阵替代 Backend 负责的 P5-E 正式全链路验收。
- 对原始 Playwright Trace 出现无法安全清洗的敏感结构时，Runner 仍按既有 fail-closed 规则丢弃该 Trace，不会为保证 Trace 存在而放宽审计。

## 5. 最终命令与结果

在 `runner/` 目录执行：

```powershell
..\.venv\Scripts\python.exe -m pytest tests/test_p5f_real_chrome_matrix.py -q
```

结果：**12 passed**，真实 Chrome 矩阵无 skip。

```powershell
..\.venv\Scripts\python.exe -m pytest tests
```

结果：**335 passed, 2 skipped in 86.37s**。两项 skip 为既有 Windows/测试账户 DPAPI 条件性用例，不是本轮真实 Chrome 矩阵。

```powershell
..\.venv\Scripts\python.exe -m ruff check --no-cache runner tests
```

结果：**All checks passed**。

## 6. 交付文件

- `runner/tests/test_p5f_real_chrome_matrix.py`
- `runner/runner/executors/web.py`
- `runner/runner/isolation.py`
- `文档/06-验收记录/P5FChrome异常矩阵记录.md`
