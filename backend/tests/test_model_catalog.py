from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db_session
from app.main import app
from app.modules.auth.schemas import CurrentUser
from app.modules.model_center import catalog
from app.modules.model_center.models import ModelConfiguration, ModelProviderConnection
from app.modules.model_center.schemas import ModelCatalogCapability
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def catalog_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (ModelProviderConnection.__table__, ModelConfiguration.__table__)
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    def override() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    try:
        with TestClient(app) as client:
            client.testing_session_factory = session_factory
            yield client
    finally:
        app.dependency_overrides.clear()
        uninstall_test_auth(engine)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _auth(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _connection_payload(
    name: str,
    *,
    provider: str = "ALIYUN_BAILIAN",
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key: str | None = "catalog-key",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "provider": provider,
        "protocol_type": "OPENAI_COMPATIBLE",
        "base_url": base_url,
    }
    if api_key is not None:
        payload["api_key"] = api_key
    return payload


def _create_connection(
    client: TestClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    name: str = "百炼渠道",
    **kwargs: Any,
) -> dict[str, Any]:
    monkeypatch.setattr(
        "app.modules.model_center.service.encrypt_secret",
        lambda value, _key: f"encrypted:{value}",
    )
    response = client.post(
        "/api/v1/model-center/connections",
        headers=headers,
        json=_connection_payload(name, **kwargs),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _install_catalog_response(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, json=payload, headers=headers)

    monkeypatch.setattr(
        catalog,
        "_build_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False, timeout=15
        ),
    )
    monkeypatch.setattr(
        "app.modules.model_center.service.decrypt_secret",
        lambda _value, _key: "catalog-key",
    )
    return requests


def _model_payload(model_id: str = "qwen-plus") -> dict[str, Any]:
    return {
        "model": model_id,
        "name": "通义千问3-Max",
        "provider": "qwen",
        "inference_provider": "aliyun-bailian",
        "description": "official description",
        "inference_metadata": {
            "request_modality": ["text"],
            "response_modality": ["text"],
        },
        "capabilities": ["TG", "Reasoning"],
        "features": ["function-calling", "structured-outputs"],
        "context_window": 131072,
        "max_input_tokens": 100000,
        "max_output_tokens": 8192,
        "max_reasoning_tokens": 4096,
        "reasoning_max_input_tokens": 90000,
        "reasoning_max_output_tokens": 4096,
        "prices": [
            {
                "range_name": "Default",
                "prices": [
                    {
                        "type": "input_token",
                        "price": "0.8",
                        "price_unit": "CNY/1M tokens",
                        "price_name": "Input tokens",
                    },
                    {
                        "type": "output_token",
                        "price": "2.4",
                        "price_unit": "CNY/1M tokens",
                        "price_name": "Output tokens",
                    },
                ],
            }
        ],
        "published_time": "2026-01-02T03:04:05Z",
    }


def test_catalog_query_maps_filters_and_normalizes_official_metadata(
    catalog_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _auth(catalog_client)
    connection = _create_connection(catalog_client, headers, monkeypatch)
    requests = _install_catalog_response(
        monkeypatch,
        {"success": True, "output": {"total": 1, "models": [_model_payload()]}},
    )

    response = catalog_client.get(
        f"/api/v1/model-center/connections/{connection['id']}/catalog/models",
        headers=headers,
        params={
            "q": "qwen",
            "model_type": "TEXT",
            "reasoning": "true",
            "tool_call": "true",
            "structured_output": "true",
            "page": 2,
            "page_size": 10,
        },
    )
    assert response.status_code == 200, response.text
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == (
        "https://dashscope.aliyuncs.com/api/v1/models?"
        "page_no=2&page_size=10&name=qwen&capabilities=TG&capabilities=Reasoning&"
        "features=function-calling&features=structured-outputs"
    )
    assert request.headers["authorization"] == "Bearer catalog-key"
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["model_id"] == "qwen-plus"
    assert item["name"] == "通义千问3-Max"
    assert item["provider"] == "qwen"
    assert item["inference_provider"] == "aliyun-bailian"
    assert item["model_category"] == "LLM"
    assert item["model_type"] == "TEXT"
    assert item["supports_reasoning"] is True
    assert item["supports_tool_call"] is True
    assert item["supports_structured_output"] is True
    assert item["context_window"] == 131072
    assert item["supported_by_platform"] is True
    assert item["published_time"] == "2026-01-02T03:04:05Z"
    assert item["input_modalities"] == ["text"]
    assert item["output_modalities"] == ["text"]
    assert body["connection_id"] == connection["id"]
    assert body["source"] == "ALIYUN_BAILIAN"
    assert body["fetched_at"].endswith(("Z", "+00:00"))


def test_catalog_accepts_the_full_repeated_official_capability_filter(
    catalog_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _auth(catalog_client)
    connection = _create_connection(catalog_client, headers, monkeypatch)
    requests = _install_catalog_response(
        monkeypatch, {"success": True, "output": {"total": 0, "models": []}}
    )
    capabilities = [capability.value for capability in ModelCatalogCapability]
    requested_capabilities = [("capabilities", capability) for capability in capabilities]
    requested_capabilities.append(("capabilities", "TG"))

    response = catalog_client.get(
        f"/api/v1/model-center/connections/{connection['id']}/catalog/models",
        headers=headers,
        params=requested_capabilities,
    )

    assert response.status_code == 200, response.text
    assert len(requests) == 1
    assert requests[0].url.params.get_list("capabilities") == capabilities


def test_catalog_import_requeries_exact_model_and_persists_official_readonly_metadata(
    catalog_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _auth(catalog_client)
    connection = _create_connection(catalog_client, headers, monkeypatch)
    requests = _install_catalog_response(
        monkeypatch,
        {"success": True, "output": {"total": 1, "models": [_model_payload()]}},
    )

    response = catalog_client.post(
        f"/api/v1/model-center/connections/{connection['id']}/catalog/models/import",
        headers=headers,
        json={"model_id": "qwen-plus", "configuration_name": "Qwen 官方模型"},
    )
    assert response.status_code == 201, response.text
    assert len(requests) == 1
    assert requests[0].url.params["model"] == "qwen-plus"
    assert "name" not in requests[0].url.params
    body = response.json()
    assert body["model_name"] == "qwen-plus"
    assert body["model_vendor"] == "qwen"
    assert body["metadata_source"] == "ALIYUN_BAILIAN"
    assert body["description"] == "official description"
    assert body["input_price"] == "0.800000"
    assert body["output_price"] == "2.400000"
    assert body["model_category"] == "LLM"
    assert body["max_context"] == 131072
    assert "catalog-key" not in response.text
    assert "provider" not in body

    with catalog_client.testing_session_factory() as session:
        model = session.get(ModelConfiguration, body["id"])
        assert model is not None
        assert model.metadata_source == "ALIYUN_BAILIAN"
        assert model.pricing_tiers == [
            {
                "range_name": "Default",
                "items": [
                    {
                        "type": "input_token",
                        "price": "0.8",
                        "price_unit": "CNY/1M tokens",
                        "price_name": "Input tokens",
                    },
                    {
                        "type": "output_token",
                        "price": "2.4",
                        "price_unit": "CNY/1M tokens",
                        "price_name": "Output tokens",
                    },
                ],
            }
        ]

    readonly = catalog_client.patch(
        f"/api/v1/model-center/{body['id']}",
        headers=headers,
        json={"model_name": "tampered-model"},
    )
    assert readonly.status_code == 409

    for field, value in (
        ("connection_id", connection["id"] + 1),
        ("input_price", "99"),
        ("output_price", "99"),
    ):
        blocked_update = catalog_client.patch(
            f"/api/v1/model-center/{body['id']}",
            headers=headers,
            json={field: value},
        )
        assert blocked_update.status_code == 409

    duplicate = catalog_client.post(
        f"/api/v1/model-center/connections/{connection['id']}/catalog/models/import",
        headers=headers,
        json={"model_id": "qwen-plus", "configuration_name": "Qwen 官方模型"},
    )
    assert duplicate.status_code == 409

    allowed_update = catalog_client.patch(
        f"/api/v1/model-center/{body['id']}",
        headers=headers,
        json={
            "name": "Qwen 官方模型重命名",
            "timeout_seconds": 120,
            "enabled": False,
        },
    )
    assert allowed_update.status_code == 200
    assert allowed_update.json()["name"] == "Qwen 官方模型重命名"


@pytest.mark.parametrize(
    ("model_id", "capabilities", "expected_category", "expected_type", "supported"),
    [
        ("qwen3.7-plus", ["TG", "Reasoning", "VU"], "LLM", "TEXT", True),
        ("qwen3-vl", ["TG", "VU"], "VISION", "VISION", True),
        ("omni-model", ["Multimodal-Omni"], "OMNI", "VISION", False),
        ("realtime-audio-model", ["Realtime-ASR"], "AUDIO", "TEXT", False),
        ("embedding-model", ["TR"], "EMBEDDING", "EMBEDDING", False),
        ("image-generation-model", ["IG"], "IMAGE_GENERATION", "VISION", False),
        ("video-generation-model", ["VG"], "VIDEO_GENERATION", "VISION", False),
        ("three-d-model", ["3D-generation"], "THREE_D", "VISION", False),
    ],
)
def test_catalog_classifies_official_primary_categories(
    catalog_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
    capabilities: list[str],
    expected_category: str,
    expected_type: str,
    supported: bool,
) -> None:
    headers = _auth(catalog_client)
    connection = _create_connection(catalog_client, headers, monkeypatch)
    payload = _model_payload(model_id)
    payload.update(
        {
            "capabilities": capabilities,
            "output_modalities": ["Text"],
            "inference_metadata": {
                "request_modality": ["Text", "Image", "Video"],
                "response_modality": ["Text"],
            },
        }
    )
    _install_catalog_response(
        monkeypatch, {"success": True, "output": {"models": [payload]}}
    )

    listed = catalog_client.get(
        f"/api/v1/model-center/connections/{connection['id']}/catalog/models",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    item = listed.json()["items"][0]
    assert item["model_category"] == expected_category
    assert item["model_type"] == expected_type
    assert item["capabilities"] == capabilities
    assert item["input_modalities"] == ["Text", "Image", "Video"]
    assert item["output_modalities"] == ["Text"]
    assert item["supported_by_platform"] is supported

    imported = catalog_client.post(
        f"/api/v1/model-center/connections/{connection['id']}/catalog/models/import",
        headers=headers,
        json={"model_id": model_id},
    )
    assert imported.status_code == (201 if supported else 409), imported.text
    if supported:
        assert imported.json()["model_category"] == expected_category


@pytest.mark.parametrize(
    ("capabilities", "output_modalities", "expected_type", "supported", "reason"),
    [
        (["ME"], ["embedding"], "EMBEDDING", False, "Embedding"),
        (["VG"], ["image"], "VISION", False, "视频"),
    ],
)
def test_catalog_displays_unsupported_models_but_rejects_import(
    catalog_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capabilities: list[str],
    output_modalities: list[str],
    expected_type: str,
    supported: bool,
    reason: str,
) -> None:
    headers = _auth(catalog_client)
    connection = _create_connection(catalog_client, headers, monkeypatch)
    payload = _model_payload("unsupported-model")
    payload.update({"capabilities": capabilities, "output_modalities": output_modalities})
    _install_catalog_response(
        monkeypatch, {"success": True, "output": {"models": [payload]}}
    )

    listed = catalog_client.get(
        f"/api/v1/model-center/connections/{connection['id']}/catalog/models",
        headers=headers,
    )
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["model_type"] == expected_type
    assert item["supported_by_platform"] is supported
    assert reason in item["unsupported_reason"]

    imported = catalog_client.post(
        f"/api/v1/model-center/connections/{connection['id']}/catalog/models/import",
        headers=headers,
        json={"model_id": "unsupported-model"},
    )
    assert imported.status_code == 409


def test_catalog_requires_bailian_key_enabled_connection_and_admin(
    catalog_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _auth(catalog_client)
    no_key = _create_connection(
        catalog_client, headers, monkeypatch, "无 Key 百炼渠道", api_key=None
    )
    response = catalog_client.get(
        f"/api/v1/model-center/connections/{no_key['id']}/catalog/models", headers=headers
    )
    assert response.status_code == 409
    assert "API Key" in response.json()["message"]

    other = _create_connection(
        catalog_client, headers, monkeypatch, "其他渠道", provider="OPENAI"
    )
    unsupported = catalog_client.get(
        f"/api/v1/model-center/connections/{other['id']}/catalog/models", headers=headers
    )
    assert unsupported.status_code == 409
    assert "百炼" in unsupported.json()["message"]

    disabled = _create_connection(catalog_client, headers, monkeypatch, "停用百炼")
    catalog_client.patch(
        f"/api/v1/model-center/connections/{disabled['id']}",
        headers=headers,
        json={"enabled": False},
    )
    blocked = catalog_client.get(
        f"/api/v1/model-center/connections/{disabled['id']}/catalog/models",
        headers=headers,
    )
    assert blocked.status_code == 409

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="viewer", username="viewer", display_name="Viewer", roles=["VIEWER"]
    )
    try:
        forbidden = catalog_client.get(
            f"/api/v1/model-center/connections/{other['id']}/catalog/models",
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert forbidden.status_code == 403


def test_catalog_rejects_ssrf_host_and_known_endpoint_is_derived(
    catalog_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _auth(catalog_client)
    unsafe = _create_connection(
        catalog_client,
        headers,
        monkeypatch,
        "恶意域名",
        base_url="https://evil.example/compatible-mode/v1",
    )
    requests = _install_catalog_response(
        monkeypatch, {"success": True, "output": {"models": []}}
    )
    blocked = catalog_client.get(
        f"/api/v1/model-center/connections/{unsafe['id']}/catalog/models",
        headers=headers,
    )
    assert blocked.status_code == 409
    assert requests == []


@pytest.mark.parametrize(
    "base_url",
    [
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "https://workspace.eu-central-1.maas.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_catalog_allows_official_workspace_domains(base_url: str) -> None:
    assert catalog.build_bailian_catalog_url(base_url).endswith("/api/v1/models")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://workspace.maas.aliyuncs.com.evil.example/compatible-mode/v1",
        "https://user:password@dashscope.aliyuncs.com/compatible-mode/v1",
        "http://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope.aliyuncs.com:8443/compatible-mode/v1",
    ],
)
def test_catalog_rejects_non_official_workspace_endpoints(base_url: str) -> None:
    with pytest.raises(catalog.BailianCatalogError, match="UNSAFE_ENDPOINT"):
        catalog.build_bailian_catalog_url(base_url)


@pytest.mark.parametrize("failure", ["redirect", "timeout", "invalid", "large"])
def test_catalog_upstream_failures_are_bounded_and_safe(
    catalog_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    headers = _auth(catalog_client)
    connection = _create_connection(catalog_client, headers, monkeypatch, f"失败-{failure}")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://evil.example/secret"})
        if failure == "timeout":
            raise httpx.ReadTimeout("upstream secret timeout")
        if failure == "invalid":
            return httpx.Response(200, content=b"not-json-secret")
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))

    monkeypatch.setattr(
        catalog,
        "_build_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False, timeout=15
        ),
    )
    monkeypatch.setattr(
        "app.modules.model_center.service.decrypt_secret",
        lambda _value, _key: "catalog-key",
    )
    response = catalog_client.get(
        f"/api/v1/model-center/connections/{connection['id']}/catalog/models",
        headers=headers,
    )
    assert response.status_code == 503
    assert "secret" not in response.text.lower()
    assert "catalog-key" not in response.text
    assert requests
