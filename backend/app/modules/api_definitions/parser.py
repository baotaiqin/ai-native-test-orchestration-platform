import hashlib
import json
from copy import deepcopy
from typing import Any

import yaml

from app.core.exceptions import InvalidDocumentError
from app.modules.api_definitions.schemas import ParsedOperation

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _resolve_ref(document: dict[str, Any], value: Any, seen: set[str] | None = None) -> Any:
    if not isinstance(value, dict) or "$ref" not in value:
        return value
    reference = value["$ref"]
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return value
    seen = seen or set()
    if reference in seen:
        return {"$ref": reference}
    target: Any = document
    try:
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
    except (KeyError, TypeError):
        raise InvalidDocumentError(f"无法解析引用：{reference}") from None
    resolved = deepcopy(target)
    if isinstance(resolved, dict):
        resolved = {**resolved, **{key: val for key, val in value.items() if key != "$ref"}}
    return _expand_refs(document, resolved, seen | {reference})


def _expand_refs(document: dict[str, Any], value: Any, seen: set[str] | None = None) -> Any:
    if isinstance(value, dict):
        if "$ref" in value:
            return _resolve_ref(document, value, seen)
        return {key: _expand_refs(document, val, seen) for key, val in value.items()}
    if isinstance(value, list):
        return [_expand_refs(document, item, seen) for item in value]
    return value


def _schema_from_content(document: dict[str, Any], content: Any) -> dict[str, Any] | None:
    if not isinstance(content, dict) or not content:
        return None
    media = content.get("application/json") or next(iter(content.values()), None)
    if not isinstance(media, dict) or not isinstance(media.get("schema"), dict):
        return None
    return _expand_refs(document, media["schema"])


def _normalize_parameters(document: dict[str, Any], parameters: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in parameters if isinstance(parameters, list) else []:
        item = _resolve_ref(document, raw)
        if not isinstance(item, dict):
            continue
        schema = item.get("schema")
        if not isinstance(schema, dict) and item.get("type"):
            schema = {key: item[key] for key in ("type", "format", "items", "enum") if key in item}
        result.append(
            {
                "name": str(item.get("name", "")),
                "in": str(item.get("in", "query")),
                "required": bool(item.get("required", False)),
                "description": item.get("description"),
                "schema": _expand_refs(document, schema) if isinstance(schema, dict) else {},
            }
        )
    return result


def _contract_hash(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_openapi(content: str) -> tuple[dict[str, Any], list[ParsedOperation]]:
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise InvalidDocumentError("JSON/YAML 格式错误", details=str(exc)) from exc
    if not isinstance(document, dict):
        raise InvalidDocumentError("OpenAPI 文档根节点必须是对象")
    spec_version = str(document.get("openapi") or document.get("swagger") or "")
    if not (spec_version.startswith("3.") or spec_version.startswith("2.")):
        raise InvalidDocumentError("仅支持 OpenAPI 3.x 或 Swagger 2.x 文档")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise InvalidDocumentError("文档缺少 paths 对象")

    global_security = document.get("security", [])
    schemes = (
        document.get("components", {}).get("securitySchemes", {})
        if spec_version.startswith("3.")
        else document.get("securityDefinitions", {})
    )
    operations: list[ParsedOperation] = []
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        path_parameters = path_item.get("parameters", [])
        for method, raw_operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(raw_operation, dict):
                continue
            operation = _resolve_ref(document, raw_operation)
            parameters = _normalize_parameters(
                document, [*path_parameters, *operation.get("parameters", [])]
            )
            request_schema = None
            if spec_version.startswith("3."):
                request_body = _resolve_ref(document, operation.get("requestBody", {}))
                request_schema = (
                    _schema_from_content(document, request_body.get("content", {}))
                    if isinstance(request_body, dict)
                    else None
                )
            else:
                body = next((item for item in parameters if item["in"] == "body"), None)
                request_schema = body["schema"] if body else None

            responses: dict[str, Any] = {}
            for code, raw_response in operation.get("responses", {}).items():
                response = _resolve_ref(document, raw_response)
                if not isinstance(response, dict):
                    continue
                schema = (
                    _schema_from_content(document, response.get("content", {}))
                    if spec_version.startswith("3.")
                    else _expand_refs(document, response.get("schema"))
                )
                responses[str(code)] = {
                    "description": response.get("description"),
                    "schema": schema,
                }

            security = operation.get("security", global_security)
            auth_names = (
                sorted(
                    {
                        name
                        for requirement in security
                        if isinstance(requirement, dict)
                        for name in requirement
                    }
                )
                if isinstance(security, list)
                else []
            )
            auth_info = {
                "requirements": security if isinstance(security, list) else [],
                "schemes": {
                    name: _expand_refs(document, schemes.get(name, {}))
                    for name in auth_names
                },
            }
            summary = operation.get("summary")
            operation_id = operation.get("operationId")
            contract = {
                "method": method.upper(), "path": path, "summary": summary,
                "description": operation.get("description"), "parameters": parameters,
                "request_schema": request_schema, "response_schema": responses,
                "auth_info": auth_info, "tags": operation.get("tags", []),
            }
            operations.append(
                ParsedOperation(
                    name=summary or operation_id or f"{method.upper()} {path}",
                    method=method.upper(), path=path, operation_id=operation_id,
                    summary=summary, description=operation.get("description"),
                    parameters=parameters, request_schema=request_schema,
                    response_schema=responses, auth_info=auth_info,
                    tags=[str(tag) for tag in operation.get("tags", [])],
                    contract_hash=_contract_hash(contract),
                )
            )
    if not operations:
        raise InvalidDocumentError("文档中没有可导入的 API 操作")
    metadata = {
        "spec_version": spec_version,
        "title": (
            document.get("info", {}).get("title")
            if isinstance(document.get("info"), dict)
            else None
        ),
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
    }
    return metadata, operations
