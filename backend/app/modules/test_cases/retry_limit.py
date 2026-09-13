from fastapi import status

from app.core.exceptions import AppError

HISTORICAL_API_STEP_MAX_RETRIES = 3
V1_API_STEP_MAX_RETRIES = 1
API_STEP_RETRY_LIMIT_ERROR_CODE = "API_STEP_RETRY_LIMIT_EXCEEDED"
API_STEP_RETRY_LIMIT_ERROR_MESSAGE = (
    "V1 API Step 自动重试最多一次；请将 max_retries 设为 0 或 1，并创建符合 V1 的新版本"
)


def is_v1_api_step_retry_limit(max_retries: int) -> bool:
    return type(max_retries) is int and 0 <= max_retries <= V1_API_STEP_MAX_RETRIES


def ensure_v1_api_step_retry_limit(max_retries: int) -> None:
    if is_v1_api_step_retry_limit(max_retries):
        return
    raise AppError(
        API_STEP_RETRY_LIMIT_ERROR_CODE,
        API_STEP_RETRY_LIMIT_ERROR_MESSAGE,
        status_code=status.HTTP_409_CONFLICT,
        details={
            "maximum": V1_API_STEP_MAX_RETRIES,
            "remediation": "CREATE_NEW_CASE_VERSION",
        },
    )
