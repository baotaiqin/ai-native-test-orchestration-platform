# V1-W10/r1 多标签页 Runner

主控已派发AI-Test-Runner_sol，Sol/xhigh。补齐总规格页面操作NEW_TAB、SWITCH_TAB、CLOSE_TAB，保持当前动作七字段type/timeout_ms/failure_policy/url/locator/value/key和schema_version=1。Backend/Frontend接入后续，不改其代码。

冻结动作契约：NEW_TAB url为既有有效http(s)URL，value为稳定标签别名，locator/key=null；SWITCH_TAB/CLOSE_TAB仅value别名，其余url/locator/key=null。别名为1..64位ASCII字母数字下划线短横线，首字符字母，初始页固定main；NEW_TAB禁止main及重复存活别名；SWITCH/CLOSE拒绝未知/已关闭别名，不按浏览器索引猜测。NEW_TAB创建并切换，SWITCH显式切换，CLOSE非当前页不改变当前，关闭当前页切回main（main已关闭则最近仍存活的受管页）；禁止关闭最后一页。保持最多16个受管存活页，非法/超限受控失败而非静默成功。业务自发popup不自动登记/接管，显式命令不碰用户外部浏览器。

仅本次执行context内状态。导航成功/失败后都可靠记住新页状态，遵守节点/总预算和取消；新页沿用context/session和已有敏感信息、console、证据保护。后续action/assertion/截图/定位/失败证据均作用于当前页，不能仍读取已关闭初始page；所有自有页最终随context清理。旧动作不改语义。安全错误不输出URL秘密或原始DOM。

允许runner/runner/protocol.py、models.py必要兼容局部、executors/web.py及适当内部tab helper、runner/tests专项；禁止改worker/队列/credential/Backend/Frontend/全局文档。当前Backend A1只开发平台认证，不依赖变化中的Runner；Frontend W9只读后端断言schema。

仅编译/lint、协议针对性与一个有界本地受控Chrome多页流程（新建、切换、关闭、后续动作/断言和清理）验证实际可执行。不跑Runner全量/W1/W4大矩阵，不正式服务/AI/共享Demo。本机Chrome不可用则记录未验，不以mock当真运行。不新增等待其他任务的依赖。

独占.codex-validation/v1-w10/和文档/05-交付记录/V1-W10多标签页Runner交付.md；简述修改、冻结接口、基本检查、待集中验收并结束，不跨任务消息/等待或自行扩展。Git/Claude/内部Agent禁止。