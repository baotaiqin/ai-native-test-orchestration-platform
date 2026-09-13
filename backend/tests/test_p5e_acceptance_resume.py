import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT_PATH = WORKSPACE / "deploy" / "p5e_live_acceptance.py"
SPEC = importlib.util.spec_from_file_location("p5e_live_acceptance_test", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
p5e = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = p5e
SPEC.loader.exec_module(p5e)
sys.modules["p5e_live_acceptance"] = p5e

CALL_19_SCRIPT_PATH = WORKSPACE / "deploy" / "p5e_resume_failure_analysis.py"
CALL_19_SPEC = importlib.util.spec_from_file_location(
    "p5e_resume_failure_analysis_test", CALL_19_SCRIPT_PATH
)
assert CALL_19_SPEC is not None and CALL_19_SPEC.loader is not None
call_19_recovery = importlib.util.module_from_spec(CALL_19_SPEC)
sys.modules[CALL_19_SPEC.name] = call_19_recovery
CALL_19_SPEC.loader.exec_module(call_19_recovery)

RUNNER_ID = "1282a04dfd894f85877384cb31af5c16"


class StubApi:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **_kwargs: Any) -> Any:
        self.calls.append((method, path))
        if path not in self.responses:
            raise AssertionError(f"unexpected stub request: {method} {path}")
        return deepcopy(self.responses[path])


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "acceptance_id": p5e.KNOWN_CALL_16_ACCEPTANCE_ID,
        "status": "FAILED",
        "stage": "generate_and_reject_healing",
        "ids": {
            "project_id": p5e.KNOWN_CALL_16_PROJECT_ID,
            "model_binding_ids": [13, 14],
            "environment_id": 10,
            "runner_id": RUNNER_ID,
            "prompt_ids": {"locator_healing": 13, "web_failure_analysis": 14},
            "prompt_version_ids": {
                "locator_healing": 15,
                "web_failure_analysis": 16,
            },
            "output_schema_ids": {
                "locator_healing": 11,
                "web_failure_analysis": 12,
            },
            "web_element_id": p5e.KNOWN_CALL_16_ELEMENT_ID,
            "source_element_version_id": p5e.KNOWN_CALL_16_ELEMENT_VERSION_ID,
            "web_case_id": p5e.KNOWN_CALL_16_CASE_ID,
            "source_web_case_version_id": p5e.KNOWN_CALL_16_CASE_VERSION_ID,
            "failed_case_run_id": p5e.KNOWN_CALL_16_CASE_RUN_ID,
            "run_ids": {
                "baseline": p5e.KNOWN_CALL_16_BASELINE_RUN_ID,
                "failed_old_locator": p5e.KNOWN_CALL_16_FAILED_RUN_ID,
            },
        },
        "statuses": {
            "baseline_run": "SUCCESS",
            "demo_locator": "CHANGED",
            "error_code": (
                "HTTP_409_POST_/runs/"
                f"{p5e.KNOWN_CALL_16_FAILED_RUN_ID}/web-healing-proposals"
            ),
        },
        "counts": {
            "ai_business_calls": 1,
            "ai_business_calls_cumulative": 1,
        },
        "links": {
            "attempt_manifest": (
                f"attempts/{p5e.KNOWN_CALL_16_ACCEPTANCE_ID}.json"
            )
        },
    }


def _ledger() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "total_attempted": 1,
        "calls": [
            {
                "acceptance_id": p5e.KNOWN_CALL_16_ACCEPTANCE_ID,
                "attempt_ordinal": 1,
                "stage": "generate_and_reject_healing",
                "status": "ATTEMPTED",
            }
        ],
    }


