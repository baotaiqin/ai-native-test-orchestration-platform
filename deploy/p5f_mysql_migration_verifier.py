from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError

REVISIONS = (
    "20260902_0030",
    "20260904_0031",
    "20260907_0032",
    "20260908_0033",
    "20260908_0034",
    "20260908_0035",
    "20260908_0036",
    "20260909_0037",
    "20260909_0038",
)
REVISION_ORDER = {revision: index for index, revision in enumerate(REVISIONS)}

LEGACY_MODEL_NAME = "p5f-validation-model"
LEGACY_PROJECT_CODE = "P5FVAL"
LEGACY_SECRET_NAME = "p5f-validation-secret"
ORIGINAL_ENCRYPTED_VALUE = "synthetic-validation-ciphertext-v1"
ORIGINAL_FINGERPRINT = hashlib.sha256(b"p5f-validation-v1").hexdigest()
ROTATED_ENCRYPTED_VALUE = "synthetic-validation-ciphertext-v2"
ROTATED_FINGERPRINT = hashlib.sha256(b"p5f-validation-v2").hexdigest()
ORIGINAL_PROVIDER = "OPENAI_COMPATIBLE"
ORIGINAL_BASE_URL = "http://127.0.0.1:9/v1"
ROTATED_PROVIDER = "P5F_VALIDATION"
ROTATED_BASE_URL = "http://localhost:9/v2"


TABLE_INTRODUCTIONS = {
    "web_recordings": "20260904_0031",
    "web_recording_ai_suggestions": "20260907_0032",
    "locator_healing_proposals": "20260908_0033",
    "web_failure_analyses": "20260908_0034",
    "model_provider_connections": "20260908_0036",
}

MODEL_COLUMN_INTRODUCTIONS = {
    "api_key_encrypted_value": "20260908_0035",
    "api_key_fingerprint": "20260908_0035",
    "api_key_rotated_at": "20260908_0035",
    "connection_id": "20260908_0036",
    "model_vendor": "20260908_0036",
    "metadata_source": "20260909_0037",
    "metadata_synced_at": "20260909_0037",
    "description": "20260909_0037",
    "supports_reasoning": "20260909_0037",
    "max_input_tokens": "20260909_0037",
    "max_output_tokens": "20260909_0037",
    "max_reasoning_tokens": "20260909_0037",
    "reasoning_max_input_tokens": "20260909_0037",
    "reasoning_max_output_tokens": "20260909_0037",
    "input_modalities": "20260909_0037",
    "output_modalities": "20260909_0037",
    "capabilities": "20260909_0037",
    "features": "20260909_0037",
    "pricing_tiers": "20260909_0037",
    "published_at": "20260909_0037",
    "model_category": "20260909_0038",
}

