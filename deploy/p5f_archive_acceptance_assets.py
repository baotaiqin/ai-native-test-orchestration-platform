"""Precisely archive the six reviewed P5-E synthetic Web assets.

The formal database connection in this process is guarded to SELECT/SHOW only.
All mutations go through the existing authenticated archive APIs. Every business
POST is journaled before dispatch and is never retried after an unknown result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import event, select
from sqlalchemy import inspect as sa_inspect

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE_ROOT / "backend"
DEPLOY_ROOT = WORKSPACE_ROOT / "deploy"
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(DEPLOY_ROOT))

from app.infrastructure.db.session import SessionLocal, engine
from app.modules.evidence.models import EvidenceArtifact
from app.modules.projects.models import Project
from app.modules.prompt_center.models import AiCallLog
from app.modules.runs.models import (
    CaseRun,
    RunWebExecutionResult,
    StepRun,
    TestRun,
)
from app.modules.web_cases.models import (
    WebCase,
    WebCaseVersion,
    WebElement,
    WebElementLocator,
    WebElementVersion,
    WebPage,
)
from app.modules.web_failure_analysis.models import WebFailureAnalysis
from app.modules.web_healing.models import WebHealingProposal
from p5e_resume_failure_analysis import DEFAULT_READY_FILE, _connect_api

PACKAGE = "P5F-C1/r1"
PROJECT_ID = 23
MARKER = "P5-E live acceptance synthetic asset"
VALIDATION_DIR = WORKSPACE_ROOT / ".codex-validation" / "p5f-archive"
PREFLIGHT_FILE = VALIDATION_DIR / "preflight.json"
JOURNAL_FILE = VALIDATION_DIR / "operations.json"
VERIFICATION_FILE = VALIDATION_DIR / "verification.json"
FAILURE_FILE = VALIDATION_DIR / "failure.json"
ARCHIVE_PLAN_FILE = WORKSPACE_ROOT / ".codex-validation" / "p5e-archive-plan.json"
RESULT_FILE = WORKSPACE_ROOT / ".codex-validation" / "p5e-live" / "result.json"
LEDGER_FILE = (
    WORKSPACE_ROOT / ".codex-validation" / "p5e-live" / "ai-call-ledger.json"
)
H4_REPORT_FILE = WORKSPACE_ROOT / "文档" / "P5E-H4前端完整验收记录.md"
H4_AUDIT_FILE = (
    WORKSPACE_ROOT / ".codex-validation" / "p5e-frontend-h4" / "ui-audit.json"
)
EXPECTED_RESULT_HASH = (
    "2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176"
)
EXPECTED_LEDGER_HASH = (
    "eb9cbf3fd6a43b5be90d74a658d01ae73126b6f3f82e6ca1444b21c1e19f8b43"
)
EXPECTED_H4_AUDIT_HASH = (
    "e87c7fce8a5eb604f8762f64285c025c4bc8d4aaf626c61963d756e45f26d938"
)
ASSETS = (
    {
        "attempt": "V1P5E_20260909_39E35D46",
        "manifest_sha256": (
            "6641011f5e02b7137191bd9ca5ab5a5049eb4099a702db4b1b8c9485da22e42b"
        ),
        "case_id": 5,
        "case_current_version_id": 5,
        "case_version_ids": (5,),
        "page_id": 1,
        "element_id": 1,
        "element_current_version_id": 1,
        "element_version_ids": (1,),
    },
    {
        "attempt": "V1P5E_20260909_F294AC1E",
        "manifest_sha256": EXPECTED_RESULT_HASH,
        "case_id": 6,
        "case_current_version_id": 7,
        "case_version_ids": (6, 7),
        "page_id": 2,
        "element_id": 2,
        "element_current_version_id": 3,
        "element_version_ids": (2, 3),
    },
)
EXPECTED_VERSION_REFERENCES = {"5": [1], "6": [2], "7": [3]}
ACTIVE_RUN_STATUSES = {"QUEUED", "ASSIGNED", "RUNNING", "CANCELLING"}


class ArchiveStop(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ArchiveStop(code)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    return str(value)


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _manifest_file(attempt: str) -> Path:
    return (
        WORKSPACE_ROOT
        / ".codex-validation"
        / "p5e-live"
        / "attempts"
        / f"{attempt}.json"
    )


def _guard_select_show_only(
    _connection: Any,
    _cursor: Any,
    statement: str,
    _parameters: Any,
    _context: Any,
    _executemany: bool,
) -> None:
    operation = statement.lstrip().split(None, 1)[0].upper()
    if operation not in {"SELECT", "SHOW"}:
        raise ArchiveStop("FORMAL_DB_NON_READ_ONLY_STATEMENT_BLOCKED")


event.listen(engine, "before_cursor_execute", _guard_select_show_only)


def _row_fingerprint(row: Any) -> dict[str, Any]:
    values = {
        attribute.key: getattr(row, attribute.key)
        for attribute in sa_inspect(row).mapper.column_attrs
    }
    identity = values.get("id")
    safe: dict[str, Any] = {"id": identity, "row_sha256": _json_hash(values)}
    if isinstance(values.get("status"), str):
        safe["status"] = values["status"]
    if isinstance(values.get("run_id"), str):
        safe["run_id"] = values["run_id"]
    return safe


def _rows_snapshot(rows: list[Any]) -> dict[str, Any]:
    items = sorted(
        (_row_fingerprint(row) for row in rows),
        key=lambda item: (str(item.get("id")), str(item.get("run_id"))),
    )
    return {"count": len(items), "items": items, "set_sha256": _json_hash(items)}


def _element_version_references(value: Any) -> list[int]:
    references: set[int] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key == "element_version_id" and isinstance(child, int):
                    references.add(child)
                else:
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return sorted(references)


def _protected_files() -> dict[str, Any]:
    first_manifest = _manifest_file(ASSETS[0]["attempt"])
    second_manifest = _manifest_file(ASSETS[1]["attempt"])
    files = {
        "archive_plan": ARCHIVE_PLAN_FILE,
        "first_attempt": first_manifest,
        "successful_attempt": second_manifest,
        "result": RESULT_FILE,
        "ledger": LEDGER_FILE,
        "h4_report": H4_REPORT_FILE,
        "h4_ui_audit": H4_AUDIT_FILE,
    }
    hashes = {name: _sha256(path) for name, path in files.items()}
    ledger = _read_json(LEDGER_FILE)
    calls = ledger.get("calls") if isinstance(ledger, dict) else None
    return {
        "sha256": hashes,
        "result_successful_attempt_bytes_equal": (
            RESULT_FILE.read_bytes() == second_manifest.read_bytes()
        ),
        "ledger_total_attempted": ledger.get("total_attempted"),
        "ledger_call_count": len(calls) if isinstance(calls, list) else None,
    }


def _validate_protected_files(snapshot: dict[str, Any]) -> None:
    hashes = snapshot["sha256"]
    require(
        hashes["first_attempt"] == ASSETS[0]["manifest_sha256"],
        "FIRST_ATTEMPT_HASH_DRIFT",
    )
    require(
        hashes["successful_attempt"] == EXPECTED_RESULT_HASH
        and hashes["result"] == EXPECTED_RESULT_HASH
        and snapshot["result_successful_attempt_bytes_equal"] is True,
        "SUCCESS_MANIFEST_HASH_DRIFT",
    )
    require(
        hashes["ledger"] == EXPECTED_LEDGER_HASH
        and snapshot["ledger_total_attempted"] == 5
        and snapshot["ledger_call_count"] == 5,
        "AI_LEDGER_DRIFT",
    )
    require(hashes["h4_ui_audit"] == EXPECTED_H4_AUDIT_HASH, "H4_AUDIT_DRIFT")
    report_text = H4_REPORT_FILE.read_text(encoding="utf-8-sig")
    require(
        "P5E-H4 / r1 **通过**" in report_text
        and EXPECTED_H4_AUDIT_HASH in report_text,
        "H4_REPORT_DRIFT",
    )


def _validate_archive_plan() -> None:
    plan = _read_json(ARCHIVE_PLAN_FILE)
    require(isinstance(plan, dict), "ARCHIVE_PLAN_INVALID")
    require(
        plan.get("status") == "PLANNED_NOT_EXECUTED"
        and plan.get("project_id") == PROJECT_ID
        and plan.get("writes_to_formal_state") == 0,
        "ARCHIVE_PLAN_STATE_DRIFT",
    )
    planned_assets = plan.get("assets")
    require(isinstance(planned_assets, list), "ARCHIVE_PLAN_ASSETS_INVALID")
    expected_plan = [
        {
            "attempt": asset["attempt"],
            "manifest_sha256": asset["manifest_sha256"],
            "case_id": asset["case_id"],
            "case_current_version_id": asset["case_current_version_id"],
            "page_id": asset["page_id"],
            "element_id": asset["element_id"],
            "element_version_ids": list(asset["element_version_ids"]),
        }
        for asset in ASSETS
    ]
    actual_plan = [
        {
            key: item.get(key)
            for key in (
                "attempt",
                "manifest_sha256",
                "case_id",
                "case_current_version_id",
                "page_id",
                "element_id",
                "element_version_ids",
            )
        }
        for item in planned_assets
        if isinstance(item, dict)
    ]
    require(actual_plan == expected_plan, "ARCHIVE_PLAN_IDENTITY_DRIFT")


def _database_snapshot(*, allow_archived: bool) -> dict[str, Any]:
    target_case_ids = {asset["case_id"] for asset in ASSETS}
    target_page_ids = {asset["page_id"] for asset in ASSETS}
    target_element_ids = {asset["element_id"] for asset in ASSETS}
    target_element_version_ids = {
        version_id for asset in ASSETS for version_id in asset["element_version_ids"]
    }
    with SessionLocal() as session:
        project = session.get(Project, PROJECT_ID)
        require(project is not None, "PROJECT_NOT_FOUND")
        cases = {
            case.id: case
            for case in session.scalars(
                select(WebCase).where(WebCase.id.in_(target_case_ids))
            ).all()
        }
        pages = {
            page.id: page
            for page in session.scalars(
                select(WebPage).where(WebPage.id.in_(target_page_ids))
            ).all()
        }
        elements = {
            element.id: element
            for element in session.scalars(
                select(WebElement).where(WebElement.id.in_(target_element_ids))
            ).all()
        }
        case_versions = list(
            session.scalars(
                select(WebCaseVersion)
                .where(WebCaseVersion.web_case_id.in_(target_case_ids))
                .order_by(WebCaseVersion.id)
            ).all()
        )
        element_versions = list(
            session.scalars(
                select(WebElementVersion)
                .where(WebElementVersion.element_id.in_(target_element_ids))
                .order_by(WebElementVersion.id)
            ).all()
        )
        locators = list(
            session.scalars(
                select(WebElementLocator)
                .where(WebElementLocator.element_version_id.in_(target_element_version_ids))
                .order_by(WebElementLocator.id)
            ).all()
        )
        all_case_versions = list(session.scalars(select(WebCaseVersion)).all())
        external_references = [
            {
                "web_case_version_id": version.id,
                "web_case_id": version.web_case_id,
                "element_version_ids": sorted(
                    set(_element_version_references(version.content))
                    & target_element_version_ids
                ),
            }
            for version in all_case_versions
            if version.web_case_id not in target_case_ids
            and set(_element_version_references(version.content))
            & target_element_version_ids
        ]
        extra_elements = list(
            session.scalars(
                select(WebElement).where(
                    WebElement.page_id.in_(target_page_ids),
                    WebElement.id.not_in(target_element_ids),
                )
            ).all()
        )
        runs = list(
            session.scalars(
                select(TestRun)
                .where(TestRun.web_case_id.in_(target_case_ids))
                .order_by(TestRun.id)
            ).all()
        )
        run_ids = [run.id for run in runs]
        case_runs = list(
            session.scalars(
                select(CaseRun).where(CaseRun.run_id.in_(run_ids)).order_by(CaseRun.id)
            ).all()
        )
        case_run_ids = [case_run.id for case_run in case_runs]
        step_runs = list(
            session.scalars(
                select(StepRun)
                .where(StepRun.case_run_id.in_(case_run_ids))
                .order_by(StepRun.id)
            ).all()
        )
        evidence = list(
            session.scalars(
                select(EvidenceArtifact)
                .where(EvidenceArtifact.run_id.in_(run_ids))
                .order_by(EvidenceArtifact.id)
            ).all()
        )
        web_results = list(
            session.scalars(
                select(RunWebExecutionResult)
                .where(RunWebExecutionResult.run_id.in_(run_ids))
                .order_by(RunWebExecutionResult.id)
            ).all()
        )
        ai_calls = list(
            session.scalars(
                select(AiCallLog)
                .where(AiCallLog.project_id == PROJECT_ID)
                .order_by(AiCallLog.id)
            ).all()
        )
        proposals = list(
            session.scalars(
                select(WebHealingProposal)
                .where(WebHealingProposal.project_id == PROJECT_ID)
                .order_by(WebHealingProposal.id)
            ).all()
        )
        analyses = list(
            session.scalars(
                select(WebFailureAnalysis)
                .where(WebFailureAnalysis.project_id == PROJECT_ID)
                .order_by(WebFailureAnalysis.id)
            ).all()
        )

        asset_snapshots: list[dict[str, Any]] = []
        for expected in ASSETS:
            case = cases.get(expected["case_id"])
            page = pages.get(expected["page_id"])
            element = elements.get(expected["element_id"])
            require(case is not None and page is not None and element is not None, "ASSET_MISSING")
            require(
                case.project_id == PROJECT_ID
                and case.code == f"WC-{case.id:05d}"
                and case.name == f"{expected['attempt']} Empty Login Click"
                and case.current_version_id == expected["case_current_version_id"],
                "CASE_IDENTITY_DRIFT",
            )
            require(
                page.project_id == PROJECT_ID
                and page.code == f"{expected['attempt']}_LOGIN"
                and page.name == f"{expected['attempt']} Login Page"
                and page.description == MARKER,
                "PAGE_IDENTITY_DRIFT",
            )
            require(
                element.project_id == PROJECT_ID
                and element.page_id == page.id
                and element.name == f"{expected['attempt']} Login Button"
                and element.description == MARKER
                and element.current_version_id == expected["element_current_version_id"],
                "ELEMENT_IDENTITY_DRIFT",
            )
            if allow_archived:
                require(
                    case.status in {"APPROVED", "ARCHIVED"}
                    and page.status in {"ACTIVE", "ARCHIVED"}
                    and element.status in {"ACTIVE", "ARCHIVED"},
                    "ASSET_STATUS_DRIFT",
                )
            else:
                require(
                    case.status == "APPROVED"
                    and page.status == "ACTIVE"
                    and element.status == "ACTIVE",
                    "ASSET_NOT_ACTIVE_BEFORE_ARCHIVE",
                )
            asset_snapshots.append(
                {
                    "attempt": expected["attempt"],
                    "case": {
                        "id": case.id,
                        "project_id": case.project_id,
                        "code": case.code,
                        "name": case.name,
                        "status": case.status,
                        "current_version_id": case.current_version_id,
                    },
                    "page": {
                        "id": page.id,
                        "project_id": page.project_id,
                        "code": page.code,
                        "name": page.name,
                        "description": page.description,
                        "status": page.status,
                    },
                    "element": {
                        "id": element.id,
                        "project_id": element.project_id,
                        "page_id": element.page_id,
                        "name": element.name,
                        "description": element.description,
                        "status": element.status,
                        "current_version_id": element.current_version_id,
                    },
                }
            )

        case_version_ids = [version.id for version in case_versions]
        element_version_ids = [version.id for version in element_versions]
        require(
            case_version_ids
            == sorted(version_id for asset in ASSETS for version_id in asset["case_version_ids"]),
            "CASE_VERSION_SET_DRIFT",
        )
        require(
            element_version_ids == sorted(target_element_version_ids),
            "ELEMENT_VERSION_SET_DRIFT",
        )
        actual_version_references = {
            str(version.id): _element_version_references(version.content)
            for version in case_versions
        }
        require(
            actual_version_references == EXPECTED_VERSION_REFERENCES,
            "OWNED_VERSION_REFERENCE_DRIFT",
        )
        require(not external_references, "EXTERNAL_CASE_VERSION_REFERENCE_FOUND")
        require(not extra_elements, "EXTRA_ELEMENT_ON_SYNTHETIC_PAGE")
        in_flight_runs = [run.id for run in runs if run.status in ACTIVE_RUN_STATUSES]
        require(not in_flight_runs, "OWNED_ASSET_RUN_IN_FLIGHT")

        history = {
            "web_case_versions": _rows_snapshot(case_versions),
            "web_element_versions": _rows_snapshot(element_versions),
            "element_locators": _rows_snapshot(locators),
            "runs": _rows_snapshot(runs),
            "case_runs": _rows_snapshot(case_runs),
            "step_runs": _rows_snapshot(step_runs),
            "web_execution_results": _rows_snapshot(web_results),
            "evidence": _rows_snapshot(evidence),
            "ai_calls": _rows_snapshot(ai_calls),
            "healing_proposals": _rows_snapshot(proposals),
            "failure_analyses": _rows_snapshot(analyses),
        }
        return {
            "project": {
                "id": project.id,
                "status": project.status,
                "row_sha256": _row_fingerprint(project)["row_sha256"],
            },
            "assets": asset_snapshots,
            "owned_case_version_references": actual_version_references,
            "external_case_version_references": external_references,
            "extra_elements_on_owned_pages": len(extra_elements),
            "owned_in_flight_run_ids": in_flight_runs,
            "history": history,
            "history_sha256": _json_hash(history),
        }


def _verify_api_identity(api: Any, snapshot: dict[str, Any]) -> None:
    for asset in snapshot["assets"]:
        case = api.request("GET", f"/web-cases/{asset['case']['id']}")
        pages = api.request(
            "GET", "/web-pages", params={"project_id": PROJECT_ID}, response_type=list
        )
        page = next((item for item in pages if item.get("id") == asset["page"]["id"]), None)
        elements = api.request(
            "GET",
            "/web-elements",
            params={"page_id": asset["page"]["id"]},
            response_type=list,
        )
        element = next(
            (item for item in elements if item.get("id") == asset["element"]["id"]),
            None,
        )
        require(isinstance(page, dict) and isinstance(element, dict), "API_ASSET_MISSING")
        require(
            all(
                case.get(key) == asset["case"][key]
                for key in ("id", "project_id", "code", "name", "status", "current_version_id")
            ),
            "API_CASE_IDENTITY_DRIFT",
        )
        require(
            all(
                page.get(key) == asset["page"][key]
                for key in ("id", "project_id", "code", "name", "description", "status")
            ),
            "API_PAGE_IDENTITY_DRIFT",
        )
        require(
            all(
                element.get(key) == asset["element"][key]
                for key in (
                    "id",
                    "project_id",
                    "page_id",
                    "name",
                    "description",
                    "status",
                    "current_version_id",
                )
            ),
            "API_ELEMENT_IDENTITY_DRIFT",
        )


def _get_api_asset(api: Any, asset_type: str, asset: dict[str, Any]) -> dict[str, Any]:
    if asset_type == "case":
        return api.request("GET", f"/web-cases/{asset['case']['id']}")
    if asset_type == "element":
        items = api.request(
            "GET",
            "/web-elements",
            params={"page_id": asset["page"]["id"]},
            response_type=list,
        )
        value = next(
            (item for item in items if item.get("id") == asset["element"]["id"]),
            None,
        )
    else:
        items = api.request(
            "GET", "/web-pages", params={"project_id": PROJECT_ID}, response_type=list
        )
        value = next((item for item in items if item.get("id") == asset["page"]["id"]), None)
    require(isinstance(value, dict), "API_ASSET_MISSING_AFTER_POST")
    return value


def _load_or_create_journal(protected: dict[str, Any]) -> dict[str, Any]:
    if JOURNAL_FILE.exists():
        journal = _read_json(JOURNAL_FILE)
        require(isinstance(journal, dict), "JOURNAL_INVALID")
        require(journal.get("package") == PACKAGE, "JOURNAL_PACKAGE_DRIFT")
        return journal
    journal = {
        "schema_version": 1,
        "package": PACKAGE,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "protected_file_hashes_before": protected["sha256"],
        "normal_login_posts": 1,
        "operations": [],
    }
    _write_json_atomic(JOURNAL_FILE, journal)
    return journal


def _archive_one(
    api: Any,
    journal: dict[str, Any],
    asset: dict[str, Any],
    asset_type: str,
) -> None:
    key = {"case": "case", "element": "element", "page": "page"}[asset_type]
    asset_id = int(asset[key]["id"])
    endpoint = {
        "case": f"/web-cases/{asset_id}/archive",
        "element": f"/web-elements/{asset_id}/archive",
        "page": f"/web-pages/{asset_id}/archive",
    }[asset_type]
    existing = next(
        (
            operation
            for operation in journal["operations"]
            if operation.get("asset_type") == asset_type
            and operation.get("asset_id") == asset_id
        ),
        None,
    )
    current = _get_api_asset(api, asset_type, asset)
    require(current.get("id") == asset_id, "API_ASSET_ID_DRIFT")
    if current.get("status") == "ARCHIVED":
        if existing is None:
            journal["operations"].append(
                {
                    "ordinal": len(journal["operations"]) + 1,
                    "asset_type": asset_type,
                    "asset_id": asset_id,
                    "endpoint": endpoint,
                    "before_status": "ARCHIVED",
                    "state": "SKIPPED_ALREADY_ARCHIVED",
                    "confirmed_at_utc": datetime.now(UTC).isoformat(),
                }
            )
        else:
            existing["state"] = "CONFIRMED_ARCHIVED"
            existing["confirmed_at_utc"] = datetime.now(UTC).isoformat()
        _write_json_atomic(JOURNAL_FILE, journal)
        return
    require(current.get("status") in {"APPROVED", "ACTIVE"}, "API_ASSET_STATUS_DRIFT")
    if existing is not None:
        require(
            existing.get("state") not in {"POST_STARTING", "UNKNOWN_RESULT"},
            "UNKNOWN_POST_RESULT_REQUIRES_MANUAL_REVIEW",
        )
        raise ArchiveStop("ACTIVE_ASSET_HAS_PRIOR_JOURNAL_ENTRY")

    operation = {
        "ordinal": len(journal["operations"]) + 1,
        "asset_type": asset_type,
        "asset_id": asset_id,
        "endpoint": endpoint,
        "before_status": current.get("status"),
        "state": "PLANNED_BEFORE_POST",
        "planned_at_utc": datetime.now(UTC).isoformat(),
    }
    journal["operations"].append(operation)
    _write_json_atomic(JOURNAL_FILE, journal)
    operation["state"] = "POST_STARTING"
    operation["post_started_at_utc"] = datetime.now(UTC).isoformat()
    _write_json_atomic(JOURNAL_FILE, journal)
    try:
        response = api.request("POST", endpoint)
    except Exception as exc:
        operation["state"] = "UNKNOWN_RESULT"
        operation["error_type"] = type(exc).__name__
        operation["stopped_at_utc"] = datetime.now(UTC).isoformat()
        _write_json_atomic(JOURNAL_FILE, journal)
        raise ArchiveStop("ARCHIVE_POST_RESULT_UNKNOWN") from exc
    operation["state"] = "RESPONSE_RECEIVED"
    operation["response_status"] = response.get("status")
    operation["response_received_at_utc"] = datetime.now(UTC).isoformat()
    _write_json_atomic(JOURNAL_FILE, journal)
    confirmed = _get_api_asset(api, asset_type, asset)
    require(
        confirmed.get("id") == asset_id and confirmed.get("status") == "ARCHIVED",
        "ARCHIVE_POST_NOT_CONFIRMED",
    )
    operation["state"] = "CONFIRMED_ARCHIVED"
    operation["confirmed_at_utc"] = datetime.now(UTC).isoformat()
    _write_json_atomic(JOURNAL_FILE, journal)


def _execute(preflight: dict[str, Any]) -> dict[str, Any]:
    current_protected = _protected_files()
    _validate_protected_files(current_protected)
    require(
        current_protected == preflight["protected_files"],
        "PROTECTED_FILES_CHANGED_AFTER_PREFLIGHT",
    )
    current = _database_snapshot(allow_archived=True)
    require(
        current["history"] == preflight["database"]["history"],
        "HISTORY_CHANGED_AFTER_PREFLIGHT",
    )
    api = _connect_api(DEFAULT_READY_FILE.resolve(), 20.0)
    _verify_api_identity(api, current)
    journal = _load_or_create_journal(current_protected)
    for asset_type in ("case", "element", "page"):
        for asset in current["assets"]:
            _archive_one(api, journal, asset, asset_type)

    post_protected = _protected_files()
    _validate_protected_files(post_protected)
    require(post_protected == preflight["protected_files"], "PROTECTED_FILES_CHANGED")
    post = _database_snapshot(allow_archived=True)
    require(post["history"] == preflight["database"]["history"], "HISTORY_CHANGED")
    require(
        all(
            item[asset_type]["status"] == "ARCHIVED"
            for item in post["assets"]
            for asset_type in ("case", "element", "page")
        ),
        "TARGET_ASSETS_NOT_ALL_ARCHIVED",
    )
    operations = journal["operations"]
    require(
        len(operations) == 6
        and all(operation.get("state") in {"CONFIRMED_ARCHIVED", "SKIPPED_ALREADY_ARCHIVED"} for operation in operations),
        "ARCHIVE_JOURNAL_INCOMPLETE",
    )
    business_post_count = sum(
        operation.get("post_started_at_utc") is not None for operation in operations
    )
    journal["completed_at_utc"] = datetime.now(UTC).isoformat()
    journal["business_post_count"] = business_post_count
    journal["protected_file_hashes_after"] = post_protected["sha256"]
    _write_json_atomic(JOURNAL_FILE, journal)
    verification = {
        "schema_version": 1,
        "package": PACKAGE,
        "status": "PASSED",
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "archive_plan_sha256": post_protected["sha256"]["archive_plan"],
        "assets": post["assets"],
        "protected_files": post_protected,
        "history": post["history"],
        "history_sha256_before": preflight["database"]["history_sha256"],
        "history_sha256_after": post["history_sha256"],
        "external_case_version_references": post[
            "external_case_version_references"
        ],
        "extra_elements_on_owned_pages": post["extra_elements_on_owned_pages"],
        "owned_in_flight_run_ids": post["owned_in_flight_run_ids"],
        "operations": {
            "normal_login_posts": 1,
            "business_archive_posts": business_post_count,
            "confirmed_archives": len(operations),
            "deletes": 0,
            "ai_calls": 0,
            "run_creations_or_dispatches": 0,
            "message_operations": 0,
        },
        "journal_sha256": _sha256(JOURNAL_FILE),
        "checks": {
            "six_assets_archived": True,
            "history_unchanged": post["history"] == preflight["database"]["history"],
            "protected_files_unchanged": post_protected == preflight["protected_files"],
            "no_external_version_references": not post[
                "external_case_version_references"
            ],
            "no_extra_elements_on_pages": post["extra_elements_on_owned_pages"] == 0,
            "no_owned_runs_in_flight": not post["owned_in_flight_run_ids"],
            "journal_complete": len(operations) == 6,
        },
    }
    require(all(verification["checks"].values()), "FINAL_VERIFICATION_FAILED")
    _write_json_atomic(VERIFICATION_FILE, verification)
    return verification


def _verify_completed(preflight: dict[str, Any]) -> dict[str, Any]:
    protected = _protected_files()
    _validate_protected_files(protected)
    require(protected == preflight["protected_files"], "PROTECTED_FILES_CHANGED")
    post = _database_snapshot(allow_archived=True)
    baseline = preflight["database"]
    require(post["history"] == baseline["history"], "HISTORY_CHANGED")
    require(post["project"] == baseline["project"], "PROJECT_CHANGED")
    require(
        post["owned_case_version_references"]
        == baseline["owned_case_version_references"],
        "OWNED_VERSION_REFERENCES_CHANGED",
    )
    require(
        all(
            item[asset_type]["status"] == "ARCHIVED"
            for item in post["assets"]
            for asset_type in ("case", "element", "page")
        ),
        "TARGET_ASSETS_NOT_ALL_ARCHIVED",
    )
    require(JOURNAL_FILE.is_file(), "JOURNAL_MISSING")
    journal = _read_json(JOURNAL_FILE)
    operations = journal.get("operations") if isinstance(journal, dict) else None
    require(isinstance(operations, list), "JOURNAL_INVALID")
    require(
        len(operations) == 6
        and all(
            operation.get("state")
            in {"CONFIRMED_ARCHIVED", "SKIPPED_ALREADY_ARCHIVED"}
            for operation in operations
        ),
        "ARCHIVE_JOURNAL_INCOMPLETE",
    )
    business_post_count = sum(
        operation.get("post_started_at_utc") is not None for operation in operations
    )
    checks = {
        "six_assets_archived": True,
        "history_unchanged": post["history"] == baseline["history"],
        "project_unchanged": post["project"] == baseline["project"],
        "owned_version_references_unchanged": (
            post["owned_case_version_references"]
            == baseline["owned_case_version_references"]
        ),
        "protected_files_unchanged": protected == preflight["protected_files"],
        "no_external_version_references": not post[
            "external_case_version_references"
        ],
        "no_extra_elements_on_pages": post["extra_elements_on_owned_pages"] == 0,
        "no_owned_runs_in_flight": not post["owned_in_flight_run_ids"],
        "journal_complete": len(operations) == 6,
    }
    require(all(checks.values()), "FINAL_VERIFICATION_FAILED")
    verification = {
        "schema_version": 1,
        "package": PACKAGE,
        "status": "PASSED",
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "verification_mode": "SELECT_SHOW_ONLY_NO_API",
        "archive_plan_sha256": protected["sha256"]["archive_plan"],
        "project": post["project"],
        "assets": post["assets"],
        "owned_case_version_references": post["owned_case_version_references"],
        "protected_files": protected,
        "history": post["history"],
        "history_sha256_before": baseline["history_sha256"],
        "history_sha256_after": post["history_sha256"],
        "external_case_version_references": post[
            "external_case_version_references"
        ],
        "extra_elements_on_owned_pages": post["extra_elements_on_owned_pages"],
        "owned_in_flight_run_ids": post["owned_in_flight_run_ids"],
        "operations": {
            "normal_login_posts": journal.get("normal_login_posts"),
            "business_archive_posts": business_post_count,
            "confirmed_archives": len(operations),
            "deletes": 0,
            "ai_calls": 0,
            "run_creations_or_dispatches": 0,
            "message_operations": 0,
        },
        "journal_sha256": _sha256(JOURNAL_FILE),
        "checks": checks,
    }
    _write_json_atomic(VERIFICATION_FILE, verification)
    return verification


def main() -> int:
    args = _parse_args()
    try:
        _validate_archive_plan()
        protected = _protected_files()
        _validate_protected_files(protected)
        if args.preflight_only:
            database = _database_snapshot(allow_archived=False)
            preflight = {
                "schema_version": 1,
                "package": PACKAGE,
                "status": "READY_NOT_EXECUTED",
                "checked_at_utc": datetime.now(UTC).isoformat(),
                "formal_db_guard": "SELECT_SHOW_ONLY",
                "archive_plan_sha256": protected["sha256"]["archive_plan"],
                "protected_files": protected,
                "database": database,
                "checks": {
                    "plan_exact": True,
                    "six_assets_exact": len(database["assets"]) == 2,
                    "no_external_version_references": not database[
                        "external_case_version_references"
                    ],
                    "no_extra_elements_on_pages": database[
                        "extra_elements_on_owned_pages"
                    ]
                    == 0,
                    "no_owned_runs_in_flight": not database["owned_in_flight_run_ids"],
                    "protected_files_match": True,
                },
            }
            require(all(preflight["checks"].values()), "PREFLIGHT_FAILED")
            _write_json_atomic(PREFLIGHT_FILE, preflight)
            print(
                json.dumps(
                    {
                        "status": preflight["status"],
                        "asset_groups": len(database["assets"]),
                        "history_sha256": database["history_sha256"],
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        require(PREFLIGHT_FILE.is_file(), "PREFLIGHT_MISSING")
        preflight = _read_json(PREFLIGHT_FILE)
        require(
            isinstance(preflight, dict)
            and preflight.get("package") == PACKAGE
            and preflight.get("status") == "READY_NOT_EXECUTED",
            "PREFLIGHT_INVALID",
        )
        verification = (
            _verify_completed(preflight) if args.verify_only else _execute(preflight)
        )
        print(
            json.dumps(
                {
                    "status": verification["status"],
                    "business_archive_posts": verification["operations"][
                        "business_archive_posts"
                    ],
                    "confirmed_archives": verification["operations"][
                        "confirmed_archives"
                    ],
                    "history_unchanged": verification["checks"][
                        "history_unchanged"
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except ArchiveStop as exc:
        failure = {
            "schema_version": 1,
            "package": PACKAGE,
            "status": "STOPPED",
            "stopped_at_utc": datetime.now(UTC).isoformat(),
            "error_code": exc.code,
            "journal_exists": JOURNAL_FILE.exists(),
            "journal_sha256": _sha256(JOURNAL_FILE) if JOURNAL_FILE.exists() else None,
        }
        _write_json_atomic(FAILURE_FILE, failure)
        print(f"[p5f-archive] STOPPED {exc.code}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - never emit response or DB details
        failure = {
            "schema_version": 1,
            "package": PACKAGE,
            "status": "STOPPED",
            "stopped_at_utc": datetime.now(UTC).isoformat(),
            "error_code": "UNEXPECTED_ERROR",
            "error_type": type(exc).__name__,
            "journal_exists": JOURNAL_FILE.exists(),
            "journal_sha256": _sha256(JOURNAL_FILE) if JOURNAL_FILE.exists() else None,
        }
        _write_json_atomic(FAILURE_FILE, failure)
        print("[p5f-archive] STOPPED UNEXPECTED_ERROR", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