def _stub_responses() -> dict[str, Any]:
    healing_context = {
        "schema_version": 1,
        "trigger": "ALL_LOCATORS_FAILED",
        "element_version_id": p5e.KNOWN_CALL_16_ELEMENT_VERSION_ID,
        "page_url": "http://127.0.0.1:8765/login",
        "page_title": "P5-E Login Demo",
        "dom_candidates": [
            {"tag": "input", "id": "username", "name": "username", "type": "text"},
            {
                "tag": "input",
                "id": "password",
                "name": "password",
                "type": "password",
            },
            {
                "tag": "button",
                "role": "button",
                "id": "signin-confirm",
                "data-testid": "signin-confirm",
                "type": "submit",
            },
        ],
    }
    failed_run = {
        "id": p5e.KNOWN_CALL_16_FAILED_RUN_ID,
        "project_id": p5e.KNOWN_CALL_16_PROJECT_ID,
        "environment_id": 10,
        "runner_id": RUNNER_ID,
        "run_type": "WEB_CASE",
        "status": "FAILED",
        "web_case_id": p5e.KNOWN_CALL_16_CASE_ID,
        "web_case_version_id": p5e.KNOWN_CALL_16_CASE_VERSION_ID,
        "case_runs": [
            {
                "id": p5e.KNOWN_CALL_16_CASE_RUN_ID,
                "status": "FAILED",
                "web_case_version_id": p5e.KNOWN_CALL_16_CASE_VERSION_ID,
            }
        ],
        "web_traces": [
            {
                "node_id": "action_1",
                "status": "FAILED",
                "error_type": "WEB_LOCATOR_NOT_FOUND",
                "healing_context": healing_context,
            }
        ],
    }
    case_versions = [
        {
            "id": p5e.KNOWN_CALL_16_CASE_VERSION_ID,
            "web_case_id": p5e.KNOWN_CALL_16_CASE_ID,
            "status": "APPROVED",
            "content": {
                "actions": [
                    {
                        "type": "CLICK",
                        "locator": {
                            "element_version_id": p5e.KNOWN_CALL_16_ELEMENT_VERSION_ID
                        },
                    }
                ]
            },
        }
    ]
    element_versions = [
        {
            "id": p5e.KNOWN_CALL_16_ELEMENT_VERSION_ID,
            "element_id": p5e.KNOWN_CALL_16_ELEMENT_ID,
            "locators": [
                {
                    "id": 20,
                    "strategy": "css",
                    "value": "#login-btn",
                    "priority": 1,
                    "source": "MANUAL",
                }
            ],
        }
    ]
    return {
        "/ai/calls": {
            "items": [
                {
                    "id": p5e.KNOWN_CALL_16_ID,
                    "project_id": p5e.KNOWN_CALL_16_PROJECT_ID,
                    "task_type": "LOCATOR_HEALING",
                    "entity_type": "WEB_HEALING_PROPOSAL",
                    "entity_id": "pending",
                    "model_config_id": 101,
                    "actual_model": "healing-model-fixed",
                    "prompt_version_id": 15,
                    "output_schema_id": 11,
                    "success": True,
                    "fallback_used": False,
                    "retry_count": 0,
                    "repair_used": False,
                    "error_type": None,
                    "parsed_result": {
                        "locator": {
                            "strategy": "css",
                            "value": "[data-testid='signin-confirm']",
                        },
                        "confidence": 0.95,
                        "reason": "safe known candidate",
                        "evidence_candidate_index": 2,
                    },
                    "validation_errors": [],
                }
            ]
        },
        "/model-center": {
            "items": [{"id": 101, "model_name": "healing-model-fixed"}]
        },
        f"/runs/{p5e.KNOWN_CALL_16_FAILED_RUN_ID}": failed_run,
        f"/web-cases/{p5e.KNOWN_CALL_16_CASE_ID}/versions": case_versions,
        f"/web-elements/{p5e.KNOWN_CALL_16_ELEMENT_ID}/versions": element_versions,
        (
            f"/runs/{p5e.KNOWN_CALL_16_FAILED_RUN_ID}/web-healing-proposals"
        ): {"items": [], "total": 0},
    }


def _write_resume_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict[str, Any],
    ledger: dict[str, Any],
) -> tuple[Path, Path, Path]:
    attempt_dir = tmp_path / "attempts"
    attempt_dir.mkdir()
    result_file = tmp_path / "result.json"
    ledger_file = tmp_path / "ai-call-ledger.json"
    attempt_file = attempt_dir / f"{p5e.KNOWN_CALL_16_ACCEPTANCE_ID}.json"
    encoded_manifest = json.dumps(manifest, ensure_ascii=False)
    result_file.write_text(encoded_manifest, encoding="utf-8")
    attempt_file.write_text(encoded_manifest, encoding="utf-8")
    ledger_file.write_text(json.dumps(ledger), encoding="utf-8")
    monkeypatch.setattr(p5e, "DEFAULT_ATTEMPT_DIR", attempt_dir)
    monkeypatch.setattr(p5e, "DEFAULT_AI_LEDGER_FILE", ledger_file)
    return result_file, attempt_file, ledger_file


def _acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, StubApi, tuple[Path, Path, Path]]:
    files = _write_resume_files(tmp_path, monkeypatch, _manifest(), _ledger())
    acceptance = p5e.LiveAcceptance(
        ready_file=tmp_path / "unused-ready.json",
        result_file=files[0],
        ordinary_timeout=20,
        ai_timeout=150,
        run_timeout=150,
        poll_interval=2,
        allow_new_attempt=False,
        resume_attempt=False,
        resume_after_call_16=True,
    )
    responses = _stub_responses()
    stub = StubApi(responses)
    acceptance.api = stub
    acceptance.project_id = p5e.KNOWN_CALL_16_PROJECT_ID
    acceptance.environment_id = 10
    acceptance.runner_id = RUNNER_ID
    acceptance.model_bindings_by_task = {
        "LOCATOR_HEALING": {
            "id": 13,
            "task_type": "LOCATOR_HEALING",
            "primary_model_id": 101,
            "fallback_model_id": None,
            "max_fallback": 0,
        }
    }
    digest = acceptance._known_call_16_legacy_source_digest()
    responses["/ai/calls"]["items"][0]["entity_id"] = digest
    return acceptance, stub, files