EXPECTED_STRUCTURES = {
    "web_recordings": {
        "checks": 8,
        "foreign_keys": {
            ("project_id", "projects"),
            ("environment_id", "environments"),
            ("runner_id", "runners"),
            ("session_profile_id", "session_profiles"),
            ("saved_session_profile_id", "session_profiles"),
            ("claimed_runner_id", "runners"),
            ("confirmed_web_case_id", "web_cases"),
            ("confirmed_web_case_version_id", "web_case_versions"),
        },
        "indexes": {
            ("project_id",),
            ("environment_id",),
            ("runner_id",),
            ("session_profile_id",),
            ("saved_session_profile_id",),
            ("project_id", "status", "created_at"),
            ("runner_id", "status"),
            ("project_id", "confirmed_web_case_id"),
            ("message_id",),
        },
    },
    "web_recording_ai_suggestions": {
        "checks": 4,
        "foreign_keys": {
            ("project_id", "projects"),
            ("recording_id", "web_recordings"),
            ("ai_call_id", "ai_call_logs"),
            ("confirmed_web_case_id", "web_cases"),
            ("confirmed_web_case_version_id", "web_case_versions"),
        },
        "indexes": {
            ("project_id",),
            ("recording_id",),
            ("recording_id", "status", "created_at"),
            ("project_id", "created_at"),
            ("ai_call_id",),
            ("recording_id", "draft_key"),
        },
    },
    "locator_healing_proposals": {
        "checks": 6,
        "foreign_keys": {
            ("project_id", "projects"),
            ("run_id", "runs"),
            ("case_run_id", "case_runs"),
            ("web_case_id", "web_cases"),
            ("web_case_version_id", "web_case_versions"),
            ("ai_call_id", "ai_call_logs"),
            ("created_element_version_id", "web_element_versions"),
            ("created_web_case_version_id", "web_case_versions"),
        },
        "indexes": {
            ("project_id",),
            ("run_id",),
            ("case_run_id",),
            ("web_case_id",),
            ("run_id", "case_run_id", "node_id", "created_at"),
            ("project_id", "created_at"),
            ("status", "created_at"),
            ("ai_call_id",),
            ("run_id", "case_run_id", "node_id", "draft_key"),
        },
    },
    "web_failure_analyses": {
        "checks": 3,
        "foreign_keys": {
            ("project_id", "projects"),
            ("run_id", "runs"),
            ("case_run_id", "case_runs"),
            ("prompt_version_id", "prompt_versions"),
            ("output_schema_id", "output_schemas"),
            ("ai_call_id", "ai_call_logs"),
        },
        "indexes": {
            ("project_id",),
            ("run_id",),
            ("case_run_id",),
            ("run_id", "case_run_id", "created_at"),
            ("project_id", "created_at"),
            ("status", "created_at"),
            ("ai_call_id",),
            ("run_id", "case_run_id", "draft_key"),
        },
    },
    "model_provider_connections": {
        "checks": 3,
        "foreign_keys": set(),
        "indexes": {("name",)},
    },
}


def _database_name(connection: Connection) -> str:
    value = connection.execute(text("SELECT DATABASE()")).scalar_one()
    if not isinstance(value, str) or not value:
        raise RuntimeError("No active validation database was selected.")
    return value


def _tables(connection: Connection, schema: str) -> set[str]:
    rows = connection.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
        ),
        {"schema": schema},
    )
    return {str(row[0]) for row in rows}


def _columns(connection: Connection, schema: str, table: str) -> set[str]:
    rows = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table"
        ),
        {"schema": schema, "table": table},
    )
    return {str(row[0]) for row in rows}


def _datetime_precision(
    connection: Connection, schema: str, table: str, column: str
) -> int:
    value = connection.execute(
        text(
            "SELECT datetime_precision FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table "
            "AND column_name = :column"
        ),
        {"schema": schema, "table": table, "column": column},
    ).scalar_one()
    if not isinstance(value, int) or not 0 <= value <= 6:
        raise RuntimeError("The persisted timestamp precision is unsupported.")
    return value


def _column_nullability_and_default(
    connection: Connection, schema: str, table: str, column: str
) -> tuple[bool, object | None]:
    is_nullable, column_default = connection.execute(
        text(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table "
            "AND column_name = :column"
        ),
        {"schema": schema, "table": table, "column": column},
    ).one()
    return str(is_nullable).upper() == "YES", column_default


def _check_clauses(connection: Connection, schema: str, table: str) -> list[str]:
    rows = connection.execute(
        text(
            "SELECT cc.check_clause "
            "FROM information_schema.table_constraints AS tc "
            "JOIN information_schema.check_constraints AS cc "
            "ON cc.constraint_schema = tc.constraint_schema "
            "AND cc.constraint_name = tc.constraint_name "
            "WHERE tc.table_schema = :schema AND tc.table_name = :table "
            "AND tc.constraint_type = 'CHECK'"
        ),
        {"schema": schema, "table": table},
    )
    return [str(row[0]).lower() for row in rows]


