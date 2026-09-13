# Phase6 报告、导出与 Dashboard 本批验收

2026-09-10，主控完成本批审查。四个可见 Sol/xhigh 执行任务均已结束，没有在任务执行中追加普通审查消息；返工均在前一轮交付后集中派发。

## 本批通过

- P6-R1/r2：报告查询与真实 Dashboard 后端，420 项后端回归与主控 16 项检查。
- P6-U1/r3：报告目录/详情、Evidence Center、Dashboard，30 项真实 Chrome 受控检查与主控 5 项刷新/筛选竞态检查。
- P6-X1/r2：完整 Markdown/HTML 导出，437 项后端回归、主控 16 项、独立真实 MySQL 11 项一致快照/事务/连接清理验证。
- P6-X2/r2：显式下载、文件与错误处理、页面和实际登录身份绑定；24 项专项 Chrome、原 U1 30 项回归、主控 4 项真实下载/双标签页检查通过，类型检查与独立构建通过。
- P6-L1/r2：冻结 Backend/Worker 统一加载；32 项就绪、主控五进程身份及 19 源码指纹通过。原注册 Worker 保留，Frontend/Demo/中间件未重启。
- V1-T1/T2/T3 的重试 0/1 限制实现已验收并随本批加载；完整后续执行能力变化仍需重新验证相应门禁。

## 主控正式数据验收

主控 `.codex-validation/p6-controller-readonly.json` 为 PASSED：10 项检查、4 个受保护 Run × Markdown/HTML 共 8 份导出。核对状态、执行锁定版本、归档资产引用、Case/Step/Evidence、AI 引用及 Dashboard 实际数据库统计和时区。

主控 `.codex-validation/p6-controller-live-ui/result.json` 为 PASSED：13 项检查，真实 Chrome 正常登录后只读访问四个报告、按 Run 筛选 Evidence、Dashboard 与最近 Run 状态，实际下载两份文件并验证字节等于对应 HTTP 响应。没有业务写入、外部请求或页面脚本错误。首轮截图在表格状态标签完成绘制前采集，主控补充显式状态文字/可见性断言后重验通过，未修改产品。

截图和实际文件保存在 `.codex-validation/p6-controller-live-ui/`。HTML 仅保存，不在浏览器执行导出正文。下载哈希随报告生成观察时间变化，不将不同次导出哈希差异误判为历史改写。

前后正式资产、Project23、受保护历史及控制文件一致；历史 SHA256 保持 `1d4b2a85d48bc286db7475fb3ebc42eceb689d9a56095e66c3f1270a036dfc78`。既有 4 Run、19 Evidence、AI Call12..20、Proposal2/3、Analysis2 等未修改，未新建 Run、重投消息或调用 AI。

## 返工与环境边界

X2/r1 的 Pinia 身份可能落后于另一标签页修改的实际 localStorage；旧请求会下载，旧 401 还会清除新账号。r2 改为实际存储身份及代际校验，清除旧详情后重新授权加载，401 只处理仍属于当前身份的请求。主控独立复验四项通过。主控重跑后有三张自有截图哈希变化，产品、构建和执行任务证据均匹配；详情见 `p6-x2-controller-review.json`，不伪称所有旧截图哈希不变。

8765 存在加载前已经运行的未归属 `http.server` PID49364。完整 netstat 同时列原 Demo21264；PowerShell/CIM 单条视图不能证明端口独占。r2 的 48 个限定只读连接均由原 Demo21264接收，health正确、changed_locator=true。未清理额外进程，本批不声明独占8765；新 Demo 业务执行前须重新确认目标服务身份。

主控台账曾因管道编码错误截断，已从本任务历史创建/修改记录恢复并验证 22491 字符、42 节；产品及验收证据未受影响。恢复详情及指纹见台账。文档后续使用补丁或预编码后替换。

## 尚未完成

本批不等于 Phase6 或完整 V1 完成。下一批为需求版本影响与用例追溯，依据《Phase6需求影响与追溯实施契约》，尚未派发；缺陷草稿、正式认证、完整 API/Scenario/数据驱动与 AI、扩展 Web/登录态恢复、跨平台部署及最终 A～E 场景继续按《V1需求验收矩阵》推进。旧工作包 90% 不是完整产品覆盖率，不因本批通过直接宣称 V1 完成。