def test_call_16_resume_accepts_only_exact_audited_state_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acceptance, stub, files = _acceptance(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in files}

    acceptance._validate_resume_ledger()
    acceptance._validate_known_failed_healing_call()

    assert acceptance.ai_calls == 1
    assert all(method == "GET" for method, _path in stub.calls)
    assert {path: path.read_bytes() for path in files} == before


@pytest.mark.parametrize(
    ("drift", "expected_code"),
    [
        ("entity", "CALL_16_SOURCE_ENTITY_MISMATCH"),
        ("model", "CALL_16_METADATA_MISMATCH"),
        ("prompt", "CALL_16_METADATA_MISMATCH"),
        ("unknown_result", "CALL_16_KNOWN_RESULT_MISMATCH"),
        ("fixed_run", "CALL_16_FIXED_RUN_MISMATCH"),
        ("candidate_source", "CALL_16_CANDIDATE_SOURCE_MISMATCH"),
        ("materialized", "CALL_16_PROPOSAL_ALREADY_EXISTS"),
    ],
)
def test_call_16_resume_rejects_identity_result_source_and_materialization_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    expected_code: str,
) -> None:
    acceptance, stub, _files = _acceptance(tmp_path, monkeypatch)
    call = stub.responses["/ai/calls"]["items"][0]
    fixed_run = stub.responses[f"/runs/{p5e.KNOWN_CALL_16_FAILED_RUN_ID}"]
    if drift == "entity":
        call["entity_id"] = "0" * 64
    elif drift == "model":
        call["model_config_id"] = 102
    elif drift == "prompt":
        call["prompt_version_id"] = 999
    elif drift == "unknown_result":
        call["parsed_result"]["locator"]["value"] = "#unknown"
    elif drift == "fixed_run":
        fixed_run["web_case_id"] = 999
    elif drift == "candidate_source":
        fixed_run["web_traces"][0]["healing_context"]["dom_candidates"][2][
            "data-testid"
        ] = "other"
    elif drift == "materialized":
        proposals = stub.responses[
            f"/runs/{p5e.KNOWN_CALL_16_FAILED_RUN_ID}/web-healing-proposals"
        ]
        proposals.update({"items": [{"id": 77}], "total": 1})

    with pytest.raises(p5e.AcceptanceFailure) as exc_info:
        acceptance._validate_known_failed_healing_call()
    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    ("drift", "expected_code"),
    [
        ("duplicate", "RESUME_AI_LEDGER_DIVERGED"),
        ("ordinal", "CALL_16_RECOVERY_LEDGER_MISMATCH"),
        ("cumulative", "RESUME_AI_LEDGER_TOTAL_DIVERGED"),
    ],
)
def test_call_16_resume_rejects_tampered_or_duplicate_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    expected_code: str,
) -> None:
    acceptance, _stub, files = _acceptance(tmp_path, monkeypatch)
    ledger = _ledger()
    if drift == "duplicate":
        duplicate = deepcopy(ledger["calls"][0])
        duplicate["attempt_ordinal"] = 2
        ledger["calls"].append(duplicate)
        ledger["total_attempted"] = 2
        acceptance.safe.counts["ai_business_calls_cumulative"] = 2
    elif drift == "ordinal":
        ledger["calls"][0]["attempt_ordinal"] = 2
    elif drift == "cumulative":
        acceptance.safe.counts["ai_business_calls_cumulative"] = 2
    files[2].write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(p5e.AcceptanceFailure) as exc_info:
        acceptance._validate_resume_ledger()
    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    ("drift", "expected_code"),
    [
        ("call_count", "RESUME_AI_CALL_COUNT_UNEXPECTED"),
        ("run", "CALL_16_RECOVERY_RUN_MISMATCH"),
        ("asset", "CALL_16_RECOVERY_ASSET_MISMATCH"),
        ("proposal", "CALL_16_RECOVERY_ALREADY_MATERIALIZED"),
    ],
)
def test_call_16_resume_constructor_rejects_manifest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    expected_code: str,
) -> None:
    manifest = _manifest()
    if drift == "call_count":
        manifest["counts"]["ai_business_calls"] = 2
    elif drift == "run":
        manifest["ids"]["run_ids"]["failed_old_locator"] = "run_" + "0" * 32
    elif drift == "asset":
        manifest["ids"]["web_case_id"] = 999
    elif drift == "proposal":
        manifest["ids"]["healing_proposal_ids"] = {"rejected": 1}
    files = _write_resume_files(tmp_path, monkeypatch, manifest, _ledger())

    with pytest.raises(p5e.AcceptanceFailure) as exc_info:
        p5e.LiveAcceptance(
            ready_file=tmp_path / "unused-ready.json",
            result_file=files[0],
            ordinary_timeout=20,
            ai_timeout=150,
            run_timeout=150,
            poll_interval=2,
            allow_new_attempt=False,
            resume_attempt=False,
            resume_after_call_16=True,
        )
    assert exc_info.value.code == expected_code