def _foreign_keys(
    connection: Connection, schema: str, table: str
) -> set[tuple[str, str]]:
    rows = connection.execute(
        text(
            "SELECT column_name, referenced_table_name "
            "FROM information_schema.key_column_usage "
            "WHERE table_schema = :schema AND table_name = :table "
            "AND referenced_table_name IS NOT NULL"
        ),
        {"schema": schema, "table": table},
    )
    return {(str(row[0]), str(row[1])) for row in rows}


def _indexes(connection: Connection, schema: str, table: str) -> set[tuple[str, ...]]:
    rows = connection.execute(
        text(
            "SELECT index_name, seq_in_index, column_name "
            "FROM information_schema.statistics "
            "WHERE table_schema = :schema AND table_name = :table "
            "ORDER BY index_name, seq_in_index"
        ),
        {"schema": schema, "table": table},
    )
    grouped: dict[str, list[str]] = {}
    for index_name, _, column_name in rows:
        if column_name is None:
            continue
        grouped.setdefault(str(index_name), []).append(str(column_name))
    return {tuple(columns) for columns in grouped.values()}


def _require_subset[T](required: set[T], actual: set[T], label: str) -> None:
    missing = required - actual
    if missing:
        raise RuntimeError(f"Missing {label}: {sorted(missing)!r}")


def _current_revision(connection: Connection) -> str:
    return str(
        connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    )


def assert_stage(engine: Engine, expected_revision: str) -> None:
    if expected_revision not in REVISION_ORDER:
        raise RuntimeError(f"Unsupported validation revision: {expected_revision}")
    stage = REVISION_ORDER[expected_revision]
    with engine.connect() as connection:
        schema = _database_name(connection)
        actual_revision = _current_revision(connection)
        if actual_revision != expected_revision:
            raise RuntimeError(
                f"Expected revision {expected_revision}, found {actual_revision}."
            )
        tables = _tables(connection, schema)
        for table, introduced_revision in TABLE_INTRODUCTIONS.items():
            should_exist = stage >= REVISION_ORDER[introduced_revision]
            if (table in tables) != should_exist:
                state = "present" if should_exist else "absent"
                raise RuntimeError(
                    f"Expected table {table} to be {state} at {expected_revision}."
                )
            if not should_exist:
                continue
            structure = EXPECTED_STRUCTURES[table]
            clauses = _check_clauses(connection, schema, table)
            if len(clauses) < int(structure["checks"]):
                raise RuntimeError(
                    f"Expected at least {structure['checks']} CHECK constraints on {table}, "
                    f"found {len(clauses)}."
                )
            _require_subset(
                set(structure["foreign_keys"]),
                _foreign_keys(connection, schema, table),
                f"foreign keys on {table}",
            )
            _require_subset(
                set(structure["indexes"]),
                _indexes(connection, schema, table),
                f"indexes on {table}",
            )

        model_columns = _columns(connection, schema, "model_configurations")
        for column, introduced_revision in MODEL_COLUMN_INTRODUCTIONS.items():
            should_exist = stage >= REVISION_ORDER[introduced_revision]
            if (column in model_columns) != should_exist:
                state = "present" if should_exist else "absent"
                raise RuntimeError(
                    f"Expected model_configurations.{column} to be {state} at "
                    f"{expected_revision}."
                )

        max_context_nullable, max_context_default = _column_nullability_and_default(
            connection, schema, "model_configurations", "max_context"
        )
        if stage < REVISION_ORDER["20260909_0037"]:
            if max_context_nullable or str(max_context_default) != "128000":
                raise RuntimeError(
                    "The pre-0037 max_context NOT NULL DEFAULT 128000 contract "
                    "was not restored."
                )
        elif not max_context_nullable:
            raise RuntimeError("Revision 0037 did not make max_context nullable.")

        model_checks = _check_clauses(connection, schema, "model_configurations")
        joined_checks = "\n".join(model_checks)
        if (
            stage >= REVISION_ORDER["20260908_0035"]
            and "api_key_fingerprint" not in joined_checks
        ):
            raise RuntimeError("Missing API-key fingerprint CHECK constraint.")
        if stage >= REVISION_ORDER["20260909_0037"]:
            for column in (
                "max_input_tokens",
                "max_output_tokens",
                "max_reasoning_tokens",
                "reasoning_max_input_tokens",
                "reasoning_max_output_tokens",
            ):
                if column not in joined_checks:
                    raise RuntimeError(f"Missing nonnegative CHECK for {column}.")
        if (
            stage >= REVISION_ORDER["20260909_0038"]
            and "model_category" not in joined_checks
        ):
            raise RuntimeError("Missing model-category CHECK constraint.")

        model_fks = _foreign_keys(connection, schema, "model_configurations")
        model_indexes = _indexes(connection, schema, "model_configurations")
        connection_fk = ("connection_id", "model_provider_connections")
        if stage >= REVISION_ORDER["20260908_0036"]:
            _require_subset({connection_fk}, model_fks, "model connection foreign key")
            _require_subset(
                {("connection_id",)}, model_indexes, "model connection index"
            )
        elif connection_fk in model_fks:
            raise RuntimeError("Connection foreign key survived below revision 0036.")

    print(f"[verify] schema structure matches {expected_revision}")


