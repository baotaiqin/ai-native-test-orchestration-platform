import hashlib
import json
import re

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ResourceConflictError, ResourceNotFoundError
from app.core.secret_cipher import encrypt_secret
from app.core.time import to_utc_aware, utc_now_naive
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment
from app.modules.projects.business_codes import (
    BusinessCodeNamespace,
    next_project_business_code,
)
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.secrets.models import Secret
from app.modules.web_cases.models import (
    SessionProfile,
    WebCase,
    WebCaseVersion,
    WebElement,
    WebElementLocator,
    WebElementVersion,
    WebPage,
)
from app.modules.web_cases.schemas import (
    SessionProfileCreate,
    SessionProfileResponse,
    SessionProfileUpdate,
    WebCaseContent,
    WebCaseCreate,
    WebCaseDetailResponse,
    WebCaseListResponse,
    WebCaseResponse,
    WebCaseUpdate,
    WebCaseVersionCreate,
    WebCaseVersionResponse,
    WebElementCreate,
    WebElementResponse,
    WebElementUpdate,
    WebElementVersionCreate,
    WebElementVersionResponse,
    WebPageCreate,
    WebPageResponse,
    WebPageUpdate,
)


def _commit(session: Session, message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError(message) from exc


def _ensure_active_project(project: object) -> None:
    if getattr(project, "status", None) == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能修改 Web 资产")


def _get_case(
    session: Session, user: CurrentUser, case_id: int, *, writable: bool = False
) -> WebCase:
    case = session.get(WebCase, case_id)
    if case is None:
        raise ResourceNotFoundError("Web Case 不存在")
    project = get_project(session, user, case.project_id)
    if writable:
        ensure_project_writable(session, project, user)
        _ensure_active_project(project)
    return case


def _validate_content(value: WebCaseContent | dict) -> WebCaseContent:
    try:
        return value if isinstance(value, WebCaseContent) else WebCaseContent.model_validate(value)
    except ValidationError as exc:
        raise ResourceConflictError("Web Case Version 内容不符合当前安全 DSL") from exc


_MANAGED_SECRET_REFERENCE = re.compile(
    r"^\{\{secret\.([A-Za-z_][A-Za-z0-9_.-]*)\}\}$"
)


def web_case_managed_secret_names(content: WebCaseContent | dict) -> set[str]:
    canonical = _validate_content(content)
    names: set[str] = set()
    for action in canonical.actions:
        value = getattr(action, "value", None)
        match = _MANAGED_SECRET_REFERENCE.fullmatch(value) if isinstance(value, str) else None
        if match:
            names.add(match.group(1))
    proxy = canonical.browser_config.proxy
    if proxy is not None:
        for value in (proxy.username, proxy.password):
            match = (
                _MANAGED_SECRET_REFERENCE.fullmatch(value)
                if isinstance(value, str)
                else None
            )
            if match:
                names.add(match.group(1))
    return names


def validate_web_case_managed_secrets(
    session: Session, project_id: int, content: WebCaseContent | dict
) -> set[str]:
    names = web_case_managed_secret_names(content)
    if not names:
        return names
    available = set(
        session.scalars(
            select(Secret.name).where(
                Secret.project_id == project_id,
                Secret.name.in_(names),
                Secret.enabled.is_(True),
            )
        ).all()
    )
    missing = sorted(names - available)
    if missing:
        raise ResourceConflictError(
            "Web Case 引用了未配置或已停用的 Secret：" + "、".join(missing)
        )
    return names


def _validate_element_refs(session: Session, project_id: int, content: WebCaseContent) -> None:
    for action in content.actions:
        locator = getattr(action, "locator", None)
        if locator is None or locator.element_version_id is None:
            continue
        version = session.get(WebElementVersion, locator.element_version_id)
        if version is None:
            raise ResourceConflictError("Web Case 引用的 Element Version 不存在")
        element = session.get(WebElement, version.element_id)
        page = session.get(WebPage, element.page_id) if element else None
        if (
            element is None
            or page is None
            or element.project_id != project_id
            or page.project_id != project_id
            or element.status != "ACTIVE"
            or page.status != "ACTIVE"
            or not version.locators
        ):
            raise ResourceConflictError("Web Case 引用的 Element Version 不属于当前项目或已归档")
    for assertion in content.assertions:
        locator = getattr(assertion, "locator", None)
        if locator is None or locator.element_version_id is None:
            continue
        version = session.get(WebElementVersion, locator.element_version_id)
        element = session.get(WebElement, version.element_id) if version else None
        page = session.get(WebPage, element.page_id) if element else None
        if (
            version is None
            or element is None
            or page is None
            or element.project_id != project_id
            or page.project_id != project_id
            or element.status != "ACTIVE"
            or page.status != "ACTIVE"
            or not version.locators
        ):
            raise ResourceConflictError("Web Case 引用的 Element Version 不属于当前项目或已归档")


def _validate_session_recovery_binding(
    session: Session,
    project_id: int,
    login_web_case_id: int | None,
    login_web_case_version_id: int | None,
) -> None:
    if login_web_case_id is None and login_web_case_version_id is None:
        return
    login_case = session.get(WebCase, login_web_case_id)
    login_version = session.get(WebCaseVersion, login_web_case_version_id)
    if (
        login_case is None
        or login_case.project_id != project_id
        or login_case.status == "ARCHIVED"
        or login_version is None
        or login_version.web_case_id != login_case.id
        or login_version.status != "APPROVED"
    ):
        raise ResourceConflictError("Session 恢复登录版本不存在、未批准或归属不匹配")
    login_content = _validate_content(login_version.content)
    if login_content.session_profile_id is not None:
        raise ResourceConflictError("Session 恢复登录版本不能再引用 Session Profile")
    _validate_element_refs(session, project_id, login_content)


def _validate_case_references(
    session: Session, case: WebCase, version: WebCaseVersion
) -> WebCaseContent:
    content = _validate_content(version.content)
    _validate_element_refs(session, case.project_id, content)
    if content.session_profile_id is not None:
        profile = session.get(SessionProfile, content.session_profile_id)
        if profile is None or profile.project_id != case.project_id or profile.status != "ACTIVE":
            raise ResourceConflictError(
                "Web Case 引用的 Session Profile 不存在、不属于当前项目或已归档"
            )
        _validate_session_recovery_binding(
            session,
            case.project_id,
            profile.login_web_case_id,
            profile.login_web_case_version_id,
        )
        if (
            profile.expires_at is not None
            and profile.expires_at <= utc_now_naive()
            and profile.login_web_case_version_id is None
        ):
            raise ResourceConflictError("Web Case 引用的 Session Profile 已过期")
    return content


def _case_response(case: WebCase) -> WebCaseResponse:
    return WebCaseResponse.model_validate(case)


def _version_response(version: WebCaseVersion) -> WebCaseVersionResponse:
    return WebCaseVersionResponse(
        id=version.id,
        web_case_id=version.web_case_id,
        version_no=version.version_no,
        content=_validate_content(version.content),
        change_note=version.change_note,
        status=version.status,
        approved_by=version.approved_by,
        approved_at=version.approved_at,
        created_by=version.created_by,
        created_at=version.created_at,
    )


def _detail(session: Session, case: WebCase) -> WebCaseDetailResponse:
    version = (
        session.get(WebCaseVersion, case.current_version_id) if case.current_version_id else None
    )
    return WebCaseDetailResponse(
        **_case_response(case).model_dump(),
        current_version=_version_response(version) if version else None,
    )


def create_web_case(
    session: Session,
    user: CurrentUser,
    payload: WebCaseCreate,
    *,
    commit: bool = True,
) -> WebCaseDetailResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    _ensure_active_project(project)
    _validate_content(payload.content)
    _validate_element_refs(session, payload.project_id, payload.content)
    case = WebCase(
        project_id=payload.project_id,
        code=next_project_business_code(
            session,
            project_id=payload.project_id,
            namespace=BusinessCodeNamespace.WEB_CASE,
            model=WebCase,
        ),
        name=payload.name.strip(),
        status="DRAFT",
        created_by=user.id,
    )
    session.add(case)
    session.flush()
    version = WebCaseVersion(
        web_case_id=case.id,
        version_no=1,
        content=payload.content.model_dump(mode="json"),
        change_note=payload.change_note,
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    case.current_version_id = version.id
    if commit:
        _commit(session, "Web Case 创建冲突")
    else:
        session.flush()
    session.refresh(case)
    return _detail(session, case)


def update_web_case(
    session: Session, user: CurrentUser, case_id: int, payload: WebCaseUpdate
) -> WebCaseResponse:
    case = _get_case(session, user, case_id, writable=True)
    if payload.name is not None:
        case.name = payload.name.strip()
    _commit(session, "Web Case 更新冲突")
    session.refresh(case)
    return _case_response(case)


def list_web_cases(
    session: Session, user: CurrentUser, project_id: int, *, include_archived: bool = False
) -> WebCaseListResponse:
    get_project(session, user, project_id)
    statement = select(WebCase).where(WebCase.project_id == project_id)
    if not include_archived:
        statement = statement.where(WebCase.status != "ARCHIVED")
    cases = list(
        session.scalars(statement.order_by(WebCase.updated_at.desc(), WebCase.id.desc())).all()
    )
    return WebCaseListResponse(items=[_case_response(item) for item in cases], total=len(cases))


def get_web_case(session: Session, user: CurrentUser, case_id: int) -> WebCaseDetailResponse:
    return _detail(session, _get_case(session, user, case_id))


def list_web_case_versions(
    session: Session, user: CurrentUser, case_id: int
) -> list[WebCaseVersionResponse]:
    case = _get_case(session, user, case_id)
    versions = list(
        session.scalars(
            select(WebCaseVersion)
            .where(WebCaseVersion.web_case_id == case.id)
            .order_by(WebCaseVersion.version_no.desc())
        ).all()
    )
    return [_version_response(item) for item in versions]


def create_web_case_version(
    session: Session,
    user: CurrentUser,
    case_id: int,
    payload: WebCaseVersionCreate,
    *,
    commit: bool = True,
) -> WebCaseVersionResponse:
    case = _get_case(session, user, case_id, writable=True)
    if case.status == "ARCHIVED":
        raise ResourceConflictError("归档 Web Case 不能创建新版本")
    _validate_content(payload.content)
    _validate_element_refs(session, case.project_id, payload.content)
    latest = (
        session.scalar(
            select(func.max(WebCaseVersion.version_no)).where(WebCaseVersion.web_case_id == case.id)
        )
        or 0
    )
    version = WebCaseVersion(
        web_case_id=case.id,
        version_no=latest + 1,
        content=payload.content.model_dump(mode="json"),
        change_note=payload.change_note,
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    case.current_version_id = version.id
    if case.status == "APPROVED":
        case.status = "DRAFT"
    if commit:
        _commit(session, "Web Case Version 创建冲突")
    else:
        session.flush()
    session.refresh(version)
    return _version_response(version)


def approve_web_case(session: Session, user: CurrentUser, case_id: int) -> WebCaseResponse:
    case = _get_case(session, user, case_id, writable=True)
    locked = session.scalar(select(WebCase).where(WebCase.id == case.id).with_for_update())
    if locked is None:
        raise ResourceNotFoundError("Web Case 不存在")
    version = (
        session.get(WebCaseVersion, locked.current_version_id)
        if locked.current_version_id
        else None
    )
    if version is None:
        raise ResourceConflictError("Web Case 必须存在当前版本才能批准")
    content = _validate_case_references(session, locked, version)
    validate_web_case_managed_secrets(session, locked.project_id, content)
    if locked.status == "ARCHIVED":
        raise ResourceConflictError("归档 Web Case 不能批准")
    if version.status != "APPROVED":
        version.status = "APPROVED"
        version.approved_by = user.id
        version.approved_at = utc_now_naive()
    locked.status = "APPROVED"
    _commit(session, "Web Case 批准冲突")
    session.refresh(locked)
    return _case_response(locked)


def archive_web_case(session: Session, user: CurrentUser, case_id: int) -> WebCaseResponse:
    case = _get_case(session, user, case_id, writable=True)
    if case.status == "ARCHIVED":
        return _case_response(case)
    case.status = "ARCHIVED"
    _commit(session, "Web Case 归档冲突")
    return _case_response(case)


def restore_web_case(session: Session, user: CurrentUser, case_id: int) -> WebCaseResponse:
    case = _get_case(session, user, case_id, writable=True)
    case.status = "DRAFT"
    _commit(session, "Web Case 恢复冲突")
    return _case_response(case)


def _page(session: Session, user: CurrentUser, page_id: int, *, writable: bool = False) -> WebPage:
    page = session.get(WebPage, page_id)
    if page is None:
        raise ResourceNotFoundError("Web Page 不存在")
    project = get_project(session, user, page.project_id)
    if writable:
        ensure_project_writable(session, project, user)
        _ensure_active_project(project)
    return page


def create_web_page(session: Session, user: CurrentUser, payload: WebPageCreate) -> WebPageResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    _ensure_active_project(project)
    page = WebPage(**payload.model_dump(), created_by=user.id)
    session.add(page)
    _commit(session, "同一项目中的 Web Page code 不能重复")
    session.refresh(page)
    return WebPageResponse.model_validate(page)


def update_web_page(
    session: Session, user: CurrentUser, page_id: int, payload: WebPageUpdate
) -> WebPageResponse:
    page = _page(session, user, page_id, writable=True)
    changes = payload.model_dump(exclude_unset=True)
    for field in ("name", "url_pattern", "description"):
        if field in changes:
            value = changes[field]
            if field == "name" and value is not None:
                value = value.strip()
            setattr(page, field, value)
    _commit(session, "Web Page 更新冲突")
    session.refresh(page)
    return WebPageResponse.model_validate(page)


def list_web_pages(session: Session, user: CurrentUser, project_id: int) -> list[WebPageResponse]:
    get_project(session, user, project_id)
    pages = session.scalars(
        select(WebPage).where(WebPage.project_id == project_id).order_by(WebPage.id.desc())
    ).all()
    return [WebPageResponse.model_validate(item) for item in pages]


def archive_web_page(session: Session, user: CurrentUser, page_id: int) -> WebPageResponse:
    page = _page(session, user, page_id, writable=True)
    page.status = "ARCHIVED"
    _commit(session, "Web Page 归档冲突")
    return WebPageResponse.model_validate(page)


def restore_web_page(session: Session, user: CurrentUser, page_id: int) -> WebPageResponse:
    page = _page(session, user, page_id, writable=True)
    page.status = "ACTIVE"
    _commit(session, "Web Page 恢复冲突")
    return WebPageResponse.model_validate(page)


def _element(
    session: Session, user: CurrentUser, element_id: int, *, writable: bool = False
) -> WebElement:
    element = session.get(WebElement, element_id)
    if element is None:
        raise ResourceNotFoundError("Web Element 不存在")
    project = get_project(session, user, element.project_id)
    if writable:
        ensure_project_writable(session, project, user)
        _ensure_active_project(project)
    return element


def create_web_element(
    session: Session, user: CurrentUser, payload: WebElementCreate
) -> WebElementResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    _ensure_active_project(project)
    page = session.get(WebPage, payload.page_id)
    if page is None or page.project_id != payload.project_id or page.status != "ACTIVE":
        raise ResourceConflictError("Web Element 的 Page 不存在、不属于当前项目或已归档")
    element = WebElement(**payload.model_dump(), created_by=user.id)
    session.add(element)
    _commit(session, "同一 Page 中的 Web Element 名称不能重复")
    session.refresh(element)
    return WebElementResponse.model_validate(element)


def update_web_element(
    session: Session, user: CurrentUser, element_id: int, payload: WebElementUpdate
) -> WebElementResponse:
    element = _element(session, user, element_id, writable=True)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field == "name" and value is not None:
            value = value.strip()
        setattr(element, field, value)
    _commit(session, "Web Element 更新冲突")
    session.refresh(element)
    return WebElementResponse.model_validate(element)


def list_web_elements(
    session: Session, user: CurrentUser, page_id: int
) -> list[WebElementResponse]:
    page = _page(session, user, page_id)
    elements = session.scalars(
        select(WebElement).where(WebElement.page_id == page.id).order_by(WebElement.id.desc())
    ).all()
    return [WebElementResponse.model_validate(item) for item in elements]


def create_web_element_version(
    session: Session, user: CurrentUser, element_id: int, payload: WebElementVersionCreate
) -> WebElementVersionResponse:
    element = _element(session, user, element_id, writable=True)
    latest = (
        session.scalar(
            select(func.max(WebElementVersion.version_no)).where(
                WebElementVersion.element_id == element.id
            )
        )
        or 0
    )
    version = WebElementVersion(
        element_id=element.id,
        version_no=latest + 1,
        description=payload.description,
        element_type=payload.element_type,
        created_by=user.id,
    )
    version.locators = [WebElementLocator(**item.model_dump()) for item in payload.locators]
    session.add(version)
    session.flush()
    element.current_version_id = version.id
    _commit(session, "Web Element Version 或 Locator priority 冲突")
    session.refresh(version)
    return WebElementVersionResponse.model_validate(version)


def list_web_element_versions(
    session: Session, user: CurrentUser, element_id: int
) -> list[WebElementVersionResponse]:
    element = _element(session, user, element_id)
    versions = session.scalars(
        select(WebElementVersion)
        .where(WebElementVersion.element_id == element.id)
        .order_by(WebElementVersion.version_no.desc())
    ).all()
    return [WebElementVersionResponse.model_validate(item) for item in versions]


def archive_web_element(session: Session, user: CurrentUser, element_id: int) -> WebElementResponse:
    element = _element(session, user, element_id, writable=True)
    element.status = "ARCHIVED"
    _commit(session, "Web Element 归档冲突")
    return WebElementResponse.model_validate(element)


def restore_web_element(session: Session, user: CurrentUser, element_id: int) -> WebElementResponse:
    element = _element(session, user, element_id, writable=True)
    element.status = "ACTIVE"
    _commit(session, "Web Element 恢复冲突")
    return WebElementResponse.model_validate(element)


def create_session_profile(
    session: Session, user: CurrentUser, payload: SessionProfileCreate
) -> SessionProfileResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    _ensure_active_project(project)
    if payload.environment_id is not None:
        environment = session.get(Environment, payload.environment_id)
        if (
            environment is None
            or environment.project_id != payload.project_id
            or not environment.enabled
        ):
            raise ResourceConflictError(
                "Session Profile 的 Environment 不存在、不属于当前项目或已停用"
            )
    _validate_session_recovery_binding(
        session,
        payload.project_id,
        payload.login_web_case_id,
        payload.login_web_case_version_id,
    )
    expires_at = to_utc_aware(payload.expires_at)
    if expires_at is not None and expires_at.replace(tzinfo=None) <= utc_now_naive():
        raise ResourceConflictError("Session Profile expires_at 必须晚于当前时间")
    encoded = json.dumps(
        payload.storage_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    profile = SessionProfile(
        project_id=payload.project_id,
        environment_id=payload.environment_id,
        name=payload.name.strip(),
        status="ACTIVE",
        storage_state_ciphertext=encrypt_secret(encoded, get_settings().secret_key),
        storage_state_fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        revision=1,
        refresh_ttl_seconds=payload.refresh_ttl_seconds,
        profile_metadata=payload.metadata or None,
        expires_at=expires_at.replace(tzinfo=None) if expires_at is not None else None,
        login_web_case_id=payload.login_web_case_id,
        login_web_case_version_id=payload.login_web_case_version_id,
        expiry_condition=(
            payload.expiry_condition.model_dump(mode="json")
            if payload.expiry_condition is not None
            else None
        ),
        success_condition=(
            payload.success_condition.model_dump(mode="json")
            if payload.success_condition is not None
            else None
        ),
        created_by=user.id,
    )
    session.add(profile)
    _commit(session, "同一项目中的 Session Profile 名称不能重复")
    session.refresh(profile)
    return SessionProfileResponse.model_validate(profile)


def update_session_profile(
    session: Session, user: CurrentUser, profile_id: int, payload: SessionProfileUpdate
) -> SessionProfileResponse:
    profile = session.get(SessionProfile, profile_id)
    if profile is None:
        raise ResourceNotFoundError("Session Profile 不存在")
    project = get_project(session, user, profile.project_id)
    ensure_project_writable(session, project, user)
    _ensure_active_project(project)
    changes = payload.model_dump(exclude_unset=True)
    recovery_fields = {
        "login_web_case_id",
        "login_web_case_version_id",
        "expiry_condition",
        "success_condition",
    }
    if recovery_fields & changes.keys():
        _validate_session_recovery_binding(
            session,
            profile.project_id,
            changes["login_web_case_id"],
            changes["login_web_case_version_id"],
        )
    if "name" in changes:
        profile.name = changes["name"].strip()
    if "storage_state" in changes:
        encoded = json.dumps(
            changes["storage_state"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        profile.storage_state_ciphertext = encrypt_secret(encoded, get_settings().secret_key)
        profile.storage_state_fingerprint = hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()
    if "metadata" in changes:
        profile.profile_metadata = changes["metadata"] or None
    if "expires_at" in changes:
        expires_at = to_utc_aware(changes["expires_at"])
        if expires_at is not None and expires_at.replace(tzinfo=None) <= utc_now_naive():
            raise ResourceConflictError("Session Profile expires_at 必须晚于当前时间")
        profile.expires_at = expires_at.replace(tzinfo=None) if expires_at is not None else None
    if "refresh_ttl_seconds" in changes:
        profile.refresh_ttl_seconds = changes["refresh_ttl_seconds"]
    if "status" in changes:
        profile.status = changes["status"]
    if recovery_fields & changes.keys():
        profile.login_web_case_id = changes["login_web_case_id"]
        profile.login_web_case_version_id = changes["login_web_case_version_id"]
        profile.expiry_condition = changes["expiry_condition"]
        profile.success_condition = changes["success_condition"]
    profile.revision += 1
    _commit(session, "Session Profile 更新冲突")
    session.refresh(profile)
    return SessionProfileResponse.model_validate(profile)


def list_session_profiles(
    session: Session, user: CurrentUser, project_id: int
) -> list[SessionProfileResponse]:
    get_project(session, user, project_id)
    profiles = session.scalars(
        select(SessionProfile)
        .where(SessionProfile.project_id == project_id)
        .order_by(SessionProfile.id.desc())
    ).all()
    return [SessionProfileResponse.model_validate(item) for item in profiles]


def get_session_profile(
    session: Session, user: CurrentUser, profile_id: int
) -> SessionProfileResponse:
    profile = session.get(SessionProfile, profile_id)
    if profile is None:
        raise ResourceNotFoundError("Session Profile 不存在")
    get_project(session, user, profile.project_id)
    return SessionProfileResponse.model_validate(profile)


def archive_session_profile(
    session: Session, user: CurrentUser, profile_id: int
) -> SessionProfileResponse:
    profile = session.get(SessionProfile, profile_id)
    if profile is None:
        raise ResourceNotFoundError("Session Profile 不存在")
    project = get_project(session, user, profile.project_id)
    ensure_project_writable(session, project, user)
    _ensure_active_project(project)
    profile.status = "ARCHIVED"
    _commit(session, "Session Profile 归档冲突")
    return SessionProfileResponse.model_validate(profile)


def restore_session_profile(
    session: Session, user: CurrentUser, profile_id: int
) -> SessionProfileResponse:
    profile = session.get(SessionProfile, profile_id)
    if profile is None:
        raise ResourceNotFoundError("Session Profile 不存在")
    project = get_project(session, user, profile.project_id)
    ensure_project_writable(session, project, user)
    _ensure_active_project(project)
    profile.status = "ACTIVE"
    _commit(session, "Session Profile 恢复冲突")
    return SessionProfileResponse.model_validate(profile)
