import json

from runner.main import capability_snapshot


def test_capability_snapshot_matches_runner_protocol() -> None:
    snapshot = capability_snapshot()

    assert snapshot["name"] == "runner"
    assert set(item["name"] for item in snapshot["capabilities"]) == {
        "API",
        "WEB",
        "SQL",
        "SCRIPT",
        "SSE",
        "JMETER",
    }
    assert {item["type"] for item in snapshot["slots"]} == {
        "API",
        "WEB",
        "PERFORMANCE",
    }
    json.dumps(snapshot, ensure_ascii=False)
