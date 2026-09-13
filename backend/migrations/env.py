import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import CheckConstraint, engine_from_config, inspect, pool

import app.modules.api_definitions.models  # noqa: F401
import app.modules.auth.models  # noqa: F401
import app.modules.ci_cd.models  # noqa: F401
import app.modules.database_connections.models  # noqa: F401
import app.modules.datasets.models  # noqa: F401
import app.modules.defect_drafts.models  # noqa: F401
import app.modules.environments.models  # noqa: F401
import app.modules.evidence.models  # noqa: F401
import app.modules.model_center.models  # noqa: F401
import app.modules.performance.models  # noqa: F401
import app.modules.projects.models  # noqa: F401
import app.modules.prompt_center.models  # noqa: F401
import app.modules.requirement_reviews.models  # noqa: F401
import app.modules.requirements.models  # noqa: F401
import app.modules.resource_registry.models  # noqa: F401
import app.modules.run_requirement_snapshots.models  # noqa: F401
import app.modules.runners.models  # noqa: F401
import app.modules.runs.models  # noqa: F401
import app.modules.scenarios.models  # noqa: F401
import app.modules.schedules.models  # noqa: F401
import app.modules.secrets.models  # noqa: F401
import app.modules.test_cases.models  # noqa: F401
import app.modules.test_plans.models  # noqa: F401
import app.modules.web_cases.models  # noqa: F401
import app.modules.web_design.models  # noqa: F401
import app.modules.web_failure_analysis.models  # noqa: F401
import app.modules.web_healing.models  # noqa: F401
import app.modules.web_recording_ai.models  # noqa: F401
import app.modules.web_recordings.models  # noqa: F401
from app.core.config import get_settings
from app.infrastructure.db.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_INTENTIONAL_DATABASE_ONLY_FOREIGN_KEYS = {
    (
        "prompt_definitions",
        "fk_prompt_definitions_current_version_id_prompt_versions",
    ),
    ("requirements", "fk_requirements_current_version_id_requirement_versions"),
    ("test_cases", "fk_test_cases_current_version_id_test_case_versions"),
}


def _normalize_check_sql(sqltext: object) -> str:
    """Compare CHECK expressions by meaning, not MySQL's reflected formatting."""

    normalized = str(sqltext).lower().replace("`", "").replace("_utf8mb4", "")
    return re.sub(r"[\s()]+", "", normalized)


def _check_constraint_filter(connection):
    metadata_signatures = {
        (table.name, _normalize_check_sql(constraint.sqltext))
        for table in target_metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    database_signatures: set[tuple[str, str]] | None = None

    def include_object(obj, name, type_, reflected, compare_to):  # noqa: ARG001
        nonlocal database_signatures
        if (
            type_ == "foreign_key_constraint"
            and reflected
            and (obj.table.name, name) in _INTENTIONAL_DATABASE_ONLY_FOREIGN_KEYS
        ):
            # These cyclic current-version pointers are enforced by MySQL migrations.
            # Keeping them out of ORM metadata lets SQLite fixtures create parents
            # before their immutable version rows without disabling all FK checks.
            return False
        if type_ != "check_constraint":
            return True
        if database_signatures is None:
            inspector = inspect(connection)
            database_signatures = {
                (table_name, _normalize_check_sql(constraint["sqltext"]))
                for table_name in inspector.get_table_names()
                for constraint in inspector.get_check_constraints(table_name)
            }
        signature = (obj.table.name, _normalize_check_sql(obj.sqltext))
        counterpart = metadata_signatures if reflected else database_signatures
        return signature not in counterpart

    return include_object


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_check_constraint_filter(connection),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