def _call_19_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "acceptance_id": call_19_recovery.ACCEPTANCE_ID,
        "status": "FAILED",
        "stage": "generate_failure_analysis",
        "ids": {
            "project_id": call_19_recovery.PROJECT_ID,
            "model_binding_ids": [13, call_19_recovery.ANALYSIS_BINDING_ID],
            "environment_id": 10,
            "runner_id": RUNNER_ID,
            "prompt_ids": {
                "locator_healing": 13,
                "web_failure_analysis": call_19_recovery.ANALYSIS_PROMPT_ID,
            },
            "prompt_version_ids": {
                "locator_healing": 15,
                "web_failure_analysis": call_19_recovery.ANALYSIS_PROMPT_VERSION_ID,
            },
            "output_schema_ids": {
                "locator_healing": 11,
                "web_failure_analysis": call_19_recovery.ANALYSIS_OUTPUT_SCHEMA_ID,
            },
            "web_element_id": call_19_recovery.ELEMENT_ID,
            "source_element_version_id": call_19_recovery.SOURCE_ELEMENT_VERSION_ID,
            "healed_element_version_id": call_19_recovery.HEALED_ELEMENT_VERSION_ID,
            "web_case_id": call_19_recovery.CASE_ID,
            "source_web_case_version_id": call_19_recovery.SOURCE_CASE_VERSION_ID,
            "healed_web_case_version_id": call_19_recovery.HEALED_CASE_VERSION_ID,
            "failed_case_run_id": call_19_recovery.FAILED_CASE_RUN_ID,
            "run_ids": {
                "baseline": call_19_recovery.BASELINE_RUN_ID,
                "failed_old_locator": call_19_recovery.FAILED_RUN_ID,
                "healed": call_19_recovery.HEALED_RUN_ID,
            },
            "evidence_ids": {
                "baseline": ["artifact-baseline"],
                "failed_old_locator": ["artifact-failed"],
                "healed": ["artifact-healed"],
            },
            "healing_proposal_ids": {
                "rejected": call_19_recovery.REJECTED_PROPOSAL_ID,
                "accepted": call_19_recovery.ACCEPTED_PROPOSAL_ID,
            },
            "healing_ai_call_ids": [17, 18],
        },
        "statuses": {
            "baseline_run": "SUCCESS",
            "demo_locator": "CHANGED",
            "failed_old_locator_run": "FAILED",
            "rejected_healing_proposal": "REJECTED",
            "accepted_healing_proposal": "ACCEPTED",
            "healed_web_case": "APPROVED",
            "healed_run": "SUCCESS",
            "error_code": (
                "HTTP_409_POST_/runs/"
                f"{call_19_recovery.FAILED_RUN_ID}/web-failure-analyses"
            ),
        },
        "counts": {
            "ai_business_calls": 4,
            "ai_business_calls_cumulative": 4,
            "baseline_evidence": 1,
            "failed_old_locator_evidence": 1,
            "healed_evidence": 1,
        },
        "links": {
            "attempt_manifest": (
                f"attempts/{call_19_recovery.ACCEPTANCE_ID}.json"
            )
        },
    }


def _call_19_ledger() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "total_attempted": 4,
        "calls": deepcopy(call_19_recovery.EXPECTED_LEDGER_CALLS),
    }


