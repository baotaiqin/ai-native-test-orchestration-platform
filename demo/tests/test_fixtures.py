import csv
import json
from pathlib import Path

import pytest

from demo.server import DEMO_PASSWORD, DEMO_USERNAME, running_demo
from demo.tests.test_server import call

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_openapi_fixture_is_importable_with_auth_and_resolved_resource_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(FIXTURES.parents[1] / "backend"))
    from app.modules.api_definitions.parser import parse_openapi

    content = (FIXTURES / "openapi.json").read_text(encoding="utf-8")
    document = json.loads(content)
    metadata, operations = parse_openapi(content)
    assert metadata["spec_version"] == "3.0.3"
    assert len(operations) == 14
    indexed = {operation.operation_id: operation for operation in operations}
    assert len(indexed) == 14
    assert indexed["createResource"].request_schema["required"] == ["name"]
    assert indexed["deleteResource"].parameters[0]["in"] == "path"
    assert indexed["createResource"].auth_info
    assert indexed["createOrder"].request_schema["required"] == [
        "product_id",
        "quantity",
    ]
    assert indexed["deleteOrder"].parameters[0]["in"] == "path"
    assert "bearerAuth" in json.dumps(indexed["createResource"].auth_info)
    assert "cookieAuth" in json.dumps(indexed["createResource"].auth_info)
    assert "#/components/schemas/Resource" not in json.dumps(
        indexed["listResources"].response_schema
    )
    assert "#/components/schemas/Order" not in json.dumps(
        indexed["listOrders"].response_schema
    )
    assert document["servers"][0]["url"] == "http://127.0.0.1:8765"


def test_resource_dataset_rows_match_live_demo_and_cleanup() -> None:
    with (FIXTURES / "resources.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 5
    with running_demo() as url:
        status, payload, _ = call(
            url,
            "/api/login",
            "POST",
            {"username": DEMO_USERNAME, "password": DEMO_PASSWORD},
        )
        assert status == 200
        token = payload["data"]["token"]
        for row in rows:
            status, payload, _ = call(
                url, "/api/resources", "POST", {"name": row["name"]}, token
            )
            assert status == int(row["expected_status"]), row["row_key"]
            if status == 201:
                resource_id = payload["data"]["id"]
                assert payload["data"]["name"] == row["name"].strip()
                deleted, result, _ = call(
                    url, f"/api/resources/{resource_id}", "DELETE", token=token
                )
                assert deleted == 200 and result["deleted"] is True
        assert call(url, "/control/state")[1]["resource_count"] == 0
