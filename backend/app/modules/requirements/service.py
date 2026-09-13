import difflib
import hashlib
import json

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError, ResourceNotFoundError
from app.modules.auth.schemas import CurrentUser
from app.modules.projects.business_codes import (
    BusinessCodeNamespace,
    next_project_business_code,
)
from app.modules.projects.models import Project
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.requirements.models import (
    Requirement,
    RequirementDocumentVersion,
    RequirementVersion,
)
from app.modules.requirements.parser import parse_markdown_tree
from app.modules.requirements.schemas import (
    MarkdownImportRequest,
    RequirementAutomationReadiness,
    RequirementCreate,
    RequirementDiffResponse,
    RequirementDocumentVersionCreate,
    RequirementResponse,
    RequirementTreeNode,
    RequirementType,
    RequirementVerificationType,
    RequirementVersionCreate,
    RequirementVersionResponse,
    SourceType,
)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _snapshot_hash(snapshot: list[dict]) -> str:
    serialized = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _content_hash(serialized)


def _get_requirement(session: Session, requirement_id: int) -> Requirement:
    requirement = session.get(Requirement, requirement_id)
    if requirement is None:
        raise ResourceNotFoundError("需求不存在")
    return requirement


def _ensure_requirement_access(
    session: Session, user: CurrentUser, requirement: Requirement, *, writable: bool = False
) -> Project:
    project = get_project(session, user, requirement.project_id)
    if writable:
        ensure_project_writable(session, project, user)
        if project.status == ProjectStatus.ARCHIVED.value:
            raise ResourceConflictError("归档项目不能修改需求")
    return project


def _next_order_index(session: Session, project_id: int, parent_id: int | None) -> int:
    statement = select(func.max(Requirement.order_index)).where(
        Requirement.project_id == project_id,
        Requirement.parent_id == parent_id,
    )
    return int(session.scalar(statement) or -1) + 1