class Call19StubApi:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.calls: list[tuple[str, str]] = []
        self.post_count = 0
        self.analysis_history_override: dict[str, Any] | None = None
        self.post_response = {
            "schema_version": 1,
            "id": 1,
            "project_id": call_19_recovery.PROJECT_ID,
            "run_id": call_19_recovery.FAILED_RUN_ID,
            "case_run_id": call_19_recovery.FAILED_CASE_RUN_ID,
            "status": "COMPLETED",
            "ai_call_id": 20,
            "actual_model": call_19_recovery.ANALYSIS_MODEL_NAME,
            "prompt_version_id": call_19_recovery.ANALYSIS_PROMPT_VERSION_ID,
            "output_schema_id": call_19_recovery.ANALYSIS_OUTPUT_SCHEMA_ID,
            "fallback_used": False,
            "repair_used": True,
            "source_snapshot_sha256": call_19_recovery.CALL_19_ENTITY_ID,
            "source_snapshot_size": 1024,
            "structured_result": {
                "failure_category": "LOCATOR_NOT_FOUND",
                "severity": "MEDIUM",
                "summary": "定位器未命中",
                "root_cause": "页面结构发生变化",
                "recommendations": ["检查失败节点"],
                "evidence_node_ids": ["action_1"],
                "confidence": 0.9,
                "needs_human_review": True,
            },
            "created_by": "dev-admin",
            "created_at": "2026-09-10T00:00:00+00:00",
        }
        self.responses = self._responses()

    def _run(
        self, run_id: str, status: str, version_id: int, case_run_id: int
    ) -> dict[str, Any]:
        trace_status = "FAILED" if status == "FAILED" else "SUCCESS"
        return {
            "id": run_id,
            "project_id": call_19_recovery.PROJECT_ID,
            "status": status,
            "web_case_id": call_19_recovery.CASE_ID,
            "web_case_version_id": version_id,
            "case_runs": [
                {
                    "id": case_run_id,
                    "status": status,
                    "web_case_version_id": version_id,
                }
            ],
            "web_traces": [
                {
                    "node_id": "action_1",
                    "status": trace_status,
                    "error_type": (
                        "WEB_LOCATOR_NOT_FOUND" if status == "FAILED" else None
                    ),
                }
            ],
        }

    def _responses(self) -> dict[str, Any]:
        return {
            "/projects": {
                "items": [
                    {"id": call_19_recovery.PROJECT_ID, "status": "ACTIVE"}
                ]
            },
            "/model-center/bindings/project": {
                "items": [
                    {
                        "id": call_19_recovery.ANALYSIS_BINDING_ID,
                        "task_type": "WEB_FAILURE_ANALYSIS",
                        "primary_model_id": call_19_recovery.ANALYSIS_MODEL_ID,
                        "fallback_model_id": None,
                        "max_fallback": 0,
                    }
                ]
            },
            "/model-center": {
                "items": [
                    {
                        "id": call_19_recovery.ANALYSIS_MODEL_ID,
                        "model_name": call_19_recovery.ANALYSIS_MODEL_NAME,
                        "enabled": True,
                        "supports_structured_output": True,
                    }
                ]
            },
            "/prompt-center": {
                "items": [
                    {
                        "id": call_19_recovery.ANALYSIS_PROMPT_ID,
                        "code": "P5_WEB_FAILURE_ANALYSIS_V1",
                        "enabled": True,
                        "current_version_id": (
                            call_19_recovery.ANALYSIS_PROMPT_VERSION_ID
                        ),
                        "current_version": {
                            "output_schema_id": (
                                call_19_recovery.ANALYSIS_OUTPUT_SCHEMA_ID
                            )
                        },
                    }
                ]
            },
            "/ai/output-schemas": {
                "items": [
                    {
                        "id": call_19_recovery.ANALYSIS_OUTPUT_SCHEMA_ID,
                        "name": "P5_WEB_FAILURE_ANALYSIS_V1",
                        "version_no": 1,
                        "enabled": True,
                    }
                ]
            },
            f"/runs/{call_19_recovery.BASELINE_RUN_ID}": self._run(
                call_19_recovery.BASELINE_RUN_ID,
                "SUCCESS",
                call_19_recovery.SOURCE_CASE_VERSION_ID,
                27,
            ),
            f"/runs/{call_19_recovery.FAILED_RUN_ID}": self._run(
                call_19_recovery.FAILED_RUN_ID,
                "FAILED",
                call_19_recovery.SOURCE_CASE_VERSION_ID,
                call_19_recovery.FAILED_CASE_RUN_ID,
            ),
            f"/runs/{call_19_recovery.HEALED_RUN_ID}": self._run(
                call_19_recovery.HEALED_RUN_ID,
                "SUCCESS",
                call_19_recovery.HEALED_CASE_VERSION_ID,
                call_19_recovery.HEALED_CASE_RUN_ID,
            ),
            f"/web-cases/{call_19_recovery.CASE_ID}": {
                "id": call_19_recovery.CASE_ID,
                "project_id": call_19_recovery.PROJECT_ID,
                "status": "APPROVED",
                "current_version_id": call_19_recovery.HEALED_CASE_VERSION_ID,
            },
            f"/web-cases/{call_19_recovery.CASE_ID}/versions": [
                {
                    "id": call_19_recovery.SOURCE_CASE_VERSION_ID,
                    "web_case_id": call_19_recovery.CASE_ID,
                    "version_no": 1,
                    "status": "APPROVED",
                    "content": {
                        "actions": [
                            {
                                "type": "CLICK",
                                "locator": {
                                    "element_version_id": (
                                        call_19_recovery.SOURCE_ELEMENT_VERSION_ID
                                    )
                                },
                            }
                        ]
                    },
                },
                {
                    "id": call_19_recovery.HEALED_CASE_VERSION_ID,
                    "web_case_id": call_19_recovery.CASE_ID,
                    "version_no": 2,
                    "status": "APPROVED",
                    "content": {
                        "actions": [
                            {
                                "type": "CLICK",
                                "locator": {
                                    "element_version_id": (
                                        call_19_recovery.HEALED_ELEMENT_VERSION_ID
                                    )
                                },
                            }
                        ]
                    },
                },
            ],
            f"/web-elements/{call_19_recovery.ELEMENT_ID}/versions": [
                {
                    "id": call_19_recovery.SOURCE_ELEMENT_VERSION_ID,
                    "element_id": call_19_recovery.ELEMENT_ID,
                    "locators": [
                        {
                            "strategy": "css",
                            "value": "#login-btn",
                            "priority": 1,
                            "source": "MANUAL",
                        }
                    ],
                },
                {
                    "id": call_19_recovery.HEALED_ELEMENT_VERSION_ID,
                    "element_id": call_19_recovery.ELEMENT_ID,
                    "locators": [
                        {
                            "strategy": "css",
                            "value": "[data-testid='signin-confirm']",
                            "priority": 1,
                            "source": "HEALED",
                        },
                        {
                            "strategy": "css",
                            "value": "#login-btn",
                            "priority": 2,
                            "source": "MANUAL",
                        },
                    ],
                },
            ],
            f"/runs/{call_19_recovery.FAILED_RUN_ID}/web-healing-proposals": {
                "total": 2,
                "items": [
                    {
                        "id": call_19_recovery.REJECTED_PROPOSAL_ID,
                        "status": "REJECTED",
                        "ai_call_id": 17,
                        "created_element_version_id": None,
                        "created_web_case_version_id": None,
                    },
                    {
                        "id": call_19_recovery.ACCEPTED_PROPOSAL_ID,
                        "status": "ACCEPTED",
                        "ai_call_id": 18,
                        "created_element_version_id": (
                            call_19_recovery.HEALED_ELEMENT_VERSION_ID
                        ),
                        "created_web_case_version_id": (
                            call_19_recovery.HEALED_CASE_VERSION_ID
                        ),
                    },
                ],
            },
        }

    def _analysis_calls(self) -> dict[str, Any]:
        call_19 = {
            "id": call_19_recovery.CALL_19_ID,
            "project_id": call_19_recovery.PROJECT_ID,
            "task_type": "WEB_FAILURE_ANALYSIS",
            "entity_type": "WEB_FAILURE_ANALYSIS",
            "entity_id": call_19_recovery.CALL_19_ENTITY_ID,
            "model_config_id": call_19_recovery.ANALYSIS_MODEL_ID,
            "actual_model": call_19_recovery.ANALYSIS_MODEL_NAME,
            "prompt_version_id": call_19_recovery.ANALYSIS_PROMPT_VERSION_ID,
            "output_schema_id": call_19_recovery.ANALYSIS_OUTPUT_SCHEMA_ID,
            "success": True,
            "fallback_used": False,
            "retry_count": 0,
            "repair_used": False,
            "error_type": None,
            "validation_errors": [],
            "parsed_result": {
                "failure_category": "LOCATOR_NOT_FOUND",
                "evidence_node_ids": ["://", "://"],
            },
        }
        items = [{"id": 14}, call_19]
        if self.post_count:
            items.append(
                {
                    "id": self.post_response["ai_call_id"],
                    "success": True,
                    "entity_type": "WEB_FAILURE_ANALYSIS",
                    "entity_id": call_19_recovery.CALL_19_ENTITY_ID,
                    "model_config_id": call_19_recovery.ANALYSIS_MODEL_ID,
                    "actual_model": call_19_recovery.ANALYSIS_MODEL_NAME,
                    "fallback_used": False,
                    "retry_count": 0,
                    "repair_used": self.post_response["repair_used"],
                    "prompt_version_id": (
                        call_19_recovery.ANALYSIS_PROMPT_VERSION_ID
                    ),
                    "output_schema_id": (
                        call_19_recovery.ANALYSIS_OUTPUT_SCHEMA_ID
                    ),
                    "validation_errors": [],
                    "parsed_result": deepcopy(
                        self.post_response["structured_result"]
                    ),
                }
            )
        return {"items": items, "total": len(items)}

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path))
        if method == "POST":
            assert path == (
                f"/runs/{call_19_recovery.FAILED_RUN_ID}/web-failure-analyses"
            )
            self.post_count += 1
            return deepcopy(self.post_response)
        if path == "/ai/calls":
            return deepcopy(self._analysis_calls())
        if path == (
            f"/runs/{call_19_recovery.FAILED_RUN_ID}/web-failure-analyses"
        ):
            if self.analysis_history_override is not None:
                return deepcopy(self.analysis_history_override)
            if self.post_count:
                return {"items": [deepcopy(self.post_response)], "total": 1}
            return {"items": [], "total": 0}
        if path == "/evidence":
            run_id = kwargs["params"]["run_id"]
            label = {
                call_19_recovery.BASELINE_RUN_ID: "baseline",
                call_19_recovery.FAILED_RUN_ID: "failed_old_locator",
                call_19_recovery.HEALED_RUN_ID: "healed",
            }[run_id]
            return {
                "items": [
                    {
                        "id": artifact_id,
                        "run_id": run_id,
                        "project_id": call_19_recovery.PROJECT_ID,
                    }
                    for artifact_id in self.manifest["ids"]["evidence_ids"][label]
                ],
                "total": len(self.manifest["ids"]["evidence_ids"][label]),
            }
        if path not in self.responses:
            raise AssertionError(f"unexpected stub request: {method} {path}")
        return deepcopy(self.responses[path])


