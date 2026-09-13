from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.web_cases.schemas import (
    SessionProfileCreate,
    SessionProfileResponse,
    SessionProfileUpdate,
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
from app.modules.web_cases.service import (
    approve_web_case,
    archive_session_profile,
    archive_web_case,
    archive_web_element,
    archive_web_page,
    create_session_profile,
    create_web_case,
    create_web_case_version,
    create_web_element,
    create_web_element_version,
    create_web_page,
    get_session_profile,
    get_web_case,
    list_session_profiles,
    list_web_case_versions,
    list_web_cases,
    list_web_element_versions,
    list_web_elements,
    list_web_pages,
    restore_session_profile,
    restore_web_case,
    restore_web_element,
    restore_web_page,
    update_session_profile,
    update_web_case,
    update_web_element,
    update_web_page,
)

case_router = APIRouter()
page_router = APIRouter()
element_router = APIRouter()
profile_router = APIRouter()
IdPath = Annotated[int, Path(gt=0)]


@case_router.post("", response_model=WebCaseDetailResponse, status_code=status.HTTP_201_CREATED)
def create_web_case_route(
    payload: WebCaseCreate, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebCaseDetailResponse:
    return create_web_case(session, current_user, payload)


@case_router.patch("/{case_id}", response_model=WebCaseResponse)
def update_web_case_route(
    case_id: IdPath,
    payload: WebCaseUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebCaseResponse:
    return update_web_case(session, current_user, case_id, payload)


@case_router.get("", response_model=WebCaseListResponse)
def list_web_cases_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
    include_archived: bool = False,
) -> WebCaseListResponse:
    return list_web_cases(session, current_user, project_id, include_archived=include_archived)


@case_router.get("/{case_id}", response_model=WebCaseDetailResponse)
def get_web_case_route(
    case_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebCaseDetailResponse:
    return get_web_case(session, current_user, case_id)


@case_router.get("/{case_id}/versions", response_model=list[WebCaseVersionResponse])
def list_web_case_versions_route(
    case_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> list[WebCaseVersionResponse]:
    return list_web_case_versions(session, current_user, case_id)


@case_router.post(
    "/{case_id}/versions",
    response_model=WebCaseVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_web_case_version_route(
    case_id: IdPath,
    payload: WebCaseVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebCaseVersionResponse:
    return create_web_case_version(session, current_user, case_id, payload)


@case_router.post("/{case_id}/approve", response_model=WebCaseResponse)
def approve_web_case_route(
    case_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebCaseResponse:
    return approve_web_case(session, current_user, case_id)


@case_router.post("/{case_id}/archive", response_model=WebCaseResponse)
def archive_web_case_route(
    case_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebCaseResponse:
    return archive_web_case(session, current_user, case_id)


@case_router.post("/{case_id}/restore", response_model=WebCaseResponse)
def restore_web_case_route(
    case_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebCaseResponse:
    return restore_web_case(session, current_user, case_id)


@page_router.post("", response_model=WebPageResponse, status_code=status.HTTP_201_CREATED)
def create_web_page_route(
    payload: WebPageCreate, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebPageResponse:
    return create_web_page(session, current_user, payload)


@page_router.patch("/{page_id}", response_model=WebPageResponse)
def update_web_page_route(
    page_id: IdPath,
    payload: WebPageUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebPageResponse:
    return update_web_page(session, current_user, page_id, payload)


@page_router.get("", response_model=list[WebPageResponse])
def list_web_pages_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> list[WebPageResponse]:
    return list_web_pages(session, current_user, project_id)


@page_router.post("/{page_id}/archive", response_model=WebPageResponse)
def archive_web_page_route(
    page_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebPageResponse:
    return archive_web_page(session, current_user, page_id)


@page_router.post("/{page_id}/restore", response_model=WebPageResponse)
def restore_web_page_route(
    page_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebPageResponse:
    return restore_web_page(session, current_user, page_id)


@element_router.post("", response_model=WebElementResponse, status_code=status.HTTP_201_CREATED)
def create_web_element_route(
    payload: WebElementCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebElementResponse:
    return create_web_element(session, current_user, payload)


@element_router.patch("/{element_id}", response_model=WebElementResponse)
def update_web_element_route(
    element_id: IdPath,
    payload: WebElementUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebElementResponse:
    return update_web_element(session, current_user, element_id, payload)


@element_router.get("", response_model=list[WebElementResponse])
def list_web_elements_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    page_id: int = Query(gt=0),
) -> list[WebElementResponse]:
    return list_web_elements(session, current_user, page_id)


@element_router.post(
    "/{element_id}/versions",
    response_model=WebElementVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_web_element_version_route(
    element_id: IdPath,
    payload: WebElementVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebElementVersionResponse:
    return create_web_element_version(session, current_user, element_id, payload)


@element_router.get("/{element_id}/versions", response_model=list[WebElementVersionResponse])
def list_web_element_versions_route(
    element_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> list[WebElementVersionResponse]:
    return list_web_element_versions(session, current_user, element_id)


@element_router.post("/{element_id}/archive", response_model=WebElementResponse)
def archive_web_element_route(
    element_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebElementResponse:
    return archive_web_element(session, current_user, element_id)


@element_router.post("/{element_id}/restore", response_model=WebElementResponse)
def restore_web_element_route(
    element_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> WebElementResponse:
    return restore_web_element(session, current_user, element_id)


@profile_router.post("", response_model=SessionProfileResponse, status_code=status.HTTP_201_CREATED)
def create_session_profile_route(
    payload: SessionProfileCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> SessionProfileResponse:
    return create_session_profile(session, current_user, payload)


@profile_router.patch("/{profile_id}", response_model=SessionProfileResponse)
def update_session_profile_route(
    profile_id: IdPath,
    payload: SessionProfileUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> SessionProfileResponse:
    return update_session_profile(session, current_user, profile_id, payload)


@profile_router.get("", response_model=list[SessionProfileResponse])
def list_session_profiles_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> list[SessionProfileResponse]:
    return list_session_profiles(session, current_user, project_id)


@profile_router.get("/{profile_id}", response_model=SessionProfileResponse)
def get_session_profile_route(
    profile_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> SessionProfileResponse:
    return get_session_profile(session, current_user, profile_id)


@profile_router.post("/{profile_id}/archive", response_model=SessionProfileResponse)
def archive_session_profile_route(
    profile_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> SessionProfileResponse:
    return archive_session_profile(session, current_user, profile_id)


@profile_router.post("/{profile_id}/restore", response_model=SessionProfileResponse)
def restore_session_profile_route(
    profile_id: IdPath, session: DatabaseSessionDependency, current_user: CurrentUserDependency
) -> SessionProfileResponse:
    return restore_session_profile(session, current_user, profile_id)