def seed_legacy_model(engine: Engine) -> None:
    with engine.begin() as connection:
        existing = connection.execute(
            text("SELECT COUNT(*) FROM model_configurations WHERE name = :name"),
            {"name": LEGACY_MODEL_NAME},
        ).scalar_one()
        if existing:
            raise RuntimeError("Synthetic legacy model already exists.")
        project_id = connection.execute(
            text(
                "INSERT INTO projects (name, code, owner_id) "
                "VALUES (:name, :code, :owner_id)"
            ),
            {
                "name": "P5-F migration validation",
                "code": LEGACY_PROJECT_CODE,
                "owner_id": "p5f-validator",
            },
        ).lastrowid
        secret_id = connection.execute(
            text(
                "INSERT INTO secrets "
                "(project_id, name, secret_type, encrypted_value, fingerprint) "
                "VALUES (:project_id, :name, :secret_type, :encrypted_value, :fingerprint)"
            ),
            {
                "project_id": project_id,
                "name": LEGACY_SECRET_NAME,
                "secret_type": "API_KEY",
                "encrypted_value": ORIGINAL_ENCRYPTED_VALUE,
                "fingerprint": ORIGINAL_FINGERPRINT,
            },
        ).lastrowid
        connection.execute(
            text(
                "INSERT INTO model_configurations "
                "(name, provider, base_url, api_key_secret_id, model_name, model_type, "
                "created_by) VALUES "
                "(:name, :provider, :base_url, :secret_id, :model_name, :model_type, "
                ":created_by)"
            ),
            {
                "name": LEGACY_MODEL_NAME,
                "provider": ORIGINAL_PROVIDER,
                "base_url": ORIGINAL_BASE_URL,
                "secret_id": secret_id,
                "model_name": "qwen-p5f-validation",
                "model_type": "TEXT",
                "created_by": "p5f-validator",
            },
        )
    print(
        "[verify] inserted one synthetic legacy model and Secret reference at revision 0034"
    )


def _legacy_row(connection: Connection) -> sa.RowMapping:
    return (
        connection.execute(
            text(
                "SELECT model.*, secret.encrypted_value AS secret_encrypted_value, "
                "secret.fingerprint AS secret_fingerprint "
                "FROM model_configurations AS model "
                "JOIN secrets AS secret ON secret.id = model.api_key_secret_id "
                "WHERE model.name = :name"
            ),
            {"name": LEGACY_MODEL_NAME},
        )
        .mappings()
        .one()
    )


def assert_base_data(engine: Engine) -> None:
    with engine.connect() as connection:
        row = _legacy_row(connection)
        if row["secret_encrypted_value"] != ORIGINAL_ENCRYPTED_VALUE:
            raise RuntimeError(
                "Synthetic Secret ciphertext changed across the migration cycle."
            )
        if row["secret_fingerprint"] != ORIGINAL_FINGERPRINT:
            raise RuntimeError(
                "Synthetic Secret fingerprint changed across the migration cycle."
            )
        if row["api_key_secret_id"] is None:
            raise RuntimeError("Legacy model-to-Secret reference was lost.")
    print("[verify] non-empty base model and Secret reference remain intact")