def _call_19_recovery_fixture(
    tmp_path: Path,
    *,
    manifest: dict[str, Any] | None = None,
    ledger: dict[str, Any] | None = None,
) -> tuple[Any, Call19StubApi, tuple[Path, Path, Path, Path, Path]]:
    manifest = manifest or _call_19_manifest()
    ledger = ledger or _call_19_ledger()
    attempt_dir = tmp_path / "attempts"
    attempt_dir.mkdir()
    result_file = tmp_path / "result.json"
    attempt_file = attempt_dir / f"{call_19_recovery.ACCEPTANCE_ID}.json"
    ledger_file = tmp_path / "ai-call-ledger.json"
    checkpoint_dir = tmp_path / "checkpoints"
    receipt_file = tmp_path / "recovery.json"
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode()
    ledger_bytes = (json.dumps(ledger, ensure_ascii=False, indent=2) + "\n").encode()
    result_file.write_bytes(manifest_bytes)
    attempt_file.write_bytes(manifest_bytes)
    ledger_file.write_bytes(ledger_bytes)
    api = Call19StubApi(manifest)
    recovery = call_19_recovery.FailureAnalysisRecovery(
        api=api,
        result_file=result_file,
        attempt_file=attempt_file,
        ledger_file=ledger_file,
        checkpoint_dir=checkpoint_dir,
        receipt_file=receipt_file,
        expected_result_sha256=call_19_recovery._sha256(manifest_bytes),
        expected_ledger_sha256=call_19_recovery._sha256(ledger_bytes),
    )
    return recovery, api, (
        result_file,
        attempt_file,
        ledger_file,
        checkpoint_dir,
        receipt_file,
    )


