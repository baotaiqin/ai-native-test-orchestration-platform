import base64
import binascii
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

from app.core.exceptions import SecurityConfigurationError

FERNET_PREFIX = "fernet:v1:"
MAX_SECRET_PLAINTEXT_BYTES = 1_000_000
MAX_FERNET_PAYLOAD_BYTES = MAX_SECRET_PLAINTEXT_BYTES * 6 + 512
MAX_FERNET_TOKEN_BYTES = 8_100_000
MAX_FERNET_KEY_FILE_BYTES = 4_096
MAX_FERNET_KEYS = 8

_INNER_VERSION = 1
_PURPOSE = "ai-test-platform-secret"
_CONTEXT_DOMAIN = b"ai-test-platform:secret-context:v1\x00"
_FERNET_KEY = re.compile(rb"^[A-Za-z0-9_-]{43}=$")


def _configuration_error(message: str) -> SecurityConfigurationError:
    return SecurityConfigurationError(message)


def _read_key_file(path: Path | None) -> tuple[bytes, ...]:
    if path is None:
        raise _configuration_error("Fernet Provider 密钥文件未配置")
    if not isinstance(path, Path):
        raise _configuration_error("Fernet Provider 密钥文件配置无效")
    try:
        if not path.is_file():
            raise _configuration_error("Fernet Provider 密钥文件不可读")
        with path.open("rb") as handle:
            raw = handle.read(MAX_FERNET_KEY_FILE_BYTES + 1)
    except SecurityConfigurationError:
        raise
    except (OSError, TypeError, ValueError):
        raise _configuration_error("Fernet Provider 密钥文件不可读") from None
    if not raw:
        raise _configuration_error("Fernet Provider 密钥文件为空")
    if len(raw) > MAX_FERNET_KEY_FILE_BYTES:
        raise _configuration_error("Fernet Provider 密钥文件超过大小限制")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise _configuration_error("Fernet Provider 密钥文件格式无效") from None

    lines = text.splitlines()
    if not lines or any(not line or line != line.strip() for line in lines):
        raise _configuration_error("Fernet Provider 密钥文件格式无效")
    if len(lines) > MAX_FERNET_KEYS:
        raise _configuration_error("Fernet Provider 密钥数量超过限制")

    keys: list[bytes] = []
    seen: set[bytes] = set()
    for line in lines:
        encoded = line.encode("ascii")
        if not _FERNET_KEY.fullmatch(encoded):
            raise _configuration_error("Fernet Provider 密钥文件格式无效")
        try:
            decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError):
            raise _configuration_error("Fernet Provider 密钥文件格式无效") from None
        if len(decoded) != 32 or base64.urlsafe_b64encode(decoded) != encoded:
            raise _configuration_error("Fernet Provider 密钥文件格式无效")
        if encoded in seen:
            raise _configuration_error("Fernet Provider 密钥文件包含重复密钥")
        seen.add(encoded)
        keys.append(encoded)
    return tuple(keys)


def _fernet_types() -> tuple[type[Any], type[Exception], type[Any]]:
    try:
        from cryptography.fernet import Fernet, InvalidToken, MultiFernet
    except ImportError:
        raise _configuration_error("Fernet Provider 依赖不可用") from None
    return Fernet, InvalidToken, MultiFernet


def _key_ring(path: Path | None) -> Any:
    keys = _read_key_file(path)
    fernet_type, _, multi_fernet_type = _fernet_types()
    return multi_fernet_type([fernet_type(key) for key in keys])


def validate_fernet_key_file(path: Path | None) -> None:
    _key_ring(path)


def _context_digest(application_key: str) -> str:
    if not isinstance(application_key, str):
        raise _configuration_error("Secret 应用上下文无效")
    try:
        encoded = application_key.encode("utf-8")
    except UnicodeEncodeError:
        raise _configuration_error("Secret 应用上下文无效") from None
    return hashlib.sha256(_CONTEXT_DOMAIN + encoded).hexdigest()


def _plaintext_bytes(plaintext: str) -> bytes:
    if not isinstance(plaintext, str):
        raise _configuration_error("Secret 明文类型无效")
    try:
        encoded = plaintext.encode("utf-8")
    except UnicodeEncodeError:
        raise _configuration_error("Secret 明文编码无效") from None
    if len(encoded) > MAX_SECRET_PLAINTEXT_BYTES:
        raise _configuration_error("Secret 明文超过大小限制")
    return encoded


def encrypt_fernet_secret(
    plaintext: str, application_key: str, key_file: Path | None
) -> str:
    _plaintext_bytes(plaintext)
    payload = json.dumps(
        {
            "application_context": _context_digest(application_key),
            "plaintext": plaintext,
            "purpose": _PURPOSE,
            "version": _INNER_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_FERNET_PAYLOAD_BYTES:
        raise _configuration_error("Fernet Secret 内部载荷超过大小限制")
    token = _key_ring(key_file).encrypt(payload)
    if len(token) > MAX_FERNET_TOKEN_BYTES:
        raise _configuration_error("Fernet Secret 密文超过大小限制")
    return FERNET_PREFIX + token.decode("ascii")


def decrypt_fernet_secret(
    ciphertext: str, application_key: str, key_file: Path | None
) -> str:
    token_text = ciphertext.removeprefix(FERNET_PREFIX)
    if not token_text or len(token_text) > MAX_FERNET_TOKEN_BYTES:
        raise _configuration_error("Fernet Secret 密文格式无效")
    try:
        token = token_text.encode("ascii")
    except UnicodeEncodeError:
        raise _configuration_error("Fernet Secret 密文格式无效") from None

    ring = _key_ring(key_file)
    _, invalid_token_type, _ = _fernet_types()
    try:
        raw_payload = ring.decrypt(token)
    except invalid_token_type:
        raise _configuration_error("Fernet Secret 密文无法认证") from None
    if len(raw_payload) > MAX_FERNET_PAYLOAD_BYTES:
        raise _configuration_error("Fernet Secret 内部载荷超过大小限制")
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _configuration_error("Fernet Secret 内部载荷无效") from None
    if not isinstance(payload, dict) or set(payload) != {
        "application_context",
        "plaintext",
        "purpose",
        "version",
    }:
        raise _configuration_error("Fernet Secret 内部载荷无效")
    version = payload.get("version")
    purpose = payload.get("purpose")
    if (
        type(version) is not int
        or version != _INNER_VERSION
        or not isinstance(purpose, str)
        or purpose != _PURPOSE
    ):
        raise _configuration_error("Fernet Secret 内部载荷无效")
    bound_context = payload.get("application_context")
    plaintext = payload.get("plaintext")
    if not isinstance(bound_context, str) or not isinstance(plaintext, str):
        raise _configuration_error("Fernet Secret 内部载荷无效")
    if not hmac.compare_digest(bound_context, _context_digest(application_key)):
        raise _configuration_error("Secret 应用上下文不匹配")
    _plaintext_bytes(plaintext)
    return plaintext
