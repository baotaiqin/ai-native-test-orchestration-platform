"""Emit a bounded, secret-free outcome snapshot for the fixed P5-E Run."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.infrastructure.db.session import SessionLocal
from app.modules.evidence.models import EvidenceArtifact
from app.modules.prompt_center.models import AiCallLog
from app.modules.runners.models import RunnerSlot
from app.modules.runs.models import RunDispatchOutbox, RunWebExecutionResult, TestRun

TARGET_RUN_ID = "run_15c59b0b27964dd8a6fec2fcca840f9c"
TERMINAL = {"SUCCESS", "FAILED", "CANCELLED", "TIMEOUT"}


def _trace_summary(trace: Any) -> dict[str, Any] | None:
    if not isinstance(trace, dict):
        return None
    attempts = trace.get("locator_attempts")
    safe_attempts = []
    if isinstance(attempts, list):
        for attempt in attempts[:20]:
            if not isinstance(attempt, dict):
                continue
            safe_attempts.append(
                {
                    "strategy": attempt.get("strategy"),
                    "priority": attempt.get("priority"),
                    "status": attempt.get("status"),
                }
            )
    healing = trace.get("healing_context")
    safe_healing: dict[str, Any] = {"present": False}
    if isinstance(healing, dict):
        candidates = healing.get("dom_candidates")
        safe_healing = {
            "present": True,
            "trigger": healing.get("trigger"),
            "element_version_id": healing.get("element_version_id"),
            "page_url": healing.get("page_url"),
            "page_title": healing.get("page_title"),
            "dom_candidate_count": len(candidates)
            if isinstance(candidates, list)
            else 0,
            "dom_candidate_key_sets": sorted(
                {
                    tuple(sorted(candidate))
                    for candidate in candidates or []
                    if isinstance(candidate, dict)
                }
            ),
        }
    return {
        "node_id": trace.get("node_id"),
        "status": trace.get("status"),
        "error_type": trace.get("error_type"),
        "duration_ms": trace.get("duration_ms"),
        "locator_attempts": safe_attempts,
        "healing_context": safe_healing,
    }


def main() -> int:
    try:
        with SessionLocal() as session:
            run = session.get(TestRun, TARGET_RUN_ID)
            if run is None:
                raise LookupError("run")
            outbox = session.scalar(
                select(RunDispatchOutbox).where(
                    RunDispatchOutbox.run_id == TARGET_RUN_ID
                )
            )
            web_result = session.scalar(
                select(RunWebExecutionResult).where(
                    RunWebExecutionResult.run_id == TARGET_RUN_ID
                )
            )
            evidence_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(EvidenceArtifact)
                    .where(EvidenceArtifact.run_id == TARGET_RUN_ID)
                )
                or 0
            )
            evidence_types = list(
                session.scalars(
                    select(EvidenceArtifact.artifact_type)
                    .where(EvidenceArtifact.run_id == TARGET_RUN_ID)
                    .order_by(EvidenceArtifact.artifact_type)
                ).all()
            )
            web_slot = session.scalar(
                select(RunnerSlot).where(
                    RunnerSlot.runner_id == run.runner_id,
                    RunnerSlot.slot_type == "WEB",
                )
            )
            ai_call_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(AiCallLog)
                    .where(AiCallLog.entity_id == TARGET_RUN_ID)
                )
                or 0
            )
            traces = web_result.traces if web_result is not None else []
            result = {
                "ok": True,
                "run_id": run.id,
                "run_status": run.status,
                "run_error_type": run.error_type,
                "terminal": run.status in TERMINAL,
                "case_runs": [
                    {
                        "id": case.id,
                        "status": case.status,
                        "error_type": case.error_type,
                        "step_runs": [
                            {
                                "id": step.id,
                                "node_id": step.node_id,
                                "status": step.status,
                                "error_type": step.error_type,
                            }
                            for step in case.step_runs
                        ],
                    }
                    for case in run.case_runs
                ],
                "outbox": {
                    "status": outbox.status if outbox is not None else None,
                    "claimed": outbox is not None
                    and outbox.claimed_runner_id is not None,
                },
                "web_result": (
                    {
                        "status": web_result.status,
                        "outcome": web_result.outcome,
                        "error_type": web_result.error_type,
                        "trace_count": len(traces),
                        "traces": [
                            summary
                            for trace in traces
                            if (summary := _trace_summary(trace)) is not None
                        ],
                    }
                    if web_result is not None
                    else None
                ),
                "evidence": {
                    "count": evidence_count,
                    "types": evidence_types,
                },
                "web_slot": (
                    {"total": web_slot.total, "available": web_slot.available}
                    if web_slot is not None
                    else None
                ),
                "ai_call_count": ai_call_count,
            }
    except Exception as exc:  # noqa: BLE001 - never serialize database details
        result = {"ok": False, "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