def test_call_19_recovery_preflight_is_get_only_and_preserves_checkpoint(
    tmp_path: Path,
) -> None:
    recovery, api, files = _call_19_recovery_fixture(tmp_path)
    before = {path: path.read_bytes() for path in files[:3]}

    recovery.preflight()

    assert api.calls
    assert all(method == "GET" for method, _path in api.calls)
    assert {path: path.read_bytes() for path in files[:3]} == before
    assert not files[3].exists()
    assert not files[4].exists()


@pytest.mark.parametrize(
    ("drift", "expected_code"),
    [
        ("manifest_bytes", "CALL_19_MANIFEST_BYTES_DIVERGED"),
        ("ledger_content", "CALL_19_LEDGER_CONTENT_DRIFT"),
        ("call_result", "CALL_19_KNOWN_INVALID_RESULT_DRIFT"),
        ("analysis_exists", "CALL_19_ANALYSIS_ALREADY_MATERIALIZED"),
        ("healed_run", "CALL_19_HEALED_RUN_DRIFT"),
        ("proposal", "CALL_19_ACCEPTED_PROPOSAL_DRIFT"),
    ],
)
def test_call_19_recovery_preflight_rejects_any_checkpoint_or_remote_drift(
    tmp_path: Path, drift: str, expected_code: str
) -> None:
    recovery, api, files = _call_19_recovery_fixture(tmp_path)
    if drift == "manifest_bytes":
        files[1].write_text("{}", encoding="utf-8")
    elif drift == "ledger_content":
        ledger = _call_19_ledger()
        ledger["calls"][0]["stage"] = "changed"
        ledger_bytes = (json.dumps(ledger, indent=2) + "\n").encode()
        files[2].write_bytes(ledger_bytes)
        recovery.expected_ledger_sha256 = call_19_recovery._sha256(ledger_bytes)
    elif drift == "call_result":
        original = api._analysis_calls

        def changed_calls() -> dict[str, Any]:
            response = original()
            response["items"][1]["parsed_result"]["evidence_node_ids"] = [
                "action_1"
            ]
            return response

        api._analysis_calls = changed_calls
    elif drift == "analysis_exists":
        api.analysis_history_override = {"items": [{"id": 9}], "total": 1}
    elif drift == "healed_run":
        api.responses[f"/runs/{call_19_recovery.HEALED_RUN_ID}"]["status"] = "FAILED"
    elif drift == "proposal":
        proposals = api.responses[
            f"/runs/{call_19_recovery.FAILED_RUN_ID}/web-healing-proposals"
        ]["items"]
        proposals[1]["status"] = "REJECTED"

    with pytest.raises(p5e.AcceptanceFailure) as exc_info:
        recovery.preflight()

    assert exc_info.value.code == expected_code
    assert api.post_count == 0
    assert not files[4].exists()


