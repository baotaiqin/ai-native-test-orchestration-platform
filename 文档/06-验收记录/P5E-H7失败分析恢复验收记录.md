# P5E-H7 / r1 失败分析恢复验收记录

2026-09-10，主控执行。H5代码修复和H6加载均已验收，四个执行任务已结束后，主控独占执行一次恢复命令。结果为 **PASSED / complete**；本次只补失败分析，没有重跑已成功的自愈、批准或浏览器流程。

## 前置与执行

- H5 Backend全量397项通过；主控另复跑32项相关测试，7项交付指纹一致。
- H6完成21项就绪检查，主控独立核对实际监听PID20860、创建时间晚于源码确认、健康响应与源码指纹，以及7项H6交付哈希。
- 主控正式数据库SELECT/SHOW只读重建失败来源，摘要与Call19完全一致；Prompt16保留真实action_1节点，未出现非法字面引用。正式GET-only恢复preflight通过，manifest/ledger字节不变。
- 执行前再次核对恢复脚本SHA256为 `9b387663be3b888e85e4cd7fdb1c446043c3545e5dd1407ba7808f5b6706187a`。

命令只执行一次，进程退出码0：

```powershell
.\.venv\Scripts\python.exe -B deploy/p5e_resume_failure_analysis.py --resume-after-call-19
```

## 实际结果与独立读取

| 项目 | 结果 |
|---|---|
| attempt | V1P5E_20260909_F294AC1E |
| 原失败Run / CaseRun | run_15c59b0b27964dd8a6fec2fcca840f9c / 28 |
| FailureAnalysis | 2，COMPLETED；正式历史total=1 |
| AiCallLog | 20，success=true，actual_model=qwen3.7-plus |
| PromptVersion / OutputSchema | 16 / 12，保持原绑定 |
| evidence_node_ids | [action_1] |
| repair / fallback / retry | false / false / 0 |
| P5-E累计业务AI调用 | 5，原上限6保持 |
| 恢复receipt | PASSED |

执行后主控通过正常认证及GET读取正式分析历史和AI调用记录：分析ID与manifest一致，新Call20来源摘要与Call19一致；原Call19的非法重复引用审计未改写。账本前四条原样保留，新第五条事前记账；result与attempt逐字节相同。原H3失败result和ledger已保存在恢复工具的独占checkpoint目录，未用成功结果覆盖历史检查点。

最终文件SHA256：

- result与attempt：`2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176`
- ai-call-ledger：`eb9cbf3fd6a43b5be90d74a658d01ae73126b6f3f82e6ca1444b21c1e19f8b43`

## 保留与范围

Proposal2 REJECTED、Proposal3 ACCEPTED、新ElementVersion3/WebCaseVersion7 APPROVED、新Run `run_7852432fb7b241dabcad111664b9621b` SUCCESS及Evidence5沿用H3实际结果。源版本6不变。没有新建第三套资产、重跑baseline、再次批准、重复执行或重投旧消息。

完整后端真实自愈/失败分析门禁已具备结果；还须Frontend H4的真实页面只读审计与深链验证，才完成P5-E整体验收。此合成Case仍只有空表单CLICK，不代表完整V1登录、Session恢复、CRUD或最终场景D已通过。
