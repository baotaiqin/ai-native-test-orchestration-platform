"""Bounded Playwright MCP exploration driven by backend-reviewed AI intents."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from runner.errors import ConfigurationError, ExecutionError, ProtocolError, RunnerError
from runner.evidence import ValidatedEvidenceArtifact, validate_artifact
from runner.mcp_client import PlaywrightMcpClient
from runner.models import (
    WebExplorationControlResult,
    WebExplorationDecisionResult,
    WebExplorationExecutionPlanResult,
)
from runner.redaction import redact_text

_PAGE_URL = re.compile(r"(?m)^- Page URL:\s*(\S+)\s*$")
_PAGE_TITLE = re.compile(r"(?m)^- Page Title:\s*(.*?)\s*$")
_ALWAYS_FORBIDDEN = re.compile(
    r"(?i)(?:\b(?:delete|remove|destroy|drop|truncate|purchase|buy|pay|payment|checkout|upload|"
    r"permission|authorize)\b|删除|移除|销毁|清空|付款|支付|购买|结算|上传|修改权限|授权)"
)
_CREATE_ACTION = re.compile(r"(?i)(?:\b(?:create|add|new|place\s+order)\b|创建|新增|添加|下单)")
_SUBMIT_ACTION = re.compile(
    r"(?i)(?:\b(?:submit|save|confirm|send|publish|approve)\b|提交|保存|确认|发送|发布|批准)"
)
_SENSITIVE_FIELD = re.compile(
    r"(?i)(password|passwd|token|secret|credential|authorization|cookie|api[_-]?key|"
    r"密码|口令|密钥|令牌|凭据)"
)
_USERNAME_FIELD = re.compile(
    r"(?i)(?:\buser(?:\s*name)?\b|\baccount\b|\bemail\b|\blogin\b|"
    r"\b(?:mobile|phone|employee\s*id)\b|用户名|账号|邮箱|登录名|手机号|手机|工号|会员名)"
)
_PASSWORD_FIELD = re.compile(r"(?i)(?:pass\s*(?:word|code)|passwd|密码|口令)")
_LOGIN_ACTION = re.compile(r"(?i)(?:log\s*in|sign\s*in|login|登录|登入)")
_INPUT_REFERENCE = re.compile(
    r"^\{\{(?P<kind>secret|faker)\.(?P<name>AI_TEST_USERNAME|AI_TEST_PASSWORD|"
    r"INVALID_USERNAME|INVALID_PASSWORD)\}\}$"
)
_SAFE_PRESS_KEYS = frozenset(
    {
        "Escape",
        "Tab",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "PageUp",
        "PageDown",
        "Home",
        "End",
    }
)
_SNAPSHOT_REF = re.compile(r"^[A-Za-z0-9_-]+$")
_SCREENSHOT_CHECKPOINT = re.compile(
    r"(?i)(?:\b(?:log\s*in|sign\s*in|log\s*out|sign\s*out|submit|save|confirm|"
    r"create|place\s+order|success|failed|error)\b|登录|登入|退出|提交|保存|确认|"
    r"创建|下单|成功|失败|错误)"
)
_LOGIN_FORM_VISIBLE = re.compile(
    r"(?i)(?:textbox[^\n]*(?:password|密码)|(?:password|密码)[^\n]*textbox)"
)


class PermissionDenied(ExecutionError):
    def __init__(self, capability: str, action: str, target: str) -> None:
        self.capability = capability
        self.action = action
        self.target = target[:300]
        super().__init__(
            f"MCP 缺少权限 {capability}：无法执行 {action}"
            + (f"（{self.target}）" if self.target else "")
        )


@dataclass(frozen=True, repr=False)
class WebExplorationExecutionResult:
    outcome: str
    observations: list[dict[str, Any]]
    action_trace: list[dict[str, Any]]
    result_summary: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None

    def __repr__(self) -> str:
        return (
            "WebExplorationExecutionResult("
            f"outcome={self.outcome!r}, observations={len(self.observations)}, "
            f"actions={len(self.action_trace)}, error_type={self.error_type!r})"
        )


class WebExplorationExecutor:
    def __init__(
        self,
        *,
        decide: Callable[[Mapping[str, Any]], WebExplorationDecisionResult],
        control: Callable[[], WebExplorationControlResult],
        upload_evidence: Callable[
            [ValidatedEvidenceArtifact, int, str, str], str
        ]
        | None = None,
        runtime_dir: Path | None = None,
    ) -> None:
        self.decide = decide
        self.control = control
        self.upload_evidence = upload_evidence
        self.runtime_dir = runtime_dir or Path(__file__).resolve().parents[2] / "mcp_node"

    def execute(
        self, plan: WebExplorationExecutionPlanResult, work_dir: Path
    ) -> WebExplorationExecutionResult:
        self._permissions = plan.permissions
        self._screenshot_count = 0
        self._managed_secret_used = False
        observations: list[dict[str, Any]] = []
        action_trace: list[dict[str, Any]] = []
        previous_action: dict[str, Any] | None = None
        session_state = plan.session.storage_state if plan.session else None
        input_values: dict[tuple[str, str], str] = {
            ("secret", item.name): item.value for item in plan.secrets
        }
        suffix = plan.exploration_id[:12]
        input_values[("faker", "INVALID_USERNAME")] = f"invalid-user-{suffix}"
        input_values[("faker", "INVALID_PASSWORD")] = f"Invalid-{suffix}!"
        sensitive_values = tuple(input_values.values())
        try:
            with PlaywrightMcpClient(
                runtime_dir=self.runtime_dir,
                work_dir=work_dir,
                allowed_origins=plan.allowed_origins,
                storage_state=session_state,
                headless=plan.headless,
            ) as mcp:
                mcp.call_tool("browser_navigate", {"url": plan.start_url})
                for sequence in range(1, plan.max_steps + 1):
                    control = self.control()
                    if control.cancellation_requested:
                        return WebExplorationExecutionResult(
                            outcome="CANCELLED",
                            observations=[],
                            action_trace=[],
                            error_type="CANCEL_REQUESTED",
                            error_message="Web 探索已按请求取消",
                        )
                    if control.stop_requested:
                        return self._completed(observations, action_trace, "用户停止了 Web 探索")
                    raw_snapshot = mcp.call_tool("browser_snapshot", {})
                    page_url, title = self._page_state(raw_snapshot)
                    self._ensure_allowed(page_url, plan.allowed_origins)
                    parsed_page_url = urlsplit(page_url)
                    safe_page_url = parsed_page_url._replace(
                        path=redact_text(parsed_page_url.path, sensitive_values, limit=1500),
                        query=redact_text(parsed_page_url.query, sensitive_values, limit=500),
                        fragment="",
                    ).geturl()
                    safe_title = (
                        redact_text(title, sensitive_values, limit=500)
                        if title is not None
                        else None
                    )
                    snapshot = redact_text(
                        raw_snapshot, sensitive_values, limit=200_000
                    )
                    network = redact_text(
                        mcp.call_tool("browser_network_requests", {"static": False}),
                        sensitive_values,
                        limit=20_000,
                    )
                    observation = {
                        "sequence": sequence,
                        "page_url": safe_page_url,
                        "title": safe_title,
                        "accessibility_snapshot": snapshot[:200_000],
                        "network_events": [{"summary": network}],
                        "previous_action": previous_action,
                    }
                    observations.append(observation)
                    decision_result = self.decide(observation)
                    decision = dict(decision_result.decision)
                    if decision.get("action") == "FINISH":
                        final_evidence = self._capture_evidence(
                            mcp,
                            work_dir,
                            sequence,
                            "FINAL",
                            str(decision.get("reason") or "探索结束"),
                            snapshot=raw_snapshot,
                        )
                        action_trace.append(
                            {
                                "sequence": sequence,
                                "ai_call_id": decision_result.ai_call_id,
                                "decision": decision,
                                "status": "FINISHED",
                                **({"evidence_id": final_evidence} if final_evidence else {}),
                            }
                        )
                        return self._completed(
                            observations,
                            action_trace,
                            str(decision.get("reason") or "AI 已完成探索目标"),
                        )
                    if decision.get("action") == "NAVIGATE":
                        self._ensure_allowed(str(decision.get("url") or ""), plan.allowed_origins)
                    try:
                        trace = self._execute_decision(
                            mcp,
                            sequence,
                            decision_result,
                            decision,
                            raw_snapshot=raw_snapshot,
                            input_values=input_values,
                            sensitive_values=sensitive_values,
                        )
                    except ExecutionError:
                        self._capture_evidence(
                            mcp,
                            work_dir,
                            sequence,
                            "FAILURE",
                            f"第 {sequence} 步执行失败",
                            snapshot=raw_snapshot,
                        )
                        raise
                    if self._is_screenshot_checkpoint(decision, trace):
                        evidence_id = self._capture_evidence(
                            mcp,
                            work_dir,
                            sequence,
                            "CHECKPOINT",
                            str(
                                decision.get("expected_observation")
                                or decision.get("element")
                                or f"第 {sequence} 步检查点"
                            ),
                        )
                        if evidence_id:
                            trace["evidence_id"] = evidence_id
                    action_trace.append(trace)
                    previous_action = {
                        "sequence": sequence,
                        "action": decision.get("action"),
                        "element": decision.get("element"),
                        "ref": decision.get("ref"),
                        "status": trace["status"],
                    }
                return self._completed(observations, action_trace, "达到 Web 探索最大步骤数")
        except PermissionDenied as exc:
            return WebExplorationExecutionResult(
                outcome="FAILED",
                observations=observations,
                action_trace=action_trace,
                result_summary={
                    "reason": str(exc),
                    "required_capability": exc.capability,
                    "action": exc.action,
                    "target": exc.target,
                },
                error_type="MCP_PERMISSION_DENIED",
                error_message=str(exc)[:1000],
            )
        except ConfigurationError as exc:
            return WebExplorationExecutionResult(
                outcome="FAILED",
                observations=observations,
                action_trace=action_trace,
                error_type="MCP_CONFIGURATION_ERROR",
                error_message=str(exc)[:1000],
            )
        except ProtocolError as exc:
            return WebExplorationExecutionResult(
                outcome="FAILED",
                observations=observations,
                action_trace=action_trace,
                error_type="WEB_EXPLORATION_PROTOCOL_ERROR",
                error_message=redact_text(exc, sensitive_values, limit=1000),
            )
        except ExecutionError as exc:
            return WebExplorationExecutionResult(
                outcome="FAILED",
                observations=observations,
                action_trace=action_trace,
                error_type="MCP_EXECUTION_ERROR",
                error_message=str(exc)[:1000],
            )
        except RunnerError:
            raise
        except Exception:
            return WebExplorationExecutionResult(
                outcome="FAILED",
                observations=observations,
                action_trace=action_trace,
                error_type="WEB_EXPLORATION_ERROR",
                error_message="Web 探索执行失败",
            )

    @staticmethod
    def _page_state(snapshot: str) -> tuple[str, str | None]:
        url_match = _PAGE_URL.search(snapshot)
        if url_match is None:
            raise ExecutionError("Playwright MCP 快照缺少 Page URL")
        title_match = _PAGE_TITLE.search(snapshot)
        return url_match.group(1), title_match.group(1)[:500] if title_match else None

    @staticmethod
    def _ensure_allowed(url: str, allowed_origins: tuple[str, ...]) -> None:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        if origin not in allowed_origins:
            raise ExecutionError("浏览器跳转离开 Web 探索允许的 origin")

    @staticmethod
    def _completed(
        observations: list[dict[str, Any]],
        action_trace: list[dict[str, Any]],
        reason: str,
    ) -> WebExplorationExecutionResult:
        return WebExplorationExecutionResult(
            outcome="COMPLETED",
            observations=observations,
            action_trace=action_trace,
            result_summary={
                "reason": reason[:1000],
                "observation_count": len(observations),
                "action_count": len(action_trace),
            },
        )

    def _execute_decision(
        self,
        mcp: PlaywrightMcpClient,
        sequence: int,
        decision_result: WebExplorationDecisionResult,
        decision: dict[str, Any],
        *,
        raw_snapshot: str,
        input_values: Mapping[tuple[str, str], str],
        sensitive_values: tuple[str, ...],
    ) -> dict[str, Any]:
        action = decision.get("action")
        element = str(decision.get("element") or "")
        target = decision.get("ref")
        if action in {"CLICK", "TYPE", "SELECT"} and (
            not isinstance(target, str) or not _SNAPSHOT_REF.fullmatch(target)
        ):
            raise ExecutionError("Runner 拒绝非快照引用的 Web 探索目标")
        target_context = self._snapshot_ref_context(raw_snapshot, target)
        # 风险判断只能基于真正会被执行的目标，不能读取模型的 reason。reason 是
        # 自由文本，诸如“确认登录已失效”会包含“确认”等风险词，但并不代表将点击
        # 确认按钮。CLICK 同时核对模型描述和当前快照行，NAVIGATE 只核对目标 URL；
        # TYPE/SELECT 均不会自动提交，PRESS 另由安全按键白名单控制。
        if action == "CLICK":
            action_target = f"{element} {target_context}"
        elif action == "NAVIGATE":
            action_target = str(decision.get("url") or "")
        else:
            action_target = ""
        permissions = set(getattr(self, "_permissions", ()))
        if _ALWAYS_FORBIDDEN.search(action_target):
            raise ExecutionError("Runner 拒绝执行可能产生破坏性副作用的探索动作")
        if action == "NAVIGATE" and urlsplit(action_target).path.lower().startswith("/api/"):
            self._require_permission(permissions, "DIRECT_API_NAVIGATION", action, action_target)
        if action == "CLICK" and _LOGIN_ACTION.search(action_target):
            self._require_permission(permissions, "MANAGED_LOGIN", action, action_target)
        elif action == "CLICK" and _CREATE_ACTION.search(action_target):
            self._require_permission(permissions, "FORM_SUBMIT", action, action_target)
            self._require_permission(permissions, "TEST_DATA_CREATE", action, action_target)
        elif action == "CLICK" and _SUBMIT_ACTION.search(action_target):
            self._require_permission(permissions, "FORM_SUBMIT", action, action_target)
        field_context = f"{element} {target_context}"
        reference = (
            _INPUT_REFERENCE.fullmatch(str(decision.get("value") or ""))
            if action == "TYPE"
            else None
        )
        typed_value: str | None = None
        safe_decision = dict(decision)
        if reference is not None:
            if reference.group("kind") == "secret":
                self._require_permission(permissions, "MANAGED_LOGIN", action, field_context)
            reference_key = (reference.group("kind"), reference.group("name"))
            if reference.group("kind") == "secret":
                self._managed_secret_used = True
            typed_value = input_values.get(reference_key)
            if typed_value is None:
                raise ExecutionError("Runner 未获得探索决策引用的登录凭据")
            if reference.group("name").endswith("USERNAME"):
                matches_field = _USERNAME_FIELD.search(field_context) is not None
            else:
                matches_field = _PASSWORD_FIELD.search(field_context) is not None
            if not matches_field:
                raise ExecutionError("Runner 拒绝把登录凭据注入不匹配的页面字段")
            safe_decision["value"] = "[REDACTED]"
        elif action == "TYPE" and (
            _SENSITIVE_FIELD.search(field_context) or _USERNAME_FIELD.search(field_context)
        ):
            raise ExecutionError("Runner 要求登录字段使用受控凭据或合成数据引用")
        if action == "PRESS" and decision.get("value") not in _SAFE_PRESS_KEYS:
            if decision.get("value") == "Enter":
                self._require_permission(permissions, "FORM_SUBMIT", action, "Enter")
            else:
                raise ExecutionError("Runner 拒绝可能提交页面或输入文本的按键")
        locator: str | None = None
        if action in {"CLICK", "TYPE", "SELECT"}:
            locator = mcp.call_tool(
                "browser_generate_locator",
                {"element": element, "target": target},
            )
            locator = redact_text(locator, sensitive_values, limit=2000)
        arguments: dict[str, Any]
        tool: str
        if action == "NAVIGATE":
            tool, arguments = "browser_navigate", {"url": decision.get("url")}
        elif action == "CLICK":
            tool, arguments = "browser_click", {"element": element, "target": target}
        elif action == "TYPE":
            tool, arguments = (
                "browser_type",
                {
                    "element": element,
                    "target": target,
                    "text": typed_value if typed_value is not None else decision.get("value"),
                    "submit": False,
                    "slowly": False,
                },
            )
        elif action == "SELECT":
            tool, arguments = (
                "browser_select_option",
                {
                    "element": element,
                    "target": target,
                    "values": [decision.get("value")],
                },
            )
        elif action == "PRESS":
            tool, arguments = "browser_press_key", {"key": decision.get("value")}
        elif action == "WAIT":
            tool, arguments = "browser_wait_for", {"time": 1}
        else:
            raise ExecutionError("Web 探索决策动作不受 Runner 支持")
        output = mcp.call_tool(tool, arguments)
        return {
            "sequence": sequence,
            "ai_call_id": decision_result.ai_call_id,
            "decision": safe_decision,
            "mcp_tool": tool,
            "generated_locator": locator,
            "result": redact_text(output, sensitive_values, limit=5000),
            "status": "EXECUTED",
        }

    def _capture_evidence(
        self,
        mcp: PlaywrightMcpClient,
        work_dir: Path,
        sequence: int,
        kind: str,
        label: str,
        *,
        snapshot: str | None = None,
    ) -> str | None:
        if (
            "SCREENSHOT_CAPTURE" not in set(getattr(self, "_permissions", ()))
            or self.upload_evidence is None
            or self._screenshot_count >= 12
            or (
                self._managed_secret_used
                and snapshot is not None
                and _LOGIN_FORM_VISIBLE.search(snapshot)
            )
        ):
            return None
        artifact_name = f"exploration-{sequence:02d}-{kind.lower()}"
        try:
            path = mcp.capture_screenshot(artifact_name)
            evidence = validate_artifact(
                work_dir,
                "SCREENSHOT",
                path.name,
                {},
            )
            safe_label = redact_text(label, (), limit=255).strip() or f"第 {sequence} 步"
            evidence_id = self.upload_evidence(evidence, sequence, kind, safe_label)
        except RunnerError:
            return None
        self._screenshot_count += 1
        return evidence_id

    def _is_screenshot_checkpoint(
        self, decision: Mapping[str, Any], trace: Mapping[str, Any]
    ) -> bool:
        if decision.get("action") != "CLICK":
            return False
        context = " ".join(
            str(value or "")
            for value in (
                decision.get("element"),
                decision.get("expected_observation"),
                decision.get("reason"),
            )
        )
        if not _SCREENSHOT_CHECKPOINT.search(context):
            return False
        # A full-page screenshot may expose a managed username while a failed login
        # leaves the password form visible. Preserve the audit reason, but skip the
        # image until the sensitive form has disappeared.
        return not (
            self._managed_secret_used
            and _LOGIN_ACTION.search(context)
            and _LOGIN_FORM_VISIBLE.search(str(trace.get("result") or ""))
        )

    @staticmethod
    def _require_permission(
        permissions: set[str], capability: str, action: str, target: str
    ) -> None:
        if capability not in permissions:
            raise PermissionDenied(capability, action, target)

    @staticmethod
    def _snapshot_ref_context(snapshot: str, target: object) -> str:
        if not isinstance(target, str):
            return ""
        marker = re.compile(rf"(?:\[ref={re.escape(target)}\]|\bref={re.escape(target)}\b)")
        for line in snapshot.splitlines():
            if marker.search(line):
                return line[:1000]
        raise ExecutionError("Runner 在当前页面快照中找不到探索目标引用")
