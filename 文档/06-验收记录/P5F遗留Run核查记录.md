# P5-F 遗留 Run 核查记录

> 2026-09-09 主控只读查询正式 MySQL；未执行 UPDATE、DELETE、取消或强制停止请求。

## 实际结果

- 诊断 Run：`RUN-D41EB71FFFFE`，内部 ID `run_2759b1b950394f8dac5a967410c5dfd1`。
- 状态已经为 `TIMEOUT`，错误类型 `TOTAL_TIMEOUT`，`force_stopped=0`。
- 持久化执行预算为 `60000 ms`；开始时间 `2026-09-09T06:06:08Z`，终止时间 `2026-09-09T13:24:08Z`，对应本轮 Backend 启动后协调器运行时段。
- CaseRun `24`、StepRun `54` 均为 `TIMEOUT / TOTAL_TIMEOUT`。
- 原 Outbox 保持 `PUBLISHED`，没有补写 Web 执行结果，`run_web_execution_results` 关联记录数为 0。
- 保留 1 个原截图 Evidence，大小 314794 字节，ID `artifact_42910359005668792638c92eda932657c56b5b18`。未读取或删除截图内容。

## 判定与后续

该记录有持久化预算，已经被服务端超期兜底收敛；不是缺预算历史 Run，不能再把它报告为仍在 RUNNING。此次只读查询不单独证明远端浏览器已停止或 Cleanup 已完成，也不将缺少的 Web trace 补成成功。

保留诊断 Run、子结果、Outbox 和截图作为故障审计历史。该记录已经终态，不需要另行发送取消或手写数据库状态；其关联 Web Case `4` / Version `4` 属于此前真实录制演示资产，不在本轮合成资产归档清单中，不擅自归档或删除。