def assert_0035_backfill(engine: Engine) -> None:
    with engine.connect() as connection:
        row = _legacy_row(connection)
        if row["api_key_encrypted_value"] != ORIGINAL_ENCRYPTED_VALUE:
            raise RuntimeError("Revision 0035 did not backfill encrypted API-key data.")
        if row["api_key_fingerprint"] != ORIGINAL_FINGERPRINT:
            raise RuntimeError(
                "Revision 0035 did not backfill the API-key fingerprint."
            )
    print("[verify] revision 0035 preserved the legacy encrypted credential state")


def assert_0036_backfill(engine: Engine) -> None:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT model.connection_id, model.model_vendor, model.provider AS old_provider, "
                    "model.base_url AS old_base_url, model.api_key_encrypted_value AS old_encrypted, "
                    "model.api_key_fingerprint AS old_fingerprint, "
                    "channel.provider, channel.base_url, channel.access_type, "
                    "channel.api_key_encrypted_value, channel.api_key_fingerprint "
                    "FROM model_configurations AS model "
                    "JOIN model_provider_connections AS channel "
                    "ON channel.id = model.connection_id "
                    "WHERE model.name = :name"
                ),
                {"name": LEGACY_MODEL_NAME},
            )
            .mappings()
            .one()
        )
        if row["connection_id"] is None or not row["model_vendor"]:
            raise RuntimeError(
                "Revision 0036 did not create the required model connection link."
            )
        if (
            row["provider"] != row["old_provider"]
            or row["base_url"] != row["old_base_url"]
        ):
            raise RuntimeError(
                "Revision 0036 changed provider routing during backfill."
            )
        if row["api_key_encrypted_value"] != row["old_encrypted"]:
            raise RuntimeError(
                "Revision 0036 changed encrypted credential data during backfill."
            )
        if row["api_key_fingerprint"] != row["old_fingerprint"]:
            raise RuntimeError(
                "Revision 0036 changed credential fingerprint during backfill."
            )
        if row["access_type"] != "SELF_HOSTED":
            raise RuntimeError(
                "Revision 0036 did not classify the loopback endpoint safely."
            )
    print(
        "[verify] revision 0036 preserved routing and credential data in the new connection"
    )


def mutate_0036_connection(engine: Engine) -> None:
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE model_provider_connections AS channel "
                "JOIN model_configurations AS model ON model.connection_id = channel.id "
                "SET channel.provider = :provider, channel.base_url = :base_url, "
                "channel.api_key_encrypted_value = :encrypted, "
                "channel.api_key_fingerprint = :fingerprint "
                "WHERE model.name = :name"
            ),
            {
                "provider": ROTATED_PROVIDER,
                "base_url": ROTATED_BASE_URL,
                "encrypted": ROTATED_ENCRYPTED_VALUE,
                "fingerprint": ROTATED_FINGERPRINT,
                "name": LEGACY_MODEL_NAME,
            },
        )
        if result.rowcount != 1:
            raise RuntimeError("Expected to mutate exactly one synthetic connection.")
    print(
        "[verify] prepared synthetic post-0036 channel changes for downgrade protection"
    )


def prepare_0037_null_context(engine: Engine) -> None:
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE model_configurations SET max_context = NULL, "
                "metadata_source = 'P5F_VALIDATION', supports_reasoning = 1, "
                "max_input_tokens = 1024 WHERE name = :name"
            ),
            {"name": LEGACY_MODEL_NAME},
        )
        if result.rowcount != 1:
            raise RuntimeError(
                "Expected to update exactly one synthetic model at revision 0037."
            )
    print("[verify] prepared nullable 0037 metadata for downgrade protection")


