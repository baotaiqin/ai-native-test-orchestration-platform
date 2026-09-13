import base64
import hashlib
import hmac
import secrets

from app.core.config import get_settings


def _base64url_token(prefix: str) -> str:
    value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    return f"{prefix}{value}"


def generate_registration_token() -> str:
    return _base64url_token("rt_")


def generate_runner_credential() -> str:
    return _base64url_token("rc_")


def digest_secret(value: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def matches_digest(value: str, expected_digest: str) -> bool:
    return hmac.compare_digest(digest_secret(value), expected_digest)
