from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.core.exceptions import InvalidDocumentError
from app.modules.datasets.parsers import MAX_FILE_BYTES, MAX_XLSX_FILE_BYTES
from app.modules.datasets.schemas import (
    DatasetCreate,
    DatasetDetailResponse,
    DatasetFakerPreviewRequest,
    DatasetIterationPreviewRequest,
    DatasetIterationPreviewResponse,
    DatasetListResponse,
    DatasetMySQLPreviewRequest,
    DatasetPreviewResponse,
    DatasetResponse,
    DatasetSheetListResponse,
    DatasetVersionCreate,
    DatasetVersionResponse,
)
from app.modules.datasets.service import (
    archive_dataset,
    create_dataset,
    get_dataset,
    list_dataset_versions,
    list_datasets,
    preview_csv,
    preview_faker,
    preview_iterations,
    preview_mysql,
    preview_xlsx,
    preview_xlsx_sheet_names,
)

router = APIRouter()


async def _read_upload_limited(file: UploadFile, maximum: int, label: str) -> bytes:
    """Read at most max+1 bytes so oversized uploads never enter memory unbounded."""
    raw = await file.read(maximum + 1)
    if len(raw) > maximum:
        raise InvalidDocumentError(f"{label}文件不能超过 {maximum // 1024 // 1024} MB")
    return raw


@router.get("", response_model=DatasetListResponse)
def list_datasets_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> DatasetListResponse:
    return list_datasets(session, current_user, project_id)


@router.post("", response_model=DatasetDetailResponse, status_code=status.HTTP_201_CREATED)
def create_dataset_route(
    payload: DatasetCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DatasetDetailResponse:
    return create_dataset(session, current_user, payload)


@router.post("/preview/csv", response_model=DatasetPreviewResponse)
async def preview_csv_route(
    file: Annotated[UploadFile, File(...)],
    current_user: CurrentUserDependency,
    delimiter: Annotated[str, Form()] = ",",
) -> DatasetPreviewResponse:
    _ = current_user
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise InvalidDocumentError("CSV 导入仅支持 .csv 文件")
    raw = await _read_upload_limited(file, MAX_FILE_BYTES, "CSV ")
    result = preview_csv(raw, delimiter)
    result.source_metadata.update({"filename": file.filename, "size_bytes": len(raw)})
    return result


@router.post("/preview/excel", response_model=DatasetPreviewResponse)
async def preview_excel_route(
    file: Annotated[UploadFile, File(...)],
    current_user: CurrentUserDependency,
    sheet_name: Annotated[str | None, Form()] = None,
    header_row: Annotated[int, Form()] = 1,
) -> DatasetPreviewResponse:
    _ = current_user
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise InvalidDocumentError("Excel V1 仅支持 .xlsx 文件，不支持旧 .xls")
    raw = await _read_upload_limited(file, MAX_XLSX_FILE_BYTES, "XLSX ")
    result = preview_xlsx(raw, sheet_name or None, header_row)
    result.source_metadata.update({"filename": file.filename, "size_bytes": len(raw)})
    return result


@router.post("/preview/excel/sheets", response_model=DatasetSheetListResponse)
async def preview_excel_sheets_route(
    file: Annotated[UploadFile, File(...)],
    current_user: CurrentUserDependency,
) -> DatasetSheetListResponse:
    _ = current_user
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise InvalidDocumentError("Excel V1 仅支持 .xlsx 文件，不支持旧 .xls")
    raw = await _read_upload_limited(file, MAX_XLSX_FILE_BYTES, "XLSX ")
    return DatasetSheetListResponse(items=preview_xlsx_sheet_names(raw))


@router.post("/preview/faker", response_model=DatasetPreviewResponse)
def preview_faker_route(
    payload: DatasetFakerPreviewRequest,
    current_user: CurrentUserDependency,
) -> DatasetPreviewResponse:
    _ = current_user
    return preview_faker(payload)


@router.post("/preview/mysql", response_model=DatasetPreviewResponse)
def preview_mysql_route(
    payload: DatasetMySQLPreviewRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DatasetPreviewResponse:
    return preview_mysql(session, current_user, payload)


@router.get("/{dataset_id}", response_model=DatasetDetailResponse)
def get_dataset_route(
    dataset_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DatasetDetailResponse:
    return get_dataset(session, current_user, dataset_id)


@router.post(
    "/{dataset_id}/versions",
    response_model=DatasetVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_dataset_version_route(
    dataset_id: int,
    payload: DatasetVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DatasetVersionResponse:
    from app.modules.datasets.service import create_dataset_version

    return create_dataset_version(session, current_user, dataset_id, payload)


@router.get("/{dataset_id}/versions", response_model=list[DatasetVersionResponse])
def list_dataset_versions_route(
    dataset_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[DatasetVersionResponse]:
    return list_dataset_versions(session, current_user, dataset_id)


@router.post("/{dataset_id}/archive", response_model=DatasetResponse)
def archive_dataset_route(
    dataset_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DatasetResponse:
    return archive_dataset(session, current_user, dataset_id)


@router.post("/iterations/preview", response_model=DatasetIterationPreviewResponse)
def preview_iterations_route(
    payload: DatasetIterationPreviewRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DatasetIterationPreviewResponse:
    return preview_iterations(session, current_user, payload)
