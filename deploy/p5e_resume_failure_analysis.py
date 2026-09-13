"""Resume only the bounded P5-E failure analysis after the audited Call 19 failure.

The entry point is intentionally tied to one immutable synthetic acceptance checkpoint.
It performs only authenticated GET preflight requests until it has preserved the failed
checkpoint and durably counted the single authorized FailureAnalysis POST. It never
replays Healing, approval, Run execution, evidence creation, or the old queue message.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from p5e_live_acceptance import (
    API_PREFIX,
    AcceptanceFailure,
    JsonClient,
    _load_json,
    _local_url,
    _public_keys_are_safe,
    require,
)

WORKSPACE = Path(__file__).resolve().parents[1]
BACKEND_DIR = WORKSPACE / "backend"
DEFAULT_READY_FILE = WORKSPACE / ".codex-validation" / "p5e-services" / "ready.json"
DEFAULT_RESULT_FILE = WORKSPACE / ".codex-validation" / "p5e-live" / "result.json"
DEFAULT_ATTEMPT_FILE = (
    WORKSPACE
    / ".codex-validation"
    / "p5e-live"
    / "attempts"
    / "V1P5E_20260909_F294AC1E.json"
)
DEFAULT_LEDGER_FILE = (
    WORKSPACE / ".codex-validation" / "p5e-live" / "ai-call-ledger.json"
)
DEFAULT_CHECKPOINT_DIR = (
    WORKSPACE / ".codex-validation" / "p5e-live" / "checkpoints"
)
DEFAULT_RECEIPT_FILE = (
    WORKSPACE
    / ".codex-validation"
    / "p5e-live"
    / "call-19-failure-analysis-recovery.json"
)

ACCEPTANCE_ID = "V1P5E_20260909_F294AC1E"
PROJECT_ID = 23
CASE_ID = 6
SOURCE_CASE_VERSION_ID = 6
HEALED_CASE_VERSION_ID = 7
ELEMENT_ID = 2
SOURCE_ELEMENT_VERSION_ID = 2
HEALED_ELEMENT_VERSION_ID = 3
BASELINE_RUN_ID = "run_786380f61a9647cd8976d376f90bc6e8"
FAILED_RUN_ID = "run_15c59b0b27964dd8a6fec2fcca840f9c"
HEALED_RUN_ID = "run_7852432fb7b241dabcad111664b9621b"
FAILED_CASE_RUN_ID = 28
HEALED_CASE_RUN_ID = 29
REJECTED_PROPOSAL_ID = 2
ACCEPTED_PROPOSAL_ID = 3
CALL_19_ID = 19
CALL_19_ENTITY_ID = "a7a7815b0548190b4768febb53c5e2139c35e158cb83e465037b5930f177f486"
ANALYSIS_BINDING_ID = 14
ANALYSIS_MODEL_ID = 18
ANALYSIS_MODEL_NAME = "qwen3.7-plus"
ANALYSIS_PROMPT_ID = 14
ANALYSIS_PROMPT_VERSION_ID = 16
ANALYSIS_OUTPUT_SCHEMA_ID = 12
EXPECTED_RESULT_SHA256 = "aa3a4a9f396d2df7e2beb6cae100da91de22160b6aecb251df2d4ce96ed999f8"
EXPECTED_LEDGER_SHA256 = "b6e20d30a358293a58435f856230345de91e0edda2eccfe44dfd40ea6675ef65"
AI_CALL_LIMIT = 6
EXPECTED_LEDGER_CALLS = [
    {
        "acceptance_id": ACCEPTANCE_ID,
        "attempt_ordinal": 1,
        "stage": "generate_and_reject_healing",
        "status": "ATTEMPTED",
    },
    {
        "acceptance_id": ACCEPTANCE_ID,
        "attempt_ordinal": 2,
        "stage": "generate_and_reject_healing",
        "status": "ATTEMPTED",
    },
    {
        "acceptance_id": ACCEPTANCE_ID,
        "attempt_ordinal": 3,
        "stage": "generate_and_accept_healing",
        "status": "ATTEMPTED",
    },
    {
        "acceptance_id": ACCEPTANCE_ID,
        "attempt_ordinal": 4,
        "stage": "generate_failure_analysis",
        "status": "ATTEMPTED",
    },
]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    require(_public_keys_are_safe(payload), "RECOVERY_OUTPUT_UNSAFE")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


class FailureAnalysisRecovery:
    def __init__(
        self,
        *,
        api: JsonClient,
        result_file: Path = DEFAULT_RESULT_FILE,
        attempt_file: Path = DEFAULT_ATTEMPT_FILE,
        ledger_file: Path = DEFAULT_LEDGER_FILE,
        checkpoint_dir: Path = DEFAULT_CHECKPOINT_DIR,
        receipt_file: Path = DEFAULT_RECEIPT_FILE,
        ai_timeout: float = 150.0,
        expected_result_sha256: str = EXPECTED_RESULT_SHA256,
        expected_ledger_sha256: str = EXPECTED_LEDGER_SHA256,
    ) -> None:
        self.api = api
        self.result_file = result_file
        self.attempt_file = attempt_file
        self.ledger_file = ledger_file
        self.checkpoint_dir = checkpoint_dir
        self.receipt_file = receipt_file
        self.ai_timeout = ai_timeout
        self.expected_result_sha256 = expected_result_sha256
        self.expected_ledger_sha256 = expected_ledger_sha256
        self.manifest: dict[str, Any] | None = None
        self.ledger: dict[str, Any] | None = None

    def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        response_type: type = dict,
    ) -> Any:
        return self.api.request(
            "GET", path, params=params, response_type=response_type
        )

    def _validate_local_checkpoint(self) -> tuple[dict[str, Any], dict[str, Any]]:
        require(not self.receipt_file.exists(), "CALL_19_RECOVERY_ALREADY_ATTEMPTED")
        require(self.result_file.is_file(), "CALL_19_RESULT_MISSING")
        require(self.attempt_file.is_file(), "CALL_19_ATTEMPT_MISSING")
        require(self.ledger_file.is_file(), "CALL_19_LEDGER_MISSING")
        result_bytes = self.result_file.read_bytes()
        attempt_bytes = self.attempt_file.read_bytes()
        ledger_bytes = self.ledger_file.read_bytes()
        require(result_bytes == attempt_bytes, "CALL_19_MANIFEST_BYTES_DIVERGED")
        require(
            _sha256(result_bytes) == self.expected_result_sha256,
            "CALL_19_RESULT_HASH_DRIFT",
        )
        require(
            _sha256(ledger_bytes) == self.expected_ledger_sha256,
            "CALL_19_LEDGER_HASH_DRIFT",
        )
        manifest = _load_json(
            self.result_file, invalid_code="CALL_19_RESULT_INVALID"
        )
        ledger = _load_json(
            self.ledger_file, invalid_code="CALL_19_LEDGER_INVALID"
        )
        require(_public_keys_are_safe(manifest), "CALL_19_MANIFEST_UNSAFE")
        require(manifest.get("schema_version") == 1, "CALL_19_SCHEMA_MISMATCH")
        require(manifest.get("acceptance_id") == ACCEPTANCE_ID, "CALL_19_ATTEMPT_DRIFT")
        require(
            manifest.get("status") == "FAILED"
            and manifest.get("stage") == "generate_failure_analysis",
            "CALL_19_CHECKPOINT_STATE_DRIFT",
        )
        ids = manifest.get("ids")
        statuses = manifest.get("statuses")
        counts = manifest.get("counts")
        require(isinstance(ids, dict), "CALL_19_IDS_INVALID")
        require(isinstance(statuses, dict), "CALL_19_STATUSES_INVALID")
        require(isinstance(counts, dict), "CALL_19_COUNTS_INVALID")
        require(
            ids.get("project_id") == PROJECT_ID
            and ids.get("web_case_id") == CASE_ID
            and ids.get("source_web_case_version_id") == SOURCE_CASE_VERSION_ID
            and ids.get("healed_web_case_version_id") == HEALED_CASE_VERSION_ID
            and ids.get("web_element_id") == ELEMENT_ID
            and ids.get("source_element_version_id") == SOURCE_ELEMENT_VERSION_ID
            and ids.get("healed_element_version_id") == HEALED_ELEMENT_VERSION_ID
            and ids.get("failed_case_run_id") == FAILED_CASE_RUN_ID,
            "CALL_19_ASSET_ID_DRIFT",
        )
        require(
            ids.get("run_ids")
            == {
                "baseline": BASELINE_RUN_ID,
                "failed_old_locator": FAILED_RUN_ID,
                "healed": HEALED_RUN_ID,
            },
            "CALL_19_RUN_ID_DRIFT",
        )
        require(
            ids.get("healing_proposal_ids")
            == {"rejected": REJECTED_PROPOSAL_ID, "accepted": ACCEPTED_PROPOSAL_ID}
            and ids.get("healing_ai_call_ids") == [17, 18],
            "CALL_19_HEALING_ID_DRIFT",
        )
        require(
            ids.get("prompt_ids", {}).get("web_failure_analysis")
            == ANALYSIS_PROMPT_ID
            and ids.get("prompt_version_ids", {}).get("web_failure_analysis")
            == ANALYSIS_PROMPT_VERSION_ID
            and ids.get("output_schema_ids", {}).get("web_failure_analysis")
            == ANALYSIS_OUTPUT_SCHEMA_ID,
            "CALL_19_PROMPT_ID_DRIFT",
        )
        require(
            statuses.get("baseline_run") == "SUCCESS"
            and statuses.get("failed_old_locator_run") == "FAILED"
            and statuses.get("rejected_healing_proposal") == "REJECTED"
            and statuses.get("accepted_healing_proposal") == "ACCEPTED"
            and statuses.get("healed_web_case") == "APPROVED"
            and statuses.get("healed_run") == "SUCCESS",
            "CALL_19_TERMINAL_STATUS_DRIFT",
        )
        require(
            statuses.get("error_code")
            == f"HTTP_409_POST_/runs/{FAILED_RUN_ID}/web-failure-analyses",
            "CALL_19_ERROR_CODE_DRIFT",
        )
        require(
            counts.get("ai_business_calls") == 4
            and counts.get("ai_business_calls_cumulative") == 4,
            "CALL_19_MANIFEST_CALL_COUNT_DRIFT",
        )
        require(
            ledger.get("schema_version") == 1
            and ledger.get("total_attempted") == 4
            and ledger.get("calls") == EXPECTED_LEDGER_CALLS,
            "CALL_19_LEDGER_CONTENT_DRIFT",
        )
        return manifest, ledger

    def _validate_runtime_configuration(self) -> None:
        projects = self._request("/projects").get("items")
        require(
            isinstance(projects, list)
            and any(
                item.get("id") == PROJECT_ID
                and item.get("status") in (None, "ACTIVE")
                for item in projects
                if isinstance(item, dict)
            ),
            "CALL_19_PROJECT_DRIFT",
        )
        bindings = self._request(
            "/model-center/bindings/project", params={"project_id": PROJECT_ID}
        ).get("items")
        binding = next(
            (
                item
                for item in bindings or []
                if isinstance(item, dict)
                and item.get("task_type") == "WEB_FAILURE_ANALYSIS"
            ),
            None,
        )
        require(
            isinstance(binding, dict)
            and binding.get("id") == ANALYSIS_BINDING_ID
            and binding.get("primary_model_id") == ANALYSIS_MODEL_ID
            and binding.get("fallback_model_id") is None
            and binding.get("max_fallback") == 0,
            "CALL_19_MODEL_BINDING_DRIFT",
        )
        models = self._request("/model-center").get("items")
        model = next(
            (
                item
                for item in models or []
                if isinstance(item, dict) and item.get("id") == ANALYSIS_MODEL_ID
            ),
            None,
        )
        require(
            isinstance(model, dict)
            and model.get("model_name") == ANALYSIS_MODEL_NAME
            and model.get("enabled") is True
            and model.get("supports_structured_output") is True,
            "CALL_19_MODEL_DRIFT",
        )
        prompts = self._request(
            "/prompt-center",
            params={"task_type": "WEB_FAILURE_ANALYSIS", "include_disabled": True},
        ).get("items")
        prompt = next(
            (
                item
                for item in prompts or []
                if isinstance(item, dict) and item.get("id") == ANALYSIS_PROMPT_ID
            ),
            None,
        )
        current_version = prompt.get("current_version") if isinstance(prompt, dict) else None
        require(
            isinstance(prompt, dict)
            and prompt.get("code") == "P5_WEB_FAILURE_ANALYSIS_V1"
            and prompt.get("enabled") is True
            and prompt.get("current_version_id") == ANALYSIS_PROMPT_VERSION_ID
            and isinstance(current_version, dict)
            and current_version.get("output_schema_id") == ANALYSIS_OUTPUT_SCHEMA_ID,
            "CALL_19_PROMPT_DRIFT",
        )
        schemas = self._request(
            "/ai/output-schemas", params={"include_disabled": True}
        ).get("items")
        schema = next(
            (
                item
                for item in schemas or []
                if isinstance(item, dict)
                and item.get("id") == ANALYSIS_OUTPUT_SCHEMA_ID
            ),
            None,
        )
        require(
            isinstance(schema, dict)
            and schema.get("name") == "P5_WEB_FAILURE_ANALYSIS_V1"
            and schema.get("version_no") == 1
            and schema.get("enabled") is True,
            "CALL_19_OUTPUT_SCHEMA_DRIFT",
        )

    def _validate_runs_and_assets(self, manifest: dict[str, Any]) -> None:
        baseline = self._request(f"/runs/{BASELINE_RUN_ID}")
        require(
            baseline.get("id") == BASELINE_RUN_ID
            and baseline.get("status") == "SUCCESS"
            and baseline.get("web_case_id") == CASE_ID
            and baseline.get("web_case_version_id") == SOURCE_CASE_VERSION_ID,
            "CALL_19_BASELINE_RUN_DRIFT",
        )
        failed = self._request(f"/runs/{FAILED_RUN_ID}")
        require(
            failed.get("id") == FAILED_RUN_ID
            and failed.get("project_id") == PROJECT_ID
            and failed.get("status") == "FAILED"
            and failed.get("web_case_id") == CASE_ID
            and failed.get("web_case_version_id") == SOURCE_CASE_VERSION_ID,
            "CALL_19_FAILED_RUN_DRIFT",
        )
        case_runs = failed.get("case_runs")
        traces = failed.get("web_traces")
        require(
            isinstance(case_runs, list)
            and len(case_runs) == 1
            and case_runs[0].get("id") == FAILED_CASE_RUN_ID
            and case_runs[0].get("status") == "FAILED"
            and case_runs[0].get("web_case_version_id") == SOURCE_CASE_VERSION_ID,
            "CALL_19_FAILED_CASE_RUN_DRIFT",
        )
        require(
            isinstance(traces, list)
            and len(traces) == 1
            and traces[0].get("node_id") == "action_1"
            and traces[0].get("status") == "FAILED"
            and traces[0].get("error_type") == "WEB_LOCATOR_NOT_FOUND",
            "CALL_19_FAILURE_NODE_DRIFT",
        )
        healed = self._request(f"/runs/{HEALED_RUN_ID}")
        healed_case_runs = healed.get("case_runs")
        require(
            healed.get("id") == HEALED_RUN_ID
            and healed.get("project_id") == PROJECT_ID
            and healed.get("status") == "SUCCESS"
            and healed.get("web_case_id") == CASE_ID
            and healed.get("web_case_version_id") == HEALED_CASE_VERSION_ID,
            "CALL_19_HEALED_RUN_DRIFT",
        )
        require(
            isinstance(healed_case_runs, list)
            and len(healed_case_runs) == 1
            and healed_case_runs[0].get("id") == HEALED_CASE_RUN_ID
            and healed_case_runs[0].get("status") == "SUCCESS",
            "CALL_19_HEALED_CASE_RUN_DRIFT",
        )
        case = self._request(f"/web-cases/{CASE_ID}")
        require(
            case.get("id") == CASE_ID
            and case.get("project_id") == PROJECT_ID
            and case.get("status") == "APPROVED"
            and case.get("current_version_id") == HEALED_CASE_VERSION_ID,
            "CALL_19_CASE_DRIFT",
        )
        case_versions = self._request(
            f"/web-cases/{CASE_ID}/versions", response_type=list
        )
        require(
            {item.get("id") for item in case_versions if isinstance(item, dict)}
            == {SOURCE_CASE_VERSION_ID, HEALED_CASE_VERSION_ID},
            "CALL_19_CASE_VERSION_SET_DRIFT",
        )
        expected_case_versions = {
            SOURCE_CASE_VERSION_ID: (1, SOURCE_ELEMENT_VERSION_ID),
            HEALED_CASE_VERSION_ID: (2, HEALED_ELEMENT_VERSION_ID),
        }
        for version_id, (version_no, element_version_id) in expected_case_versions.items():
            version = next(item for item in case_versions if item.get("id") == version_id)
            content = version.get("content")
            actions = content.get("actions") if isinstance(content, dict) else None
            require(
                version.get("web_case_id") == CASE_ID
                and version.get("version_no") == version_no
                and version.get("status") == "APPROVED"
                and isinstance(actions, list)
                and len(actions) == 1
                and isinstance(actions[0], dict)
                and actions[0].get("type") == "CLICK"
                and isinstance(actions[0].get("locator"), dict)
                and actions[0]["locator"].get("element_version_id")
                == element_version_id,
                "CALL_19_CASE_VERSION_DRIFT",
            )
        element_versions = self._request(
            f"/web-elements/{ELEMENT_ID}/versions", response_type=list
        )
        require(
            {item.get("id") for item in element_versions if isinstance(item, dict)}
            == {SOURCE_ELEMENT_VERSION_ID, HEALED_ELEMENT_VERSION_ID},
            "CALL_19_ELEMENT_VERSION_SET_DRIFT",
        )
        require(
            all(
                item.get("element_id") == ELEMENT_ID
                for item in element_versions
                if isinstance(item, dict)
            ),
            "CALL_19_ELEMENT_VERSION_DRIFT",
        )
        expected_locators = {
            SOURCE_ELEMENT_VERSION_ID: [
                {
                    "strategy": "css",
                    "value": "#login-btn",
                    "priority": 1,
                    "source": "MANUAL",
                }
            ],
            HEALED_ELEMENT_VERSION_ID: [
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
        }
        for version_id, expected in expected_locators.items():
            version = next(
                item for item in element_versions if item.get("id") == version_id
            )
            locators = version.get("locators")
            actual = [
                {
                    key: locator.get(key)
                    for key in ("strategy", "value", "priority", "source")
                }
                for locator in locators or []
                if isinstance(locator, dict)
            ]
            require(actual == expected, "CALL_19_ELEMENT_LOCATOR_DRIFT")
        proposals = self._request(
            f"/runs/{FAILED_RUN_ID}/web-healing-proposals",
            params={"case_run_id": FAILED_CASE_RUN_ID},
        )
        proposal_items = proposals.get("items")
        require(
            proposals.get("total") == 2
            and isinstance(proposal_items, list)
            and {item.get("id") for item in proposal_items} == {
                REJECTED_PROPOSAL_ID,
                ACCEPTED_PROPOSAL_ID,
            },
            "CALL_19_PROPOSAL_SET_DRIFT",
        )
        rejected = next(
            item for item in proposal_items if item.get("id") == REJECTED_PROPOSAL_ID
        )
        accepted = next(
            item for item in proposal_items if item.get("id") == ACCEPTED_PROPOSAL_ID
        )
        require(
            rejected.get("status") == "REJECTED"
            and rejected.get("ai_call_id") == 17
            and rejected.get("created_element_version_id") is None
            and rejected.get("created_web_case_version_id") is None,
            "CALL_19_REJECTED_PROPOSAL_DRIFT",
        )
        require(
            accepted.get("status") == "ACCEPTED"
            and accepted.get("ai_call_id") == 18
            and accepted.get("created_element_version_id") == HEALED_ELEMENT_VERSION_ID
            and accepted.get("created_web_case_version_id") == HEALED_CASE_VERSION_ID,
            "CALL_19_ACCEPTED_PROPOSAL_DRIFT",
        )
        evidence_ids = manifest.get("ids", {}).get("evidence_ids", {})
        for label, run_id in (
            ("baseline", BASELINE_RUN_ID),
            ("failed_old_locator", FAILED_RUN_ID),
            ("healed", HEALED_RUN_ID),
        ):
            response = self._request(
                "/evidence",
                params={"project_id": PROJECT_ID, "run_id": run_id, "page_size": 100},
            )
            items = response.get("items")
            expected = evidence_ids.get(label)
            require(
                isinstance(items, list)
                and isinstance(expected, list)
                and len(items) == len(expected)
                and {item.get("id") for item in items} == set(expected)
                and all(item.get("run_id") == run_id for item in items),
                f"CALL_19_{label.upper()}_EVIDENCE_DRIFT",
            )

    def _validate_call_19_and_empty_history(self) -> None:
        calls = self._request(
            "/ai/calls",
            params={"project_id": PROJECT_ID, "task_type": "WEB_FAILURE_ANALYSIS"},
        )
        items = calls.get("items")
        require(
            calls.get("total") == 2
            and isinstance(items, list)
            and {item.get("id") for item in items} == {14, CALL_19_ID},
            "CALL_19_ANALYSIS_CALL_SET_DRIFT",
        )
        call = next(item for item in items if item.get("id") == CALL_19_ID)
        require(
            call.get("project_id") == PROJECT_ID
            and call.get("task_type") == "WEB_FAILURE_ANALYSIS"
            and call.get("entity_type") == "WEB_FAILURE_ANALYSIS"
            and call.get("entity_id") == CALL_19_ENTITY_ID
            and call.get("model_config_id") == ANALYSIS_MODEL_ID
            and call.get("actual_model") == ANALYSIS_MODEL_NAME
            and call.get("prompt_version_id") == ANALYSIS_PROMPT_VERSION_ID
            and call.get("output_schema_id") == ANALYSIS_OUTPUT_SCHEMA_ID
            and call.get("success") is True
            and call.get("fallback_used") is False
            and call.get("retry_count") == 0
            and call.get("repair_used") is False
            and call.get("error_type") is None
            and call.get("validation_errors") == [],
            "CALL_19_METADATA_DRIFT",
        )
        parsed = call.get("parsed_result")
        require(
            isinstance(parsed, dict)
            and parsed.get("failure_category") == "LOCATOR_NOT_FOUND"
            and parsed.get("evidence_node_ids") == ["://", "://"],
            "CALL_19_KNOWN_INVALID_RESULT_DRIFT",
        )
        history = self._request(
            f"/runs/{FAILED_RUN_ID}/web-failure-analyses",
            params={"case_run_id": FAILED_CASE_RUN_ID},
        )
        require(
            history.get("total") == 0 and history.get("items") == [],
            "CALL_19_ANALYSIS_ALREADY_MATERIALIZED",
        )

    def preflight(self) -> None:
        manifest, ledger = self._validate_local_checkpoint()
        self._validate_runtime_configuration()
        self._validate_runs_and_assets(manifest)
        self._validate_call_19_and_empty_history()
        self.manifest = manifest
        self.ledger = ledger

    def _preserve_checkpoint_and_start_receipt(self) -> None:
        require(self.manifest is not None, "CALL_19_PREFLIGHT_REQUIRED")
        require(self.ledger is not None, "CALL_19_PREFLIGHT_REQUIRED")
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_result = self.checkpoint_dir / f"{ACCEPTANCE_ID}-after-call-19.json"
        checkpoint_ledger = self.checkpoint_dir / f"{ACCEPTANCE_ID}-ledger-before-call-20.json"
        for path, source in (
            (checkpoint_result, self.result_file),
            (checkpoint_ledger, self.ledger_file),
        ):
            content = source.read_bytes()
            if path.exists():
                require(path.read_bytes() == content, "CALL_19_CHECKPOINT_COPY_DRIFT")
            else:
                with path.open("xb") as handle:
                    handle.write(content)
        self.receipt_file.parent.mkdir(parents=True, exist_ok=True)
        receipt = {
            "schema_version": 1,
            "acceptance_id": ACCEPTANCE_ID,
            "state": "STARTED",
            "source_result_sha256": self.expected_result_sha256,
            "source_ledger_sha256": self.expected_ledger_sha256,
        }
        try:
            with self.receipt_file.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        except FileExistsError as exc:
            raise AcceptanceFailure("CALL_19_RECOVERY_ALREADY_ATTEMPTED") from exc

    def _count_single_post(self) -> None:
        require(self.manifest is not None, "CALL_19_PREFLIGHT_REQUIRED")
        require(self.ledger is not None, "CALL_19_PREFLIGHT_REQUIRED")
        require(
            self.ledger.get("total_attempted") == 4
            and self.ledger.get("calls") == EXPECTED_LEDGER_CALLS,
            "CALL_19_LEDGER_CONTENT_DRIFT",
        )
        require(self.ledger["total_attempted"] < AI_CALL_LIMIT, "AI_CALL_LIMIT_EXCEEDED")
        self.ledger["calls"].append(
            {
                "acceptance_id": ACCEPTANCE_ID,
                "attempt_ordinal": 5,
                "stage": "generate_failure_analysis",
                "status": "ATTEMPTED",
            }
        )
        self.ledger["total_attempted"] = 5
        _atomic_write_json(self.ledger_file, self.ledger)
        self.manifest["counts"]["ai_business_calls"] = 5
        self.manifest["counts"]["ai_business_calls_cumulative"] = 5

    def _validate_new_analysis(self, analysis: dict[str, Any]) -> None:
        require(_public_keys_are_safe(analysis), "FAILURE_ANALYSIS_RESPONSE_UNSAFE")
        require(
            analysis.get("status") == "COMPLETED"
            and analysis.get("project_id") == PROJECT_ID
            and analysis.get("run_id") == FAILED_RUN_ID
            and analysis.get("case_run_id") == FAILED_CASE_RUN_ID
            and analysis.get("prompt_version_id") == ANALYSIS_PROMPT_VERSION_ID
            and analysis.get("output_schema_id") == ANALYSIS_OUTPUT_SCHEMA_ID
            and analysis.get("actual_model") == ANALYSIS_MODEL_NAME
            and analysis.get("fallback_used") is False
            and isinstance(analysis.get("repair_used"), bool)
            and analysis.get("source_snapshot_sha256") == CALL_19_ENTITY_ID,
            "FAILURE_ANALYSIS_RESPONSE_DRIFT",
        )
        call_id = analysis.get("ai_call_id")
        require(
            isinstance(call_id, int) and call_id > CALL_19_ID,
            "FAILURE_ANALYSIS_CALL_ID_INVALID",
        )
        structured = analysis.get("structured_result")
        require(
            isinstance(structured, dict)
            and structured.get("failure_category")
            in {"LOCATOR_NOT_FOUND", "PAGE_CHANGED"}
            and structured.get("evidence_node_ids") == ["action_1"],
            "FAILURE_ANALYSIS_RESULT_INVALID",
        )
        history = self._request(
            f"/runs/{FAILED_RUN_ID}/web-failure-analyses",
            params={"case_run_id": FAILED_CASE_RUN_ID},
        )
        history_items = history.get("items")
        require(
            history.get("total") == 1
            and isinstance(history_items, list)
            and len(history_items) == 1
            and history_items[0].get("id") == analysis.get("id"),
            "FAILURE_ANALYSIS_HISTORY_INVALID",
        )
        calls = self._request(
            "/ai/calls",
            params={"project_id": PROJECT_ID, "task_type": "WEB_FAILURE_ANALYSIS"},
        )
        call_items = calls.get("items")
        require(
            calls.get("total") == 3 and isinstance(call_items, list),
            "FAILURE_ANALYSIS_CALL_HISTORY_INVALID",
        )
        call = next(
            (
                item
                for item in call_items
                if isinstance(item, dict) and item.get("id") == call_id
            ),
            None,
        )
        parsed_result = call.get("parsed_result") if isinstance(call, dict) else None
        require(
            isinstance(call, dict)
            and call.get("success") is True
            and call.get("entity_type") == "WEB_FAILURE_ANALYSIS"
            and call.get("entity_id") == CALL_19_ENTITY_ID
            and call.get("model_config_id") == ANALYSIS_MODEL_ID
            and call.get("actual_model") == ANALYSIS_MODEL_NAME
            and call.get("prompt_version_id") == ANALYSIS_PROMPT_VERSION_ID
            and call.get("output_schema_id") == ANALYSIS_OUTPUT_SCHEMA_ID
            and call.get("fallback_used") is False
            and call.get("retry_count") == 0
            and call.get("repair_used") == analysis.get("repair_used")
            and call.get("validation_errors") == []
            and isinstance(parsed_result, dict)
            and parsed_result.get("evidence_node_ids") == ["action_1"],
            "FAILURE_ANALYSIS_NEW_CALL_INVALID",
        )

    def _write_manifest(self) -> None:
        require(self.manifest is not None, "CALL_19_PREFLIGHT_REQUIRED")
        _atomic_write_json(self.attempt_file, self.manifest)
        _atomic_write_json(self.result_file, self.manifest)

    def _finish_receipt(self, state: str, *, error_code: str | None = None) -> None:
        receipt = _load_json(
            self.receipt_file, invalid_code="CALL_19_RECEIPT_INVALID"
        )
        receipt["state"] = state
        if error_code is not None:
            receipt["error_code"] = error_code
        _atomic_write_json(self.receipt_file, receipt)

    def _finish_failed(self, error_code: str) -> None:
        require(self.manifest is not None, "CALL_19_PREFLIGHT_REQUIRED")
        self.manifest["status"] = "FAILED"
        self.manifest["stage"] = "generate_failure_analysis"
        self.manifest["statuses"]["error_code"] = error_code
        self._write_manifest()
        self._finish_receipt("FAILED", error_code=error_code)

    def run(self) -> None:
        print("[verify_call_19_checkpoint] running", flush=True)
        self.preflight()
        self._preserve_checkpoint_and_start_receipt()
        self._count_single_post()
        try:
            print("[generate_failure_analysis] running", flush=True)
            analysis = self.api.request(
                "POST",
                f"/runs/{FAILED_RUN_ID}/web-failure-analyses",
                payload={
                    "case_run_id": FAILED_CASE_RUN_ID,
                    "prompt_id": ANALYSIS_PROMPT_ID,
                },
                timeout=self.ai_timeout,
            )
            require(isinstance(analysis, dict), "FAILURE_ANALYSIS_RESPONSE_INVALID")
            self._validate_new_analysis(analysis)
        except AcceptanceFailure as exc:
            self._finish_failed(exc.code)
            raise
        except Exception as exc:
            self._finish_failed("UNEXPECTED_ERROR")
            raise AcceptanceFailure("UNEXPECTED_ERROR") from exc
        require(self.manifest is not None, "CALL_19_PREFLIGHT_REQUIRED")
        self.manifest["ids"]["failure_analysis_id"] = analysis["id"]
        self.manifest["ids"]["failure_analysis_ai_call_id"] = analysis["ai_call_id"]
        self.manifest["statuses"].pop("error_code", None)
        self.manifest["statuses"]["failure_analysis"] = "COMPLETED"
        self.manifest["counts"].update(
            {
                "failure_analysis_history": 1,
                "synthetic_web_assets": 5,
                "synthetic_runs": 3,
                "healing_proposals": 2,
                "failure_analyses": 1,
            }
        )
        self.manifest["links"]["failure_analysis_api"] = (
            f"{API_PREFIX}/runs/{FAILED_RUN_ID}/web-failure-analyses"
        )
        self.manifest["status"] = "PASSED"
        self.manifest["stage"] = "complete"
        self._write_manifest()
        self._finish_receipt("PASSED")
        print(f"[complete] PASSED {ACCEPTANCE_ID}", flush=True)


def _connect_api(ready_file: Path, timeout: float) -> JsonClient:
    ready = _load_json(ready_file)
    require(ready.get("schema_version") == 1, "READINESS_SCHEMA_MISMATCH")
    services = ready.get("services")
    require(isinstance(services, list), "READINESS_SERVICES_MISSING")
    backend = next(
        (
            item
            for item in services
            if isinstance(item, dict) and item.get("name") == "backend"
        ),
        None,
    )
    require(
        isinstance(backend, dict) and backend.get("healthy") is True,
        "READINESS_BACKEND_UNHEALTHY",
    )
    backend_url = _local_url(backend.get("address"), port=8000, label="BACKEND")
    health = JsonClient(backend_url, timeout).request(
        "GET", "/health", authenticated=False
    )
    require(
        health.get("status") == "ok" and health.get("service") == "backend",
        "BACKEND_HEALTH_FAILED",
    )
    api = JsonClient(f"{backend_url}{API_PREFIX}", timeout)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from app.core.config import Settings

    settings = Settings(_env_file=str(BACKEND_DIR / ".env"))
    login = api.request(
        "POST",
        "/auth/login",
        payload={
            "username": settings.dev_admin_username,
            "password": settings.dev_admin_password,
        },
        authenticated=False,
    )
    token = login.get("access_token")
    require(isinstance(token, str), "LOGIN_TOKEN_MISSING")
    api.set_bearer(token)
    del login, token, settings
    return api


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-after-call-19", action="store_true")
    parser.add_argument("--ready-file", type=Path, default=DEFAULT_READY_FILE)
    parser.add_argument("--ordinary-timeout", type=float, default=20.0)
    parser.add_argument("--ai-timeout", type=float, default=150.0)
    args = parser.parse_args()
    require(args.resume_after_call_19, "CALL_19_RECOVERY_CONFIRMATION_REQUIRED")
    require(1 <= args.ordinary_timeout <= 60, "ORDINARY_TIMEOUT_OUT_OF_RANGE")
    require(30 <= args.ai_timeout <= 300, "AI_TIMEOUT_OUT_OF_RANGE")
    args.ready_file = args.ready_file.resolve()
    require(args.ready_file == DEFAULT_READY_FILE.resolve(), "READY_FILE_SCOPE_INVALID")
    return args


def main() -> int:
    try:
        args = parse_args()
        api = _connect_api(args.ready_file, args.ordinary_timeout)
        recovery = FailureAnalysisRecovery(api=api, ai_timeout=args.ai_timeout)
        recovery.run()
    except AcceptanceFailure as exc:
        print(f"[failure-analysis-recovery] FAILED {exc.code}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - never expose runtime or response details
        print("[failure-analysis-recovery] FAILED UNEXPECTED_ERROR", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
