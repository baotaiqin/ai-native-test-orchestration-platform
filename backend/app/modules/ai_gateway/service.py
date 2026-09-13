import base64
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    ResourceConflictError,
    ResourceNotFoundError,
    SecurityConfigurationError,
)
from app.core.metrics import observe_ai_call
from app.core.secret_cipher import decrypt_secret
from app.modules.ai_gateway.schemas import AiGenerateRequest, AiGenerateResponse
from app.modules.auth.schemas import CurrentUser
from app.modules.model_center.models import ModelConfiguration, ProjectModelBinding
from app.modules.projects.service import get_project
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.prompt_center.schemas import PromptRenderRequest
from app.modules.prompt_center.service import render_prompt, resolve_effective_prompt
from app.modules.prompt_center.validator import parse_json_once, validate_json_schema

RETRYABLE_STATUS = {429, 502, 503, 504}
DOMAIN_RESULT_VALIDATION_ERROR = "$: 领域结果不符合调用上下文中的安全约束"
StructuredResultValidator = Callable[[Any], bool | Sequence[str]]


def _domain_validation_errors(result: bool | Sequence[str]) -> list[str]:
    """Normalize trusted domain-validator feedback without accepting arbitrary payloads."""

    if result is True:
        return []
    if result is False or isinstance(result, (str, bytes)):
        return [DOMAIN_RESULT_VALIDATION_ERROR]
    if not isinstance(result, Sequence):
        return [DOMAIN_RESULT_VALIDATION_ERROR]
    errors: list[str] = []
    for value in result[:20]:
        if not isinstance(value, str):
            return [DOMAIN_RESULT_VALIDATION_ERROR]
        normalized = " ".join(value.split()).strip()
        if normalized:
            errors.append(normalized[:500])
    return errors or []


@dataclass(frozen=True)
class AiImageInput:
    """Trusted internal image input; never exposed by the public AI endpoint."""

    mime: str
    content: bytes


class ProviderCallError(Exception):
    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


@dataclass
class ProviderResult:
    content: str
    response_id: str | None
    input_token: int
    output_token: int


@dataclass(frozen=True)
class ModelConnectionProbeResult:
    success: bool
    status: str
    duration_ms: int
    summary: str
    error_type: str | None = None


def _build_client(timeout: int) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def _provider_url(model: ModelConfiguration) -> str:
    connection = model.connection
    if connection is None or not connection.enabled:
        raise ProviderCallError("CONNECTION_UNAVAILABLE", "模型接入渠道不可用", retryable=False)
    return f"{connection.base_url.rstrip('/')}/chat/completions"


def _provider_headers(session: Session, model: ModelConfiguration) -> dict:
    headers = {"Content-Type": "application/json"}
    connection = model.connection
    if connection is None or not connection.enabled:
        raise ProviderCallError("CONNECTION_UNAVAILABLE", "模型接入渠道不可用", retryable=False)
    if connection.api_key_encrypted_value:
        key = decrypt_secret(connection.api_key_encrypted_value, get_settings().secret_key)
        headers["Authorization"] = f"Bearer {key}"
    return headers


def validate_model_base_url(base_url: str) -> None:
    """Validate the endpoint shape before storing or contacting a model URL."""
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("模型服务地址不受支持") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("模型服务地址不受支持")


def _validate_probe_url(base_url: str) -> None:
    try:
        validate_model_base_url(base_url)
    except ValueError as exc:
        raise ProviderCallError(
            "INVALID_ENDPOINT", "模型服务地址不受支持", retryable=False
        ) from exc


