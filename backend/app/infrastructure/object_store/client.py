from contextlib import suppress
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import get_settings


class ObjectStoreUnavailableError(RuntimeError):
    """Raised when the object-storage boundary cannot complete an operation."""


class ObjectStoreNotFoundError(RuntimeError):
    """Raised when a referenced object is missing from object storage."""


class ObjectStore(Protocol):
    def put_object(
        self, *, bucket: str, key: str, content: bytes, content_type: str
    ) -> None:
        """Store one object under a caller-provided safe key."""

    def get_object(self, *, bucket: str, key: str) -> bytes:
        """Read one object under a caller-provided safe key."""

    def delete_object(self, *, bucket: str, key: str) -> None:
        """Delete one object; a missing object is treated as already deleted."""


class MinioObjectStore:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        secure: bool,
    ) -> None:
        try:
            from minio import Minio
        except ImportError as exc:
            raise ObjectStoreUnavailableError from exc
        self.client: Any = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def put_object(
        self, *, bucket: str, key: str, content: bytes, content_type: str
    ) -> None:
        import io

        from minio.error import MinioException, S3Error

        try:
            if not self.client.bucket_exists(bucket):
                try:
                    self.client.make_bucket(bucket)
                except S3Error as exc:
                    if exc.code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                        raise
            self.client.put_object(
                bucket,
                key,
                io.BytesIO(content),
                length=len(content),
                content_type=content_type,
            )
        except (MinioException, OSError, TimeoutError) as exc:
            raise ObjectStoreUnavailableError from exc

    def get_object(self, *, bucket: str, key: str) -> bytes:
        from minio.error import MinioException, S3Error

        response = None
        try:
            response = self.client.get_object(bucket, key)
            return response.read()
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise ObjectStoreNotFoundError from exc
            raise ObjectStoreUnavailableError from exc
        except (MinioException, OSError, TimeoutError) as exc:
            raise ObjectStoreUnavailableError from exc
        finally:
            if response is not None:
                with suppress(OSError):
                    response.close()
                with suppress(OSError):
                    response.release_conn()

    def delete_object(self, *, bucket: str, key: str) -> None:
        from minio.error import MinioException, S3Error

        try:
            self.client.remove_object(bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return
            raise ObjectStoreUnavailableError from exc
        except (MinioException, OSError, TimeoutError) as exc:
            raise ObjectStoreUnavailableError from exc


@lru_cache
def get_object_store() -> MinioObjectStore:
    settings = get_settings()
    return MinioObjectStore(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        secure=settings.minio_secure,
    )
