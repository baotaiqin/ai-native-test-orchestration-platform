from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "AI 原生智能测试编排平台"
    env: str = "development"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    secret_key: str = "please-change-this-development-key"
    access_token_expire_minutes: int = Field(default=120, ge=1, le=7 * 24 * 60)
    auth_token_issuer: str = Field(default="ai-test-platform", min_length=1, max_length=128)
    auth_token_audience: str = Field(
        default="ai-test-platform-api", min_length=1, max_length=128
    )
    cors_origins_text: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        validation_alias="APP_CORS_ORIGINS",
    )

    database_url: str = (
        "mysql+pymysql://test_platform:test_platform@127.0.0.1:3306/"
        "ai_test_platform?charset=utf8mb4"
    )
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_key_prefix: str = "ai-test-platform"
    run_event_stream_maxlen: int = Field(default=1000, ge=100, le=100_000)
    run_event_stream_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60, ge=300, le=30 * 24 * 60 * 60
    )
    runner_registration_token_ttl_seconds: int = 600
    runner_heartbeat_interval_seconds: int = 30
    runner_heartbeat_ttl_seconds: int = 90
    runner_disconnect_scan_enabled: bool = True
    runner_disconnect_scan_interval_seconds: int = Field(default=30, ge=5, le=3600)
    runner_total_timeout_default_ms: int = Field(
        default=15 * 60 * 1000, ge=1000, le=86_400_000
    )
    runner_execution_timeout_grace_seconds: int = Field(default=60, ge=0, le=3600)
    web_exploration_stale_timeout_seconds: int = Field(default=900, ge=60, le=86_400)
    schedule_scan_enabled: bool = True
    schedule_scan_interval_seconds: int = Field(default=30, ge=5, le=3600)
    webhook_scan_enabled: bool = True
    webhook_scan_interval_seconds: int = Field(default=15, ge=5, le=3600)
    metrics_enabled: bool = True
    rabbitmq_url: str = "amqp://test_platform:test_platform@127.0.0.1:5672/"
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "test_platform"
    minio_secret_key: str = "test_platform_dev_only"
    minio_bucket: str = "ai-test-evidence"
    minio_secure: bool = False
    demo_enabled: bool | None = None
    demo_public_url: str = "http://127.0.0.1:8765"

    secret_provider: Literal["dpapi", "fernet"] = "dpapi"
    secret_fernet_key_file: Path | None = None

    dev_admin_username: str = "admin"
    dev_admin_password: str = "admin123"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_text.split(",") if origin.strip()]

    @property
    def docs_enabled(self) -> bool:
        return self.env != "production" or self.debug

    @property
    def demo_available(self) -> bool:
        if self.demo_enabled is not None:
            return self.demo_enabled
        return self.env != "production"

    @property
    def log_level(self) -> str:
        return "DEBUG" if self.debug else "INFO"

    @field_validator("secret_provider", mode="before")
    @classmethod
    def normalize_secret_provider(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("secret_fernet_key_file", mode="before")
    @classmethod
    def normalize_fernet_key_file(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("demo_public_url")
    @classmethod
    def validate_demo_public_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Demo 公网地址必须是无凭据、查询参数和片段的 HTTP(S) origin")
        return normalized

    @model_validator(mode="after")
    def reject_development_secrets_in_production(self) -> "Settings":
        if self.env == "production":
            invalid = {
                "secret_key": self.secret_key == "please-change-this-development-key",
                "dev_admin_password": self.dev_admin_password == "admin123",
            }
            names = [name for name, is_invalid in invalid.items() if is_invalid]
            if names:
                raise ValueError(f"生产环境不能使用开发默认值：{', '.join(names)}")
        return self

    @model_validator(mode="after")
    def require_explicit_secret_provider_in_production(self) -> "Settings":
        if self.env == "production" and "secret_provider" not in self.model_fields_set:
            raise ValueError("生产环境必须显式配置 Secret Provider")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
