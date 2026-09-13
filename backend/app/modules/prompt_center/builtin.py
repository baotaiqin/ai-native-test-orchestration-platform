"""System prompt catalog installed automatically for every deployment."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.prompt_center.models import OutputSchema, PromptDefinition, PromptVersion


def ensure_builtin_prompts(session: Session) -> dict[str, PromptDefinition]:
    """Install missing built-ins without overwriting administrator customizations."""

    # Imported lazily to keep the existing catalog source compatible while the Demo
    # bootstrap is reduced to consuming, rather than owning, these system defaults.
    from app.modules.demo_bootstrap.service import PROMPTS

    result: dict[str, PromptDefinition] = {}
    for task_type, code, legacy_name, result_type, system_prompt, user_template in PROMPTS:
        name = legacy_name.replace("[内置 Demo]", "[系统默认]")
        schema_name = f"{name} Schema"
        legacy_schema_name = f"{legacy_name} Schema"
        schema = session.scalar(
            select(OutputSchema)
            .where(OutputSchema.name.in_([schema_name, legacy_schema_name]))
            .order_by(OutputSchema.version_no.desc())
        )
        canonical_schema = result_type.model_json_schema()
        if schema is None or schema.schema_json != canonical_schema:
            latest_schema_version = session.scalar(
                select(func.max(OutputSchema.version_no)).where(
                    OutputSchema.name.in_([schema_name, legacy_schema_name])
                )
            )
            schema = OutputSchema(
                name=schema_name,
                version_no=int(latest_schema_version or 0) + 1,
                description="系统内置的结构化输出约束。",
                schema_json=canonical_schema,
                enabled=True,
                created_by="system",
            )
            session.add(schema)
            session.flush()
        else:
            schema.name = schema_name
            schema.enabled = True

        prompt = session.scalar(
            select(PromptDefinition).where(PromptDefinition.code == code)
        )
        if prompt is None:
            prompt = PromptDefinition(
                name=name,
                code=code,
                task_type=task_type.value,
                scope="SYSTEM",
                project_id=None,
                base_prompt_id=None,
                is_builtin=True,
                description="系统默认提示词模板；所有项目可直接继承。",
                enabled=True,
                created_by="system",
            )
            session.add(prompt)
            session.flush()
        else:
            prompt.name = name
            prompt.task_type = task_type.value
            prompt.scope = "SYSTEM"
            prompt.project_id = None
            prompt.base_prompt_id = None
            prompt.is_builtin = True
            prompt.enabled = True
            prompt.description = "系统默认提示词模板；所有项目可直接继承。"

        current = (
            session.get(PromptVersion, prompt.current_version_id)
            if prompt.current_version_id is not None
            else None
        )
        if current is None:
            latest_prompt_version = session.scalar(
                select(func.max(PromptVersion.version_no)).where(
                    PromptVersion.prompt_id == prompt.id
                )
            )
            current = PromptVersion(
                prompt_id=prompt.id,
                version_no=int(latest_prompt_version or 0) + 1,
                system_prompt=system_prompt,
                user_template=user_template,
                output_schema_id=schema.id,
                change_note="同步系统默认提示词",
                created_by="system",
            )
            session.add(current)
            session.flush()
            prompt.current_version_id = current.id
        elif current.created_by == "system" or (
            prompt.is_builtin and current.change_note == "初始版本"
        ):
            # System defaults are a single mutable factory version. Keep their
            # contract synchronized without creating user-visible V2/V3 noise.
            # Older Demo installations created the original built-ins as the
            # bootstrap administrator; "初始版本" identifies that untouched
            # legacy factory content. Explicit administrator edits use a different
            # change note and remain protected from automatic synchronization.
            current.system_prompt = system_prompt
            current.user_template = user_template
            current.output_schema_id = schema.id
            current.change_note = "同步系统默认提示词"
            current.created_by = "system"
        result[task_type.value] = prompt
    session.commit()
    return result


def get_builtin_prompt_factory(code: str) -> tuple[str, str, type] | None:
    """Return the immutable factory content used by the explicit restore action."""

    from app.modules.demo_bootstrap.service import PROMPTS

    for _, item_code, _, result_type, system_prompt, user_template in PROMPTS:
        if item_code == code:
            return system_prompt, user_template, result_type
    return None
