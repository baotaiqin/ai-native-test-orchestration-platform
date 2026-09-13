"""Switch API case generation to the compact intent compiler contract."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0066"
down_revision: str | None = "20260912_0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA_NAME = "[系统默认] API 用例生成 Schema"
_COMPILER_INSTRUCTION = (
    "\n\n[平台编译协议] 只输出测试意图；不要输出 request、URL、认证、"
    "Runtime 变量、前置动作、extractor、assertion 或 cleanup DSL。"
    "完整可执行用例由平台根据 OpenAPI 契约编译。"
)


def _tables() -> tuple[sa.Table, sa.Table, sa.Table]:
    metadata = sa.MetaData()
    schemas = sa.Table(
        "output_schemas",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128)),
        sa.Column("version_no", sa.Integer()),
        sa.Column("description", sa.String(length=500)),
        sa.Column("schema_json", sa.JSON()),
        sa.Column("enabled", sa.Boolean()),
        sa.Column("created_by", sa.String(length=64)),
    )
    prompts = sa.Table(
        "prompt_definitions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_type", sa.String(length=64)),
        sa.Column("current_version_id", sa.Integer()),
    )
    versions = sa.Table(
        "prompt_versions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system_prompt", sa.Text()),
        sa.Column("output_schema_id", sa.Integer()),
    )
    return schemas, prompts, versions


def upgrade() -> None:
    from app.modules.test_cases.schemas import ApiCaseIntentResult

    bind = op.get_bind()
    schemas, prompts, versions = _tables()
    latest_version = bind.scalar(
        sa.select(sa.func.max(schemas.c.version_no)).where(schemas.c.name == _SCHEMA_NAME)
    )
    result = bind.execute(
        schemas.insert().values(
            name=_SCHEMA_NAME,
            version_no=int(latest_version or 0) + 1,
            description="平台编译器使用的 API 测试意图结构。",
            schema_json=ApiCaseIntentResult.model_json_schema(),
            enabled=True,
            created_by="system",
        )
    )
    schema_id = result.inserted_primary_key[0]
    current_rows = bind.execute(
        sa.select(versions.c.id, versions.c.system_prompt)
        .select_from(
            prompts.join(versions, prompts.c.current_version_id == versions.c.id)
        )
        .where(prompts.c.task_type == "API_CASE_GENERATE")
    ).all()
    for version_id, system_prompt in current_rows:
        prompt_text = str(system_prompt or "")
        if _COMPILER_INSTRUCTION not in prompt_text:
            prompt_text += _COMPILER_INSTRUCTION
        bind.execute(
            versions.update()
            .where(versions.c.id == version_id)
            .values(output_schema_id=schema_id, system_prompt=prompt_text)
        )


def downgrade() -> None:
    bind = op.get_bind()
    schemas, prompts, versions = _tables()
    intent_schema = bind.execute(
        sa.select(schemas.c.id)
        .where(schemas.c.name == _SCHEMA_NAME)
        .order_by(schemas.c.version_no.desc())
        .limit(1)
    ).scalar_one_or_none()
    legacy_rows = bind.execute(
        sa.select(schemas.c.id, schemas.c.schema_json)
        .where(schemas.c.name == _SCHEMA_NAME)
        .order_by(schemas.c.version_no.desc())
    ).all()
    legacy_schema_id = next(
        (
            schema_id
            for schema_id, schema_json in legacy_rows
            if isinstance(schema_json, dict) and "cases" in schema_json.get("properties", {})
        ),
        None,
    )
    current_ids = bind.scalars(
        sa.select(versions.c.id)
        .select_from(
            prompts.join(versions, prompts.c.current_version_id == versions.c.id)
        )
        .where(prompts.c.task_type == "API_CASE_GENERATE")
    ).all()
    for version_id in current_ids:
        current_prompt = bind.scalar(
            sa.select(versions.c.system_prompt).where(versions.c.id == version_id)
        )
        bind.execute(
            versions.update()
            .where(versions.c.id == version_id)
            .values(
                output_schema_id=legacy_schema_id,
                system_prompt=str(current_prompt or "").replace(_COMPILER_INSTRUCTION, ""),
            )
        )
    if intent_schema is not None:
        bind.execute(schemas.delete().where(schemas.c.id == intent_schema))