def assert_0037_downgrade_protection(engine: Engine) -> None:
    with engine.connect() as connection:
        value = connection.execute(
            text("SELECT max_context FROM model_configurations WHERE name = :name"),
            {"name": LEGACY_MODEL_NAME},
        ).scalar_one()
        if value != 128000:
            raise RuntimeError(
                "Revision 0037 downgrade did not restore non-null max_context."
            )
    print("[verify] revision 0037 downgrade restored a safe non-null context limit")


def assert_0036_downgrade_protection(engine: Engine) -> None:
    with engine.connect() as connection:
        row = _legacy_row(connection)
        expected = {
            "provider": ROTATED_PROVIDER,
            "base_url": ROTATED_BASE_URL,
            "api_key_encrypted_value": ROTATED_ENCRYPTED_VALUE,
            "api_key_fingerprint": ROTATED_FINGERPRINT,
        }
        for column, value in expected.items():
            if row[column] != value:
                raise RuntimeError(
                    f"Revision 0036 downgrade did not restore legacy field {column}."
                )
    print(
        "[verify] revision 0036 downgrade restored channel state into legacy model fields"
    )


def assert_0038_check(engine: Engine) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "UPDATE model_configurations SET model_category = 'INVALID_P5F' "
                    "WHERE name = :name"
                ),
                {"name": LEGACY_MODEL_NAME},
            )
        except DBAPIError:
            transaction.rollback()
        else:
            transaction.rollback()
            raise RuntimeError(
                "Revision 0038 model-category CHECK did not reject an invalid value."
            )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE model_configurations SET model_category = 'LLM' WHERE name = :name"
            ),
            {"name": LEGACY_MODEL_NAME},
        )
    print("[verify] revision 0038 CHECK rejects invalid categories and accepts LLM")


def assert_final_data(engine: Engine) -> None:
    assert_base_data(engine)
    assert_0036_backfill(engine)
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT model.model_category, COUNT(channel.id) AS channel_count "
                    "FROM model_configurations AS model "
                    "JOIN model_provider_connections AS channel ON channel.id = model.connection_id "
                    "WHERE model.name = :name GROUP BY model.model_category"
                ),
                {"name": LEGACY_MODEL_NAME},
            )
            .mappings()
            .one()
        )
        if row["model_category"] != "LLM" or row["channel_count"] != 1:
            raise RuntimeError(
                "Final non-empty model data did not survive the full round trip."
            )
    print("[verify] final non-empty data and connection cardinality are intact")


def assert_timestampadd_boundary(engine: Engine) -> None:
    timeout_ms = 1501
    grace_microseconds = 60_000_000
    with engine.connect() as connection:
        schema = _database_name(connection)
        precision = _datetime_precision(connection, schema, "runs", "started_at")
        aligned_microseconds = 123456 - (123456 % (10 ** (6 - precision)))
        started_at = datetime(
            2026,
            9,
            9,
            0,
            0,
            0,
            aligned_microseconds,
            tzinfo=UTC,
        ).replace(tzinfo=None)
        expected_deadline = started_at + timedelta(
            milliseconds=timeout_ms,
            microseconds=grace_microseconds,
        )
        persisted_datetime_type = f"DATETIME({precision})"
        row = (
            connection.execute(
                text(
                    "SELECT "
                    "TIMESTAMPADD(MICROSECOND, :timeout_ms * 1000 + :grace_us, "
                    f"CAST(:started_at AS {persisted_datetime_type})) AS deadline, "
                    "TIMESTAMPADD(MICROSECOND, :timeout_ms * 1000 + :grace_us, "
                    f"CAST(:started_at AS {persisted_datetime_type})) "
                    "< CAST(:before_deadline AS DATETIME(6)) "
                    "AS before_is_overdue, "
                    "TIMESTAMPADD(MICROSECOND, :timeout_ms * 1000 + :grace_us, "
                    f"CAST(:started_at AS {persisted_datetime_type})) "
                    "< CAST(:at_deadline AS DATETIME(6)) "
                    "AS equal_is_overdue, "
                    "TIMESTAMPADD(MICROSECOND, :timeout_ms * 1000 + :grace_us, "
                    f"CAST(:started_at AS {persisted_datetime_type})) "
                    "< CAST(:after_deadline AS DATETIME(6)) "
                    "AS after_is_overdue"
                ),
                {
                    "timeout_ms": timeout_ms,
                    "grace_us": grace_microseconds,
                    "started_at": started_at,
                    "before_deadline": expected_deadline - timedelta(microseconds=1),
                    "at_deadline": expected_deadline,
                    "after_deadline": expected_deadline + timedelta(microseconds=1),
                },
            )
            .mappings()
            .one()
        )
        if row["deadline"] != expected_deadline:
            raise RuntimeError(
                "MySQL TIMESTAMPADD did not preserve millisecond precision."
            )
        observed = (
            bool(row["before_is_overdue"]),
            bool(row["equal_is_overdue"]),
            bool(row["after_is_overdue"]),
        )
        if observed != (False, False, True):
            raise RuntimeError("MySQL overdue boundary semantics are not strict-after.")
    print(
        f"[verify] MySQL TIMESTAMPADD matches runs.started_at DATETIME({precision}), "
        "preserves millisecond budget, and uses strict before/equal/after semantics"
    )


