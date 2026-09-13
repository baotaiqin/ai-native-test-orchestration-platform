"""Provider model catalog adapters with bounded, secret-free normalization."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.modules.model_center.schemas import (
    ModelCatalogQuery,
    ModelCategory,
    ModelType,
)

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 15.0
_BAILIAN_PROVIDER = "ALIYUN_BAILIAN"
_BAILIAN_HOSTS = frozenset(
    {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
        "cn-hongkong.dashscope.aliyuncs.com",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "set-cookie",
        "token",
    }
)


class BailianCatalogError(Exception):
    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


@dataclass(frozen=True)
class CatalogModel:
    model_id: str
    name: str
    description: str | None
    provider: str
    inference_provider: str | None
    model_category: ModelCategory
    model_type: ModelType
    capabilities: list[str]
    features: list[str]
    input_modalities: list[str]
    output_modalities: list[str]
    supports_reasoning: bool
    supports_tool_call: bool
    supports_structured_output: bool
    context_window: int | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    max_reasoning_tokens: int | None
    reasoning_max_input_tokens: int | None
    reasoning_max_output_tokens: int | None
    pricing_tiers: list[dict[str, object]]
    published_time: str | None
    supported_by_platform: bool
    unsupported_reason: str | None


@dataclass(frozen=True)
class CatalogPage:
    items: list[CatalogModel]
    total: int
    source: str
    fetched_at: datetime


def _build_client() -> httpx.Client:
    return httpx.Client(
        timeout=_REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
    )


def _is_allowed_bailian_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    hostname = hostname.lower().rstrip(".")
    return hostname in _BAILIAN_HOSTS or (
        hostname.endswith(".maas.aliyuncs.com")
        and hostname != "maas.aliyuncs.com"
    )


def build_bailian_catalog_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise BailianCatalogError("UNSAFE_ENDPOINT") from exc
    if (
        parsed.scheme != "https"
        or not _is_allowed_bailian_host(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise BailianCatalogError("UNSAFE_ENDPOINT")

    path = parsed.path.rstrip("/")
    known_suffix = "/compatible-mode/v1"
    if path.endswith(known_suffix):
        path = path[: -len(known_suffix)] + "/api/v1/models"
    elif path.endswith("/api/v1/models"):
        pass
    elif path.endswith("/api/v1"):
        path += "/models"
    else:
        path += "/api/v1/models"
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def build_bailian_catalog_params(query: ModelCatalogQuery) -> list[tuple[str, str | int]]:
    params: list[tuple[str, str | int]] = [
        ("page_no", query.page),
        ("page_size", query.page_size),
    ]
    if query.exact_model:
        params.append(("model", query.exact_model))
    elif query.q:
        params.append(("name", query.q))

    if query.capabilities:
        capabilities = list(dict.fromkeys(capability.value for capability in query.capabilities))
    else:
        capabilities = []
        if query.model_type is ModelType.TEXT:
            capabilities.append("TG")
        elif query.model_type is ModelType.VISION:
            capabilities.append("VU")
        elif query.model_type is ModelType.EMBEDDING:
            capabilities.append("TR")
        if query.reasoning:
            capabilities.append("Reasoning")
    params.extend(("capabilities", capability) for capability in capabilities)

    features: list[str] = []
    if query.tool_call:
        features.append("function-calling")
    if query.structured_output:
        features.append("structured-outputs")
    params.extend(("features", feature) for feature in features)
    return params


def _bounded_text(value: object, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] if text else None


def _string_list(value: object, *, max_items: int = 32) -> list[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, Mapping):
        values = [key for key, enabled in value.items() if enabled]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _bounded_text(item, max_length=64)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= max_items:
            break
    return result


def _lookup(item: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value) if isinstance(value, (int, float)) else False


def _nonnegative_int(value: object, *, max_value: int = 10_000_000) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number <= max_value else None


def _safe_metadata_value(value: object, *, depth: int = 0) -> object:
    if depth > 3:
        return None
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in list(value.items())[:32]:
            key = _bounded_text(raw_key, max_length=64)
            if not key or key.lower() in _SENSITIVE_KEYS:
                continue
            result[key] = _safe_metadata_value(raw_value, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_metadata_value(item, depth=depth + 1) for item in list(value)[:32]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _bounded_text(value, max_length=512) if isinstance(value, str) else value
    return None


def _pricing_tiers(value: object) -> list[dict[str, object]]:
    if isinstance(value, Mapping):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        return []
    result: list[dict[str, object]] = []
    for item in values[:32]:
        if not isinstance(item, Mapping):
            continue
        range_name = _bounded_text(item.get("range_name"), max_length=64)
        if not range_name:
            continue
        prices = item.get("prices", item.get("items"))
        normalized_prices: list[dict[str, object]] = []
        if isinstance(prices, (list, tuple)):
            for price in list(prices)[:32]:
                if not isinstance(price, Mapping):
                    continue
                normalized: dict[str, object] = {}
                for key in ("type", "price", "price_unit", "price_name"):
                    if key in price and price[key] is not None:
                        safe = _safe_metadata_value(price[key])
                        if safe is not None:
                            normalized[key] = safe
                if normalized:
                    normalized_prices.append(normalized)
        result.append({"range_name": range_name, "items": normalized_prices})
    return result


def _value_from_sections(
    item: Mapping[str, object], sections: tuple[Mapping[str, object], ...], *keys: str
) -> object:
    value = _lookup(item, *keys)
    if value is not None:
        return value
    for section in sections:
        value = _lookup(section, *keys)
        if value is not None:
            return value
    return None


def _model_category(
    model_id: str,
    model_name: str,
    capabilities: list[str],
    ) -> ModelCategory:
    upper_name = f"{model_id} {model_name}".upper()
    upper_caps = {value.upper() for value in capabilities}
    if upper_caps & {"MULTIMODAL-OMNI", "REALTIME-OMNI"}:
        return ModelCategory.OMNI
    if "IG" in upper_caps:
        return ModelCategory.IMAGE_GENERATION
    if "VG" in upper_caps:
        return ModelCategory.VIDEO_GENERATION
    if "3D-GENERATION" in upper_caps:
        return ModelCategory.THREE_D
    if upper_caps & {
        "ASR",
        "TTS",
        "REALTIME-TEXT-TO-SPEECH",
        "REALTIME-ASR",
        "REALTIME-AUDIO-TRANSLATE",
        "REALTIME-CHATTING",
    }:
        return ModelCategory.AUDIO
    if upper_caps & {"TR", "ME"}:
        return ModelCategory.EMBEDDING
    if (
        "VU" in upper_caps
        and any(value in upper_name for value in ("-VL", "_VL", "QVQ", "OCR"))
    ) or ("VU" in upper_caps and "TG" not in upper_caps):
        return ModelCategory.VISION
    if "TG" in upper_caps:
        return ModelCategory.LLM
    if "VU" in upper_caps:
        return ModelCategory.VISION
    return ModelCategory.OTHER


def _runtime_model_type(category: ModelCategory) -> ModelType:
    if category in {
        ModelCategory.VISION,
        ModelCategory.OMNI,
        ModelCategory.IMAGE_GENERATION,
        ModelCategory.VIDEO_GENERATION,
        ModelCategory.THREE_D,
    }:
        return ModelType.VISION
    if category is ModelCategory.EMBEDDING:
        return ModelType.EMBEDDING
    return ModelType.TEXT


def _normalize_model(item: Mapping[str, object]) -> CatalogModel | None:
    model_info = item.get("model_info")
    inference_metadata = item.get("inference_metadata")
    sections = tuple(
        section
        for section in (model_info, inference_metadata)
        if isinstance(section, Mapping)
    )
    model_id = _bounded_text(
        _value_from_sections(
            item, sections, "model", "model_id", "id", "model_name", "name"
        ),
        max_length=128,
    )
    if not model_id:
        return None
    name = _bounded_text(
        _value_from_sections(item, sections, "name", "model_name", "display_name"),
        max_length=128,
    ) or model_id
    description = _bounded_text(
        _value_from_sections(item, sections, "description", "desc"), max_length=4000
    )
    capabilities = _string_list(
        _value_from_sections(item, sections, "capabilities", "capability")
    )
    features = _string_list(_value_from_sections(item, sections, "features", "feature"))
    input_modalities = _string_list(
        _value_from_sections(
            item,
            sections,
            "input_modalities",
            "input_modalities_supported",
            "request_modality",
        )
    )
    output_modalities = _string_list(
        _value_from_sections(
            item,
            sections,
            "output_modalities",
            "output_modalities_supported",
            "response_modality",
        )
    )
    model_category = _model_category(model_id, name, capabilities)
    model_type = _runtime_model_type(model_category)
    supports_reasoning = _as_bool(
        _value_from_sections(item, sections, "supports_reasoning", "reasoning")
    ) or any(value.upper() == "REASONING" for value in capabilities)
    supports_tool_call = _as_bool(
        _value_from_sections(item, sections, "supports_tool_call", "tool_call")
    ) or any("FUNCTION-CALLING" in value.upper() for value in features)
    supports_structured_output = _as_bool(
        _value_from_sections(
            item, sections, "supports_structured_output", "structured_output"
        )
    ) or any("STRUCTURED-OUTPUT" in value.upper() for value in features)
    context_window = _nonnegative_int(
        _value_from_sections(item, sections, "context_window", "max_context")
    )
    max_input_tokens = _nonnegative_int(
        _value_from_sections(item, sections, "max_input_tokens")
    )
    max_output_tokens = _nonnegative_int(
        _value_from_sections(item, sections, "max_output_tokens")
    )
    max_reasoning_tokens = _nonnegative_int(
        _value_from_sections(item, sections, "max_reasoning_tokens")
    )
    reasoning_max_input_tokens = _nonnegative_int(
        _value_from_sections(item, sections, "reasoning_max_input_tokens")
    )
    reasoning_max_output_tokens = _nonnegative_int(
        _value_from_sections(item, sections, "reasoning_max_output_tokens")
    )
    pricing_tiers = _pricing_tiers(
        _value_from_sections(item, sections, "pricing_tiers", "prices")
    )
    published_time = _bounded_text(
        _value_from_sections(item, sections, "published_time", "published_at"),
        max_length=64,
    )
    inference_provider = _bounded_text(
        _value_from_sections(item, sections, "inference_provider"),
        max_length=64,
    )
    provider = _bounded_text(
        _value_from_sections(item, sections, "provider"), max_length=64
    ) or "UNKNOWN"
    output_upper = {value.upper() for value in output_modalities}
    unsupported_reason: str | None = None
    if model_category is ModelCategory.EMBEDDING:
        unsupported_reason = "当前平台网关暂不支持 Embedding 模型"
    elif model_category is ModelCategory.OMNI:
        unsupported_reason = "当前平台网关暂不支持 Omni 模型"
    elif model_category is ModelCategory.AUDIO:
        unsupported_reason = "当前平台网关暂不支持音频/语音模型"
    elif model_category is ModelCategory.IMAGE_GENERATION:
        unsupported_reason = "当前平台网关暂不支持图片生成模型"
    elif model_category is ModelCategory.VIDEO_GENERATION:
        unsupported_reason = "当前平台网关暂不支持视频生成模型"
    elif model_category is ModelCategory.THREE_D:
        unsupported_reason = "当前平台网关暂不支持 3D 生成模型"
    elif model_category is ModelCategory.OTHER:
        unsupported_reason = "无法确认该模型的可执行主分类"
    elif not output_upper or output_upper - {"TEXT", "TEXTUAL"}:
        unsupported_reason = "当前平台网关仅支持文本/视觉理解输出"
    supported = unsupported_reason is None
    if not supported and unsupported_reason is None:
        unsupported_reason = "当前平台网关不支持该模型类型"
    return CatalogModel(
        model_id=model_id,
        name=name,
        description=description,
        provider=provider,
        inference_provider=inference_provider,
        model_category=model_category,
        model_type=model_type,
        capabilities=capabilities,
        features=features,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        supports_reasoning=supports_reasoning,
        supports_tool_call=supports_tool_call,
        supports_structured_output=supports_structured_output,
        context_window=context_window,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_reasoning_tokens=max_reasoning_tokens,
        reasoning_max_input_tokens=reasoning_max_input_tokens,
        reasoning_max_output_tokens=reasoning_max_output_tokens,
        pricing_tiers=pricing_tiers,
        published_time=published_time,
        supported_by_platform=supported,
        unsupported_reason=unsupported_reason,
    )


def _extract_models(payload: object) -> tuple[list[Mapping[str, object]], int | None]:
    if isinstance(payload, list):
        values = payload
        total = None
    elif isinstance(payload, Mapping):
        if payload.get("success") is False:
            raise BailianCatalogError("UPSTREAM_ERROR")
        output = payload.get("output")
        if isinstance(output, Mapping) and output.get("success") is False:
            raise BailianCatalogError("UPSTREAM_ERROR")
        source: Mapping[str, object] = output if isinstance(output, Mapping) else payload
        values: object = None
        for key in ("models", "data", "items", "result"):
            candidate = source.get(key)
            if isinstance(candidate, list):
                values = candidate
                break
            if isinstance(candidate, Mapping):
                for nested_key in ("models", "items", "data"):
                    nested = candidate.get(nested_key)
                    if isinstance(nested, list):
                        values = nested
                        break
                if isinstance(values, list):
                    break
        total = _nonnegative_int(
            _lookup(source, "total", "total_count", "total_num"), max_value=10_000_000
        )
        if not isinstance(values, list):
            raise BailianCatalogError("INVALID_RESPONSE")
    else:
        raise BailianCatalogError("INVALID_RESPONSE")
    rows = [item for item in values if isinstance(item, Mapping)]
    return rows, total


def _read_response_body(response: httpx.Response) -> bytes:
    content_length = response.headers.get("content-length")
    try:
        if content_length is not None and int(content_length) > _MAX_RESPONSE_BYTES:
            raise BailianCatalogError("RESPONSE_TOO_LARGE")
    except ValueError:
        pass
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes(64 * 1024):
        size += len(chunk)
        if size > _MAX_RESPONSE_BYTES:
            raise BailianCatalogError("RESPONSE_TOO_LARGE")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_bailian_catalog(*, base_url: str, api_key: str, query: ModelCatalogQuery) -> CatalogPage:
    url = build_bailian_catalog_url(base_url)
    try:
        with _build_client() as client, client.stream(
            "GET",
            url,
            params=build_bailian_catalog_params(query),
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        ) as response:
            if 300 <= response.status_code < 400:
                raise BailianCatalogError("REDIRECT_NOT_ALLOWED")
            if response.status_code >= 400:
                raise BailianCatalogError("UPSTREAM_ERROR")
            body = _read_response_body(response)
    except BailianCatalogError:
        raise
    except httpx.TimeoutException as exc:
        raise BailianCatalogError("TIMEOUT") from exc
    except httpx.HTTPError as exc:
        raise BailianCatalogError("NETWORK_ERROR") from exc
    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise BailianCatalogError("INVALID_RESPONSE") from exc
    rows, total = _extract_models(payload)
    normalized = [model for row in rows if (model := _normalize_model(row)) is not None]
    return CatalogPage(
        items=normalized,
        total=total if total is not None else len(normalized),
        source=_BAILIAN_PROVIDER,
        fetched_at=datetime.now(UTC),
    )


def default_prices(pricing_tiers: list[dict[str, object]]) -> tuple[Decimal, Decimal]:
    for tier in pricing_tiers:
        range_name = str(tier.get("range_name", "")).strip().upper()
        if range_name != "DEFAULT":
            continue
        input_price = Decimal("0")
        output_price = Decimal("0")
        for item in tier.get("items", []):
            if not isinstance(item, Mapping):
                continue
            price_type = str(item.get("type", item.get("price_name", ""))).lower()
            price_type = price_type.replace("-", "_").replace(" ", "_")
            value = item.get("price")
            try:
                parsed = Decimal(str(value)) if value is not None else Decimal("0")
            except (InvalidOperation, ValueError):
                continue
            if parsed < 0:
                continue
            if "input_token" in price_type:
                input_price = parsed
            elif "output_token" in price_type:
                output_price = parsed
        return input_price, output_price
    return Decimal("0"), Decimal("0")