def test_call_19_recovery_success_counts_once_preserves_history_and_blocks_repeat(
    tmp_path: Path,
) -> None:
    recovery, api, files = _call_19_recovery_fixture(tmp_path)
    original_result = files[0].read_bytes()
    original_ledger = files[2].read_bytes()

    recovery.run()

    assert api.post_count == 1
    assert [method for method, _path in api.calls].count("POST") == 1
    result = json.loads(files[0].read_text(encoding="utf-8"))
    attempt = json.loads(files[1].read_text(encoding="utf-8"))
    ledger = json.loads(files[2].read_text(encoding="utf-8"))
    receipt = json.loads(files[4].read_text(encoding="utf-8"))
    assert result == attempt
    assert result["status"] == "PASSED"
    assert result["stage"] == "complete"
    assert result["ids"]["failure_analysis_id"] == 1
    assert result["ids"]["failure_analysis_ai_call_id"] == 20
    assert result["counts"]["ai_business_calls_cumulative"] == 5
    assert ledger["total_attempted"] == 5
    assert ledger["calls"][:4] == call_19_recovery.EXPECTED_LEDGER_CALLS
    assert ledger["calls"][4] == {
        "acceptance_id": call_19_recovery.ACCEPTANCE_ID,
        "attempt_ordinal": 5,
        "stage": "generate_failure_analysis",
        "status": "ATTEMPTED",
    }
    assert receipt["state"] == "PASSED"
    assert (
        files[3] / f"{call_19_recovery.ACCEPTANCE_ID}-after-call-19.json"
    ).read_bytes() == original_result
    assert (
        files[3]
        / f"{call_19_recovery.ACCEPTANCE_ID}-ledger-before-call-20.json"
    ).read_bytes() == original_ledger

    repeated = call_19_recovery.FailureAnalysisRecovery(
        api=api,
        result_file=files[0],
        attempt_file=files[1],
        ledger_file=files[2],
        checkpoint_dir=files[3],
        receipt_file=files[4],
        expected_result_sha256=recovery.expected_result_sha256,
        expected_ledger_sha256=recovery.expected_ledger_sha256,
    )
    with pytest.raises(p5e.AcceptanceFailure) as exc_info:
        repeated.run()
    assert exc_info.value.code == "CALL_19_RECOVERY_ALREADY_ATTEMPTED"
    assert api.post_count == 1


def test_call_19_recovery_unknown_post_result_stops_and_cannot_retry(
    tmp_path: Path,
) -> None:
    recovery, api, files = _call_19_recovery_fixture(tmp_path)
    api.post_response["structured_result"]["evidence_node_ids"] = ["missing_node"]

    with pytest.raises(p5e.AcceptanceFailure) as exc_info:
        recovery.run()

    assert exc_info.value.code == "FAILURE_ANALYSIS_RESULT_INVALID"
    assert api.post_count == 1
    result = json.loads(files[0].read_text(encoding="utf-8"))
    ledger = json.loads(files[2].read_text(encoding="utf-8"))
    receipt = json.loads(files[4].read_text(encoding="utf-8"))
    assert result["status"] == "FAILED"
    assert result["stage"] == "generate_failure_analysis"
    assert result["statuses"]["error_code"] == "FAILURE_ANALYSIS_RESULT_INVALID"
    assert ledger["total_attempted"] == 5
    assert receipt["state"] == "FAILED"

    with pytest.raises(p5e.AcceptanceFailure) as repeat_exc:
        recovery.run()
    assert repeat_exc.value.code == "CALL_19_RECOVERY_ALREADY_ATTEMPTED"
    assert api.post_count == 1