def build_engine() -> Engine:
    database_url = os.environ.get("APP_DATABASE_URL")
    if not database_url:
        raise RuntimeError("The isolated P5-F database target is not configured.")
    try:
        parsed_url = make_url(database_url)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "The isolated P5-F database target is not approved."
        ) from exc
    if not (
        parsed_url.drivername == "mysql+pymysql"
        and parsed_url.host == "127.0.0.1"
        and parsed_url.port is not None
        and parsed_url.port != 3306
        and parsed_url.database == "p5f_validation"
        and parsed_url.username == "p5f_validator"
        and parsed_url.password
        and dict(parsed_url.query) == {"charset": "utf8mb4"}
    ):
        raise RuntimeError("The isolated P5-F database target is not approved.")
    return sa.create_engine(parsed_url, pool_pre_ping=True, hide_parameters=True)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the isolated P5-F MySQL migration cycle."
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=(
            "assert-stage",
            "seed-legacy-model",
            "assert-base-data",
            "assert-0035-backfill",
            "assert-0036-backfill",
            "mutate-0036-connection",
            "prepare-0037-null-context",
            "assert-0037-downgrade-protection",
            "assert-0036-downgrade-protection",
            "assert-0038-check",
            "assert-final-data",
            "assert-timestampadd-boundary",
        ),
    )
    parser.add_argument("--expected-revision")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    engine = build_engine()
    try:
        if args.action == "assert-stage":
            if not args.expected_revision:
                raise RuntimeError("--expected-revision is required for assert-stage.")
            assert_stage(engine, args.expected_revision)
        elif args.action == "seed-legacy-model":
            seed_legacy_model(engine)
        elif args.action == "assert-base-data":
            assert_base_data(engine)
        elif args.action == "assert-0035-backfill":
            assert_0035_backfill(engine)
        elif args.action == "assert-0036-backfill":
            assert_0036_backfill(engine)
        elif args.action == "mutate-0036-connection":
            mutate_0036_connection(engine)
        elif args.action == "prepare-0037-null-context":
            prepare_0037_null_context(engine)
        elif args.action == "assert-0037-downgrade-protection":
            assert_0037_downgrade_protection(engine)
        elif args.action == "assert-0036-downgrade-protection":
            assert_0036_downgrade_protection(engine)
        elif args.action == "assert-0038-check":
            assert_0038_check(engine)
        elif args.action == "assert-final-data":
            assert_final_data(engine)
        elif args.action == "assert-timestampadd-boundary":
            assert_timestampadd_boundary(engine)
        else:
            raise RuntimeError(f"Unhandled verifier action: {args.action}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