def _create_requirement_row(
    session: Session,
    user: CurrentUser,
    *,
    project_id: int,
    parent_id: int | None,
    title: str,
    requirement_type: RequirementType,
    verification_type: RequirementVerificationType = RequirementVerificationType.AUTO,
    automation_readiness: RequirementAutomationReadiness = (
        RequirementAutomationReadiness.READY
    ),
    markdown_content: str,
    source_type: SourceType,
    source_filename: str | None = None,
    order_index: int | None = None,
) -> Requirement:
    requirement = Requirement(
        project_id=project_id,
        parent_id=parent_id,
        code=next_project_business_code(
            session,
            project_id=project_id,
            namespace=BusinessCodeNamespace.REQUIREMENT,
            model=Requirement,
        ),
        title=title.strip(),
        type=requirement_type.value,
        verification_type=verification_type.value,
        automation_readiness=automation_readiness.value,
        order_index=(
            _next_order_index(session, project_id, parent_id)
            if order_index is None
            else order_index
        ),
        status="ACTIVE",
        created_by=user.id,
    )
    session.add(requirement)
    session.flush()
    version = RequirementVersion(
        requirement_id=requirement.id,
        version_no=1,
        markdown_content=markdown_content.strip(),
        content_hash=_content_hash(markdown_content.strip()),
        source_type=source_type.value,
        source_filename=source_filename,
        change_summary="初始版本",
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    requirement.current_version_id = version.id
    return requirement


def create_requirement(
    session: Session, user: CurrentUser, payload: RequirementCreate
) -> Requirement:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能创建需求")
    if payload.parent_id is not None:
        parent = _get_requirement(session, payload.parent_id)
        if parent.project_id != payload.project_id:
            raise ResourceConflictError("父需求与项目不匹配")
    requirement = _create_requirement_row(
        session,
        user,
        project_id=payload.project_id,
        parent_id=payload.parent_id,
        title=payload.title,
        requirement_type=payload.type,
        verification_type=payload.verification_type,
        automation_readiness=payload.automation_readiness,
        markdown_content=payload.markdown_content,
        source_type=SourceType.MANUAL,
    )
    session.commit()
    session.refresh(requirement)
    return requirement


def get_current_version(session: Session, requirement: Requirement) -> RequirementVersion | None:
    if requirement.current_version_id is None:
        return None
    return session.get(RequirementVersion, requirement.current_version_id)


def requirement_response(session: Session, requirement: Requirement) -> RequirementResponse:
    current = get_current_version(session, requirement)
    return RequirementResponse(
        **RequirementResponse.model_validate(requirement).model_dump(exclude={"current_version"}),
        current_version=(RequirementVersionResponse.model_validate(current) if current else None),
    )


def list_requirement_tree(
    session: Session, user: CurrentUser, project_id: int
) -> tuple[list[RequirementTreeNode], int]:
    get_project(session, user, project_id)
    requirements = list(
        session.scalars(
            select(Requirement)
            .where(
                Requirement.project_id == project_id,
                Requirement.status == "ACTIVE",
            )
            .order_by(Requirement.order_index.asc(), Requirement.id.asc())
        ).all()
    )
    node_map: dict[int, RequirementTreeNode] = {}
    roots: list[RequirementTreeNode] = []
    for requirement in requirements:
        response = requirement_response(session, requirement)
        node_map[requirement.id] = RequirementTreeNode(**response.model_dump(), children=[])
    for requirement in requirements:
        node = node_map[requirement.id]
        if requirement.parent_id and requirement.parent_id in node_map:
            node_map[requirement.parent_id].children.append(node)
        else:
            roots.append(node)
    def number_nodes(nodes: list[RequirementTreeNode], prefix: str = "") -> None:
        for index, node in enumerate(nodes, start=1):
            node.outline_number = f"{prefix}.{index}" if prefix else str(index)
            number_nodes(node.children, node.outline_number)

    number_nodes(roots)
    return roots, len(requirements)


def list_document_versions(
    session: Session, user: CurrentUser, project_id: int
) -> list[RequirementDocumentVersion]:
    get_project(session, user, project_id)
    # A few focused unit-test databases intentionally create only the legacy
    # requirement tables. Production databases receive 0060 before this code.
    if not inspect(session.get_bind()).has_table(RequirementDocumentVersion.__tablename__):
        return []
    statement = (
        select(RequirementDocumentVersion)
        .where(RequirementDocumentVersion.project_id == project_id)
        .order_by(RequirementDocumentVersion.version_no.desc())
    )
    return list(session.scalars(statement).all())


def get_current_document_version(
    session: Session, user: CurrentUser, project_id: int
) -> RequirementDocumentVersion | None:
    versions = list_document_versions(session, user, project_id)
    return versions[0] if versions else None


def _snapshot_current_tree(session: Session, project_id: int) -> list[dict]:
    requirements = list(
        session.scalars(
            select(Requirement)
            .where(
                Requirement.project_id == project_id,
                Requirement.status == "ACTIVE",
            )
            .order_by(
                Requirement.parent_id.asc(),
                Requirement.order_index.asc(),
                Requirement.id.asc(),
            )
        ).all()
    )
    by_parent: dict[int | None, list[Requirement]] = {}
    for requirement in requirements:
        by_parent.setdefault(requirement.parent_id, []).append(requirement)
    snapshot: list[dict] = []

    def append_nodes(parent_id: int | None, prefix: str = "") -> None:
        for index, requirement in enumerate(by_parent.get(parent_id, []), start=1):
            outline_number = f"{prefix}.{index}" if prefix else str(index)
            version = get_current_version(session, requirement)
            snapshot.append(
                {
                    "requirement_id": requirement.id,
                    "code": requirement.code,
                    "parent_id": requirement.parent_id,
                    "outline_number": outline_number,
                    "title": requirement.title,
                    "type": requirement.type,
                    "verification_type": requirement.verification_type,
                    "automation_readiness": requirement.automation_readiness,
                    "order_index": requirement.order_index,
                    "markdown_content": version.markdown_content if version else "",
                    "technical_version_id": version.id if version else None,
                    "technical_version_no": version.version_no if version else None,
                }
            )
            append_nodes(requirement.id, outline_number)

    append_nodes(None)
    return snapshot


def _create_document_snapshot(
    session: Session,
    user: CurrentUser,
    project_id: int,
    *,
    source_type: SourceType,
    source_filename: str | None = None,
    change_summary: str | None = None,
    source_review_id: int | None = None,
) -> RequirementDocumentVersion:
    snapshot = _snapshot_current_tree(session, project_id)
    latest_no = session.scalar(
        select(func.max(RequirementDocumentVersion.version_no)).where(
            RequirementDocumentVersion.project_id == project_id
        )
    )
    document_version = RequirementDocumentVersion(
        project_id=project_id,
        version_no=int(latest_no or 0) + 1,
        snapshot=snapshot,
        content_hash=_snapshot_hash(snapshot),
        source_type=source_type.value,
        source_filename=source_filename,
        change_summary=change_summary,
        source_review_id=source_review_id,
        created_by=user.id,
    )
    session.add(document_version)
    session.flush()
    return document_version


def _validate_document_draft(payload: RequirementDocumentVersionCreate) -> None:
    client_ids = [node.client_id for node in payload.nodes]
    if len(client_ids) != len(set(client_ids)):
        raise ResourceConflictError("需求树草稿包含重复节点")
    client_id_set = set(client_ids)
    for node in payload.nodes:
        if node.parent_client_id == node.client_id:
            raise ResourceConflictError("需求不能成为自己的父级")
        if node.parent_client_id and node.parent_client_id not in client_id_set:
            raise ResourceConflictError("需求树草稿包含不存在的父级")

    parent_map = {node.client_id: node.parent_client_id for node in payload.nodes}
    for client_id in client_ids:
        visited: set[str] = set()
        current: str | None = client_id
        while current is not None:
            if current in visited:
                raise ResourceConflictError("需求树草稿存在循环层级")
            visited.add(current)
            current = parent_map.get(current)


def _validate_review_document_scope(
    session: Session,
    payload: RequirementDocumentVersionCreate,
    source_review: object,
    existing_map: dict[int, Requirement],
) -> None:
    """Limit a review-based version to the reviewed requirement subtree.

    Parent and sibling context is sent to the model for understanding only.  It must
    not silently become editable merely because it appeared in the review prompt.
    """
    review_requirement_id = int(source_review.requirement_id)
    context = source_review.context_snapshot
    context = context if isinstance(context, dict) else {}
    descendants = context.get("descendants", [])
    descendant_ids: set[int] = set()
    descendant_codes: set[str] = set()
    if isinstance(descendants, list):
        for item in descendants:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            item_code = item.get("code")
            if isinstance(item_id, int):
                descendant_ids.add(item_id)
            if isinstance(item_code, str):
                descendant_codes.add(item_code)
    editable_ids = {review_requirement_id, *descendant_ids}
    editable_ids.update(
        item.id for item in existing_map.values() if item.code in descendant_codes
    )

    payload_by_client = {node.client_id: node for node in payload.nodes}
    payload_by_requirement = {
        node.requirement_id: node
        for node in payload.nodes
        if node.requirement_id is not None
    }

    def draft_parent_requirement_id(client_id: str | None) -> int | None:
        if client_id is None:
            return None
        parent = payload_by_client[client_id]
        return parent.requirement_id

    for requirement in existing_map.values():
        if requirement.status != "ACTIVE":
            continue
        node = payload_by_requirement.get(requirement.id)
        if requirement.id not in editable_ids:
            if node is None:
                raise ResourceConflictError(
                    "基于评审创建新版时，不能删除评审范围之外的需求"
                )
            current = get_current_version(session, requirement)
            unchanged = (
                node.title.strip() == requirement.title
                and node.type.value == requirement.type
                and node.verification_type.value == requirement.verification_type
                and node.automation_readiness.value == requirement.automation_readiness
                and node.markdown_content.strip()
                == (current.markdown_content.strip() if current else "")
                and draft_parent_requirement_id(node.parent_client_id)
                == requirement.parent_id
            )
            if not unchanged:
                raise ResourceConflictError(
                    "基于评审创建新版时，只能修改评审需求及其下级需求"
                )
        elif node is not None:
            draft_parent_id = draft_parent_requirement_id(node.parent_client_id)
            if draft_parent_id != requirement.parent_id:
                nearest_retained_parent_id = requirement.parent_id
                visited_parent_ids: set[int] = set()
                while (
                    nearest_retained_parent_id in editable_ids
                    and nearest_retained_parent_id not in payload_by_requirement
                    and nearest_retained_parent_id not in visited_parent_ids
                ):
                    visited_parent_ids.add(nearest_retained_parent_id)
                    parent = existing_map.get(nearest_retained_parent_id)
                    nearest_retained_parent_id = parent.parent_id if parent else None
                if draft_parent_id != nearest_retained_parent_id:
                    raise ResourceConflictError(
                        "既有需求只能因删除上级而提升到最近保留的父级"
                    )

    for node in payload.nodes:
        if node.requirement_id is not None:
            continue
        parent_client_id = node.parent_client_id
        visited: set[str] = set()
        while parent_client_id is not None:
            if parent_client_id in visited:
                break
            visited.add(parent_client_id)
            parent = payload_by_client[parent_client_id]
            if parent.requirement_id is not None:
                if parent.requirement_id not in editable_ids:
                    raise ResourceConflictError(
                        "新增需求必须位于本次评审需求或其下级需求之下"
                    )
                break
            parent_client_id = parent.parent_client_id
        else:
            raise ResourceConflictError("基于评审创建新版时，不能新增根需求")

    active_by_parent: dict[int | None, list[Requirement]] = {}
    for requirement in existing_map.values():
        if requirement.status == "ACTIVE":
            active_by_parent.setdefault(requirement.parent_id, []).append(requirement)
    for siblings in active_by_parent.values():
        siblings.sort(key=lambda item: (item.order_index, item.id))
        current_outside = [item.id for item in siblings if item.id not in editable_ids]
        draft_outside = [
            node.requirement_id
            for node in sorted(payload.nodes, key=lambda item: item.order_index)
            if node.requirement_id in current_outside
            and draft_parent_requirement_id(node.parent_client_id) == siblings[0].parent_id
        ]
        if draft_outside != current_outside:
            raise ResourceConflictError("评审范围之外的需求顺序不能调整")

    scope_root = existing_map.get(review_requirement_id)
    scope_root_node = payload_by_requirement.get(review_requirement_id)
    if scope_root is not None and scope_root_node is not None:
        original_siblings = active_by_parent.get(scope_root.parent_id, [])
        original_before = [
            item.id for item in original_siblings
            if item.id not in editable_ids and item.order_index < scope_root.order_index
        ]
        draft_siblings = sorted(
            [
                node for node in payload.nodes
                if draft_parent_requirement_id(node.parent_client_id) == scope_root.parent_id
            ],
            key=lambda item: item.order_index,
        )
        root_index = draft_siblings.index(scope_root_node)
        draft_before = [
            node.requirement_id for node in draft_siblings[:root_index]
            if node.requirement_id not in editable_ids
        ]
        if draft_before != original_before:
            raise ResourceConflictError("被评审需求不能移动到其他同级需求之前或之后")


def publish_document_version(
    session: Session,
    user: CurrentUser,
    project_id: int,
    payload: RequirementDocumentVersionCreate,
) -> tuple[RequirementDocumentVersion, list[RequirementTreeNode], int]:
    project = get_project(session, user, project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能发布需求文档版本")
    _validate_document_draft(payload)

    source_review = None
    if payload.source_review_id is not None:
        from app.modules.requirement_reviews.models import RequirementReview

        source_review = session.get(RequirementReview, payload.source_review_id)
        if (
            source_review is None
            or source_review.project_id != project_id
            or source_review.status != "ACCEPTED"
        ):
            raise ResourceConflictError("只能基于当前项目已确认的评审创建新版本")

    existing = list(
        session.scalars(
            select(Requirement).where(Requirement.project_id == project_id)
        ).all()
    )
    existing_map = {item.id: item for item in existing}
    requested_existing_ids = {
        node.requirement_id for node in payload.nodes if node.requirement_id is not None
    }
    if any(requirement_id not in existing_map for requirement_id in requested_existing_ids):
        raise ResourceConflictError("需求树草稿包含其他项目或不存在的需求")
    if source_review is not None:
        _validate_review_document_scope(session, payload, source_review, existing_map)

    resolved: dict[str, Requirement] = {}
    for node in payload.nodes:
        requirement = existing_map.get(node.requirement_id) if node.requirement_id else None
        if requirement is None:
            requirement = _create_requirement_row(
                session,
                user,
                project_id=project_id,
                parent_id=None,
                title=node.title,
                requirement_type=node.type,
                verification_type=node.verification_type,
                automation_readiness=node.automation_readiness,
                markdown_content=node.markdown_content,
                source_type=SourceType.MANUAL,
                order_index=node.order_index,
            )
        else:
            requirement.title = node.title.strip()
            requirement.type = node.type.value
            requirement.verification_type = node.verification_type.value
            requirement.automation_readiness = node.automation_readiness.value
            requirement.order_index = node.order_index
            requirement.status = "ACTIVE"
            content = node.markdown_content.strip()
            current = get_current_version(session, requirement)
            if current is None or current.content_hash != _content_hash(content):
                latest_no = session.scalar(
                    select(func.max(RequirementVersion.version_no)).where(
                        RequirementVersion.requirement_id == requirement.id
                    )
                )
                version = RequirementVersion(
                    requirement_id=requirement.id,
                    version_no=int(latest_no or 0) + 1,
                    markdown_content=content,
                    content_hash=_content_hash(content),
                    source_type=SourceType.MANUAL.value,
                    change_summary=payload.change_summary or "需求文档版本更新",
                    created_by=user.id,
                )
                session.add(version)
                session.flush()
                requirement.current_version_id = version.id
        resolved[node.client_id] = requirement

    for node in payload.nodes:
        requirement = resolved[node.client_id]
        requirement.parent_id = (
            resolved[node.parent_client_id].id if node.parent_client_id else None
        )
        requirement.order_index = node.order_index

    for requirement in existing:
        if requirement.id not in requested_existing_ids:
            requirement.status = "ARCHIVED"

    session.flush()
    document_version = _create_document_snapshot(
        session,
        user,
        project_id,
        source_type=SourceType.MANUAL,
        change_summary=payload.change_summary or "更新完整需求文档",
        source_review_id=payload.source_review_id,
    )
    session.commit()
    session.refresh(document_version)
    roots, total = list_requirement_tree(session, user, project_id)
    return document_version, roots, total


def get_requirement(
    session: Session, user: CurrentUser, requirement_id: int
) -> RequirementResponse:
    requirement = _get_requirement(session, requirement_id)
    _ensure_requirement_access(session, user, requirement)
    return requirement_response(session, requirement)


def create_requirement_version(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    payload: RequirementVersionCreate,
) -> RequirementResponse:
    requirement = _get_requirement(session, requirement_id)
    _ensure_requirement_access(session, user, requirement, writable=True)
    content = payload.markdown_content.strip()
    current = get_current_version(session, requirement)
    if current and current.content_hash == _content_hash(content):
        raise ResourceConflictError("需求内容未发生变化")
    latest_no = session.scalar(
        select(func.max(RequirementVersion.version_no)).where(
            RequirementVersion.requirement_id == requirement_id
        )
    )
    version = RequirementVersion(
        requirement_id=requirement_id,
        version_no=int(latest_no or 0) + 1,
        markdown_content=content,
        content_hash=_content_hash(content),
        source_type=SourceType.MANUAL.value,
        change_summary=payload.change_summary,
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    requirement.current_version_id = version.id
    if payload.title:
        requirement.title = payload.title.strip()
    session.commit()
    session.refresh(requirement)
    return requirement_response(session, requirement)


def list_versions(
    session: Session, user: CurrentUser, requirement_id: int
) -> list[RequirementVersion]:
    requirement = _get_requirement(session, requirement_id)
    _ensure_requirement_access(session, user, requirement)
    statement = (
        select(RequirementVersion)
        .where(RequirementVersion.requirement_id == requirement_id)
        .order_by(RequirementVersion.version_no.desc())
    )
    return list(session.scalars(statement).all())


def set_current_version(
    session: Session, user: CurrentUser, requirement_id: int, version_id: int
) -> RequirementResponse:
    requirement = _get_requirement(session, requirement_id)
    _ensure_requirement_access(session, user, requirement, writable=True)
    version = session.get(RequirementVersion, version_id)
    if version is None or version.requirement_id != requirement_id:
        raise ResourceNotFoundError("需求版本不存在")
    requirement.current_version_id = version.id
    session.commit()
    session.refresh(requirement)
    return requirement_response(session, requirement)


def calculate_version_diff(
    from_version: RequirementVersion,
    to_version: RequirementVersion,
) -> tuple[list[str], int, int]:
    diff_lines = list(
        difflib.unified_diff(
            from_version.markdown_content.splitlines(),
            to_version.markdown_content.splitlines(),
            fromfile=f"V{from_version.version_no}",
            tofile=f"V{to_version.version_no}",
            lineterm="",
        )
    )
    # difflib emits exactly two file header lines before the first hunk. Remove
    # those by structure/position so body content beginning with ++ or -- is
    # still counted after unified-diff adds its own +/- marker.
    changed_lines = diff_lines[2:] if diff_lines else []
    additions = sum(1 for line in changed_lines if line.startswith("+"))
    deletions = sum(1 for line in changed_lines if line.startswith("-"))
    return diff_lines, additions, deletions


def diff_versions(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    from_version: int,
    to_version: int,
) -> RequirementDiffResponse:
    requirement = _get_requirement(session, requirement_id)
    _ensure_requirement_access(session, user, requirement)
    versions = list(
        session.scalars(
            select(RequirementVersion).where(
                RequirementVersion.requirement_id == requirement_id,
                RequirementVersion.version_no.in_([from_version, to_version]),
            )
        ).all()
    )
    version_map = {version.version_no: version for version in versions}
    if from_version not in version_map or to_version not in version_map:
        raise ResourceNotFoundError("对比版本不存在")
    source_version = version_map[from_version]
    target_version = version_map[to_version]
    diff_lines, additions, deletions = calculate_version_diff(
        source_version,
        target_version,
    )
    return RequirementDiffResponse(
        requirement_id=requirement_id,
        from_version=from_version,
        to_version=to_version,
        content_changed=source_version.markdown_content != target_version.markdown_content,
        unified_diff="\n".join(diff_lines),
        additions=additions,
        deletions=deletions,
    )


def import_markdown(
    session: Session, user: CurrentUser, payload: MarkdownImportRequest
) -> tuple[list[RequirementTreeNode], int, RequirementDocumentVersion]:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能导入需求")
    preview_nodes = parse_markdown_tree(payload.content, payload.filename)
    id_map: dict[str, int] = {}
    for node in preview_nodes:
        parent_id = id_map.get(node.parent_temp_id) if node.parent_temp_id else None
        requirement = _create_requirement_row(
            session,
            user,
            project_id=payload.project_id,
            parent_id=parent_id,
            title=node.title,
            requirement_type=RequirementType.SECTION,
            markdown_content=node.markdown_content,
            source_type=SourceType.MARKDOWN,
            source_filename=payload.filename,
            order_index=node.order_index,
        )
        id_map[node.temp_id] = requirement.id
    document_version = _create_document_snapshot(
        session,
        user,
        payload.project_id,
        source_type=SourceType.MARKDOWN,
        source_filename=payload.filename,
        change_summary="导入需求文档" if not payload.filename else f"导入 {payload.filename}",
    )
    session.commit()
    session.refresh(document_version)
    roots, _ = list_requirement_tree(session, user, payload.project_id)
    return roots, len(preview_nodes), document_version