def _call_provider(
    session: Session,
    model: ModelConfiguration,
    messages: list[dict[str, Any]],
    output_schema: OutputSchema | None,
    *,
    timeout_seconds: int | None = None,
) -> ProviderResult:
    body: dict[str, Any] = {"model": model.model_name, "messages": messages}
    if output_schema is not None and model.supports_structured_output:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": output_schema.name,
                "strict": True,
                "schema": output_schema.schema_json,
            },
        }
    try:
        with _build_client(timeout_seconds or model.timeout_seconds) as client:
            response = client.post(
                _provider_url(model),
                headers=_provider_headers(session, model),
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise ProviderCallError("TIMEOUT", "模型调用超时", retryable=True) from exc
    except httpx.NetworkError as exc:
        raise ProviderCallError("NETWORK_ERROR", "模型服务网络不可用", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderCallError("NETWORK_ERROR", "模型服务网络不可用", retryable=True) from exc
    except SecurityConfigurationError as exc:
        raise ProviderCallError(
            "SECRET_UNAVAILABLE", "模型绑定的 API Key 不可用", retryable=False
        ) from exc
    if response.status_code >= 400:
        retryable = response.status_code in RETRYABLE_STATUS
        error_type = (
            "RATE_LIMIT"
            if response.status_code == 429
            else "SERVICE_UNAVAILABLE"
            if retryable
            else "PROVIDER_REQUEST_ERROR"
        )
        raise ProviderCallError(
            error_type,
            f"模型服务返回 HTTP {response.status_code}",
            retryable=retryable,
            status_code=response.status_code,
        )
    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderCallError(
            "PROVIDER_RESPONSE_INVALID", "模型服务响应格式无效", retryable=False
        ) from exc
    usage = data.get("usage") or {}
    return ProviderResult(
        content=str(content),
        response_id=str(data["id"]) if data.get("id") else None,
        input_token=int(usage.get("prompt_tokens") or 0),
        output_token=int(usage.get("completion_tokens") or 0),
    )


def _call_provider_with_schema_compatibility(
    session: Session,
    model: ModelConfiguration,
    messages: list[dict[str, Any]],
    output_schema: OutputSchema | None,
    *,
    timeout_seconds: int | None = None,
) -> tuple[ProviderResult, bool]:
    """Retry once without native JSON Schema when a compatible API rejects it.

    Platform-side parsing, Schema validation and the existing bounded repair remain mandatory.
    The retry only removes an optional transport hint that OpenAI-compatible providers do not
    implement consistently.
    """
    schema_messages = _messages_with_schema_contract(messages, output_schema)
    try:
        return _call_provider(
            session,
            model,
            messages if model.supports_structured_output else schema_messages,
            output_schema,
            timeout_seconds=timeout_seconds,
        ), False
    except ProviderCallError as exc:
        if (
            output_schema is None
            or not model.supports_structured_output
            or exc.status_code not in {400, 422}
        ):
            raise
        return _call_provider(
            session, model, schema_messages, None, timeout_seconds=timeout_seconds
        ), True


def _messages_with_schema_contract(
    messages: list[dict[str, Any]],
    output_schema: OutputSchema | None,
) -> list[dict[str, Any]]:
    """Show the exact contract when transport-level json_schema is unavailable."""

    if output_schema is None:
        return messages
    contract = {
        "name": output_schema.name,
        "schema": output_schema.schema_json,
    }
    instruction = (
        "你的最终回答必须只包含一个 JSON 值，不要使用 Markdown 代码块或补充解释。"
        "该 JSON 必须严格符合以下结构，字段名、类型、枚举和必填项均不可改变："
        + json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
    )
    if not messages:
        return [{"role": "system", "content": instruction}]
    first = messages[0]
    if first.get("role") == "system" and isinstance(first.get("content"), str):
        return [
            {**first, "content": f"{first['content']}\n\n{instruction}"},
            *messages[1:],
        ]
    return [{"role": "system", "content": instruction}, *messages]


_PROBE_SUMMARIES = {
    "TIMEOUT": "模型连接验证超时",
    "NETWORK_ERROR": "模型服务网络不可用",
    "RATE_LIMIT": "模型服务限流",
    "SERVICE_UNAVAILABLE": "模型服务暂不可用",
    "PROVIDER_REQUEST_ERROR": "模型服务请求失败",
    "PROVIDER_RESPONSE_INVALID": "模型服务响应格式无效",
    "SECRET_UNAVAILABLE": "模型 API Key 不可用",
    "INVALID_ENDPOINT": "模型服务地址不受支持",
    "CONNECTION_UNAVAILABLE": "模型接入渠道不可用",
    "MODEL_DISABLED": "模型配置已停用",
}


def probe_model_connection(
    session: Session, model: ModelConfiguration
) -> ModelConnectionProbeResult:
    """Run a deterministic, non-business probe without creating an AiCallLog."""
    started_at = time.perf_counter()

    def duration_ms() -> int:
        return min(60_000, max(0, round((time.perf_counter() - started_at) * 1000)))

    try:
        if not model.enabled:
            raise ProviderCallError("MODEL_DISABLED", "模型配置已停用", retryable=False)
        if model.connection is None or not model.connection.enabled:
            raise ProviderCallError("CONNECTION_UNAVAILABLE", "模型接入渠道不可用", retryable=False)
        _validate_probe_url(model.connection.base_url)
        # Keep the probe bounded even when a model is configured with a long task timeout.
        _call_provider(
            session,
            model,
            [{"role": "user", "content": "Reply with exactly OK."}],
            None,
            timeout_seconds=min(max(model.timeout_seconds, 1), 60),
        )
    except ProviderCallError as exc:
        return ModelConnectionProbeResult(
            success=False,
            status="FAILED",
            duration_ms=duration_ms(),
            summary=_PROBE_SUMMARIES.get(exc.error_type, "模型连接验证失败"),
            error_type=exc.error_type,
        )
    return ModelConnectionProbeResult(
        success=True,
        status="SUCCESS",
        duration_ms=duration_ms(),
        summary="模型连接验证成功",
    )


def _get_binding(session: Session, project_id: int, task_type: str) -> ProjectModelBinding:
    binding = session.scalar(
        select(ProjectModelBinding).where(
            ProjectModelBinding.project_id == project_id,
            ProjectModelBinding.task_type == task_type,
        )
    )
    if binding is None:
        raise ResourceConflictError("当前项目尚未为该 AI 任务绑定模型")
    return binding


def _get_enabled_model(session: Session, model_id: int) -> ModelConfiguration:
    model = session.get(ModelConfiguration, model_id)
    if model is None or not model.enabled:
        raise ResourceConflictError("任务绑定的模型不存在或已停用")
    if model.connection is None or not model.connection.enabled:
        raise ResourceConflictError("任务绑定的模型接入渠道不存在或已停用")
    return model


def _get_prompt_context(
    session: Session,
    payload: AiGenerateRequest,
    image_input: AiImageInput | None = None,
) -> tuple[PromptDefinition, PromptVersion, OutputSchema | None, list[dict[str, Any]]]:
    prompt = session.get(PromptDefinition, payload.prompt_id)
    if prompt is None or not prompt.enabled:
        raise ResourceNotFoundError("Prompt 不存在或已停用")
    if prompt.task_type != payload.task_type.value:
        raise ResourceConflictError("Prompt 与 AI 任务类型不匹配")
    prompt = resolve_effective_prompt(session, payload.project_id, prompt)
    if not prompt.enabled or prompt.task_type != payload.task_type.value:
        raise ResourceConflictError("项目提示词与 AI 任务类型不匹配或已停用")
    version = session.get(PromptVersion, prompt.current_version_id)
    if version is None:
        raise ResourceConflictError("Prompt 没有当前版本")
    rendered = render_prompt(
        session,
        CurrentUser(id="gateway", username="gateway", display_name="gateway", roles=[]),
        prompt.id,
        PromptRenderRequest(variables=payload.variables),
    )
    if rendered.missing_variables:
        raise ResourceConflictError(f"Prompt 缺少变量：{', '.join(rendered.missing_variables)}")
    output_schema = (
        session.get(OutputSchema, version.output_schema_id)
        if version.output_schema_id is not None
        else None
    )
    if version.output_schema_id is not None and (
        output_schema is None or not output_schema.enabled
    ):
        raise ResourceConflictError("Prompt 绑定的 Output Schema 不可用")
    user_content: str | list[dict[str, Any]] = rendered.user_prompt
    if image_input is not None:
        if (
            image_input.mime != "image/png"
            or not image_input.content.startswith(b"\x89PNG\r\n\x1a\n")
            or not 0 < len(image_input.content) <= 5_000_000
        ):
            raise ResourceConflictError("AI 图片输入必须是 5MB 内的有效 PNG")
        encoded = base64.b64encode(image_input.content).decode("ascii")
        user_content = [
            {"type": "text", "text": rendered.user_prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            },
        ]
    messages = [
        {"role": "system", "content": rendered.system_prompt},
        {"role": "user", "content": user_content},
    ]
    return prompt, version, output_schema, messages


def _cost(model: ModelConfiguration, input_token: int, output_token: int) -> Decimal:
    return (
        Decimal(input_token) * model.input_price + Decimal(output_token) * model.output_price
    ) / Decimal(1_000_000)


def _save_log(
    session: Session,
    payload: AiGenerateRequest,
    model: ModelConfiguration,
    version: PromptVersion,
    schema: OutputSchema | None,
    *,
    started_at: float,
    success: bool,
    content: str,
    parsed_result: Any,
    validation_errors: list[str],
    fallback_used: bool,
    repair_used: bool,
    retry_count: int,
    input_token: int,
    output_token: int,
    response_id: str | None,
    error_type: str | None,
    repair_response: str | None = None,
    commit: bool = True,
) -> AiCallLog:
    log = AiCallLog(
        project_id=payload.project_id,
        task_type=payload.task_type.value,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        model_config_id=model.id,
        actual_model=model.model_name,
        prompt_version_id=version.id,
        output_schema_id=schema.id if schema else None,
        input_token=input_token,
        output_token=output_token,
        total_token=input_token + output_token,
        estimated_cost=_cost(model, input_token, output_token),
        latency_ms=round((time.perf_counter() - started_at) * 1000),
        success=success,
        fallback_used=fallback_used,
        retry_count=retry_count,
        repair_used=repair_used,
        error_type=error_type,
        response_id=response_id,
        raw_response=content,
        repair_response=repair_response,
        parsed_result=parsed_result if success else None,
        validation_errors=validation_errors,
    )
    session.add(log)
    if commit:
        session.commit()
        session.refresh(log)
    else:
        session.flush()
    observe_ai_call(
        task_type=payload.task_type.value,
        success=success,
        fallback_used=fallback_used,
        repair_used=repair_used,
        input_token=input_token,
        output_token=output_token,
        estimated_cost=log.estimated_cost,
    )
    return log


def _validate_structured_result(
    content: str,
    output_schema: OutputSchema,
    result_validator: StructuredResultValidator | None,
) -> tuple[Any | None, list[str]]:
    parsed_result, parse_error = parse_json_once(content)
    if parse_error:
        return parsed_result, [parse_error]
    errors = validate_json_schema(parsed_result, output_schema.schema_json)
    if errors or result_validator is None:
        return parsed_result, errors
    try:
        domain_result = result_validator(parsed_result)
    except Exception:  # noqa: BLE001 - validator details must never reach logs or prompts
        return parsed_result, [DOMAIN_RESULT_VALIDATION_ERROR]
    return parsed_result, _domain_validation_errors(domain_result)


def generate(
    session: Session,
    user: CurrentUser,
    payload: AiGenerateRequest,
    *,
    result_validator: StructuredResultValidator | None = None,
    commit: bool = True,
    image_input: AiImageInput | None = None,
    timeout_seconds: int | None = None,
    trusted_system_instruction: str | None = None,
) -> AiGenerateResponse:
    get_project(session, user, payload.project_id)
    _, version, output_schema, messages = _get_prompt_context(session, payload, image_input)
    if trusted_system_instruction:
        messages[0] = {
            **messages[0],
            "content": (
                f"{messages[0]['content']}\n\n"
                "以下是平台不可由项目提示词覆盖的输出契约：\n"
                f"{trusted_system_instruction}"
            ),
        }
    binding = _get_binding(session, payload.project_id, payload.task_type.value)
    primary = _get_enabled_model(session, binding.primary_model_id)
    if (
        image_input is not None
        and primary.model_type != "VISION"
        and primary.model_category != "OMNI"
    ):
        raise ResourceConflictError("当前 Locator Healing 模型不支持图片输入")
    actual_model = primary
    started_at = time.perf_counter()
    fallback_used = False
    retry_count = 0
    native_schema_compatibility_used = False
    try:
        result, native_schema_compatibility_used = _call_provider_with_schema_compatibility(
            session, primary, messages, output_schema, timeout_seconds=timeout_seconds
        )
        if native_schema_compatibility_used:
            retry_count = 1
    except ProviderCallError as primary_error:
        can_fallback = (
            primary_error.retryable
            and binding.max_fallback == 1
            and binding.fallback_model_id is not None
        )
        if not can_fallback:
            _save_log(
                session,
                payload,
                primary,
                version,
                output_schema,
                started_at=started_at,
                success=False,
                content="",
                parsed_result=None,
                validation_errors=[primary_error.message],
                fallback_used=False,
                repair_used=False,
                retry_count=0,
                input_token=0,
                output_token=0,
                response_id=None,
                error_type=primary_error.error_type,
                commit=commit,
            )
            raise AppError(
                "MODEL_PROVIDER_ERROR",
                primary_error.message,
                status_code=503,
                details={"error_type": primary_error.error_type},
            ) from primary_error
        actual_model = _get_enabled_model(session, binding.fallback_model_id)
        fallback_used = True
        retry_count = 1
        try:
            result, native_schema_compatibility_used = _call_provider_with_schema_compatibility(
                session,
                actual_model,
                messages,
                output_schema,
                timeout_seconds=timeout_seconds,
            )
            if native_schema_compatibility_used:
                retry_count += 1
        except ProviderCallError as fallback_error:
            _save_log(
                session,
                payload,
                actual_model,
                version,
                output_schema,
                started_at=started_at,
                success=False,
                content="",
                parsed_result=None,
                validation_errors=[fallback_error.message],
                fallback_used=True,
                repair_used=False,
                retry_count=1,
                input_token=0,
                output_token=0,
                response_id=None,
                error_type=fallback_error.error_type,
                commit=commit,
            )
            raise AppError(
                "MODEL_PROVIDER_ERROR",
                fallback_error.message,
                status_code=503,
                details={"error_type": fallback_error.error_type},
            ) from fallback_error

    input_token = result.input_token
    output_token = result.output_token
    content = result.content
    response_id = result.response_id
    parsed_result: Any = content
    errors: list[str] = []
    repair_used = False
    repair_response = None
    final_error_type = "STRUCTURED_OUTPUT_INVALID"
    if output_schema is not None:
        parsed_result, errors = _validate_structured_result(
            content,
            output_schema,
            result_validator,
        )
        if errors:
            repair_used = True
            repair_instruction = (
                "上一次输出不符合 JSON Schema。"
                if result_validator is None
                else "上一次输出不符合结构化输出或可执行性约束。"
            )
            repair_base_messages = (
                _messages_with_schema_contract(messages, output_schema)
                if native_schema_compatibility_used or not actual_model.supports_structured_output
                else messages
            )
            repair_messages = [
                *repair_base_messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        f"{repair_instruction}请逐条修复下列问题；只返回修复后的 JSON，"
                        "不要 Markdown。"
                        f"\n校验错误：{json.dumps(errors, ensure_ascii=False)}"
                    ),
                },
            ]
            try:
                repaired = _call_provider(
                    session,
                    actual_model,
                    repair_messages,
                    None if native_schema_compatibility_used else output_schema,
                    timeout_seconds=timeout_seconds,
                )
                repair_response = repaired.content
                input_token += repaired.input_token
                output_token += repaired.output_token
                response_id = repaired.response_id or response_id
                parsed_result, errors = _validate_structured_result(
                    repaired.content,
                    output_schema,
                    result_validator,
                )
            except ProviderCallError as repair_error:
                errors = [repair_error.message]
                final_error_type = repair_error.error_type

    success = not errors
    log = _save_log(
        session,
        payload,
        actual_model,
        version,
        output_schema,
        started_at=started_at,
        success=success,
        content=content,
        parsed_result=parsed_result,
        validation_errors=errors,
        fallback_used=fallback_used,
        repair_used=repair_used,
        retry_count=retry_count,
        input_token=input_token,
        output_token=output_token,
        response_id=response_id,
        error_type=None if success else final_error_type,
        repair_response=repair_response,
        commit=commit,
    )
    if not success:
        raise AppError(
            "AI_STRUCTURED_OUTPUT_INVALID",
            "模型输出经一次 Repair 后仍不符合结构或可执行性约束",
            status_code=422,
            details={"ai_call_id": log.id, "validation_errors": errors},
        )
    return AiGenerateResponse(
        ai_call_id=log.id,
        success=True,
        content=repair_response or content,
        parsed_result=log.parsed_result,
        actual_model=actual_model.model_name,
        fallback_used=fallback_used,
        repair_used=repair_used,
        input_token=input_token,
        output_token=output_token,
        total_token=input_token + output_token,
        estimated_cost=log.estimated_cost,
        latency_ms=log.latency_ms,
        response_id=response_id,
    )
