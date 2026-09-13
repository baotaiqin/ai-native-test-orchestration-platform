import base64
import binascii
import ctypes
import hashlib
import sys
from ctypes import wintypes
from typing import Any

from app.core.config import Settings, get_settings
from app.core.exceptions import SecurityConfigurationError
from app.core.fernet_secret_provider import (
    FERNET_PREFIX,
    MAX_FERNET_TOKEN_BYTES,
    MAX_SECRET_PLAINTEXT_BYTES,
    decrypt_fernet_secret,
    encrypt_fernet_secret,
    validate_fernet_key_file,
)

DPAPI_PREFIX = "dpapi:v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_CRYPTPROTECT_LOCAL_MACHINE = 0x4


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    return blob, buffer


def _require_windows() -> None:
    if sys.platform != "win32":
        raise SecurityConfigurationError(
            "当前 Secret Provider 仅支持 Windows 本机开发，部署前需配置跨平台密钥服务"
        )


def _windows_libraries() -> tuple[Any, Any]:
    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = crypt32.CryptProtectData.argtypes
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32


def _encrypt_dpapi_secret(plaintext: str, application_key: str) -> str:
    _require_windows()
    if not isinstance(plaintext, str):
        raise SecurityConfigurationError("Secret 明文类型无效")
    if not isinstance(application_key, str):
        raise SecurityConfigurationError("Secret 应用上下文无效")
    try:
        encoded_plaintext = plaintext.encode("utf-8")
        encoded_context = application_key.encode("utf-8")
    except UnicodeEncodeError:
        raise SecurityConfigurationError("Secret 明文或应用上下文编码无效") from None
    if len(encoded_plaintext) > MAX_SECRET_PLAINTEXT_BYTES:
        raise SecurityConfigurationError("Secret 明文超过大小限制")
    input_blob, input_buffer = _to_blob(encoded_plaintext)
    entropy_blob, entropy_buffer = _to_blob(hashlib.sha256(encoded_context).digest())
    output_blob = _DataBlob()
    crypt32, kernel32 = _windows_libraries()
    success = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN | _CRYPTPROTECT_LOCAL_MACHINE,
        ctypes.byref(output_blob),
    )
    _ = (input_buffer, entropy_buffer)
    if not success:
        error_code = ctypes.get_last_error()
        raise SecurityConfigurationError(f"Secret 加密失败（Windows 错误码 {error_code}）")
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return DPAPI_PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))


def _decrypt_dpapi_secret(ciphertext: str, application_key: str) -> str:
    _require_windows()
    if not isinstance(application_key, str):
        raise SecurityConfigurationError("Secret 应用上下文无效")
    token = ciphertext.removeprefix(DPAPI_PREFIX)
    if not token or len(token) > MAX_FERNET_TOKEN_BYTES:
        raise SecurityConfigurationError("DPAPI Secret 密文格式无效")
    try:
        encrypted = base64.b64decode(token, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise SecurityConfigurationError("DPAPI Secret 密文格式无效") from None
    input_blob, input_buffer = _to_blob(encrypted)
    try:
        encoded_context = application_key.encode("utf-8")
    except UnicodeEncodeError:
        raise SecurityConfigurationError("Secret 应用上下文编码无效") from None
    entropy_blob, entropy_buffer = _to_blob(hashlib.sha256(encoded_context).digest())
    output_blob = _DataBlob()
    crypt32, kernel32 = _windows_libraries()
    success = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    _ = (input_buffer, entropy_buffer)
    if not success:
        raise SecurityConfigurationError(
            f"Secret 解密失败（Windows 错误码 {ctypes.get_last_error()}），"
            "应用密钥或 Windows 用户可能已变更"
        )
    try:
        if output_blob.cbData > MAX_SECRET_PLAINTEXT_BYTES:
            raise SecurityConfigurationError("DPAPI Secret 明文超过大小限制")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
        except UnicodeDecodeError:
            raise SecurityConfigurationError("DPAPI Secret 明文编码无效") from None
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))


def validate_secret_provider_configuration(settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    if resolved.secret_provider == "dpapi":
        _require_windows()
        return
    if resolved.secret_provider == "fernet":
        validate_fernet_key_file(resolved.secret_fernet_key_file)
        return
    raise SecurityConfigurationError("Secret Provider 配置无效")


def encrypt_secret(plaintext: str, application_key: str) -> str:
    settings = get_settings()
    if settings.secret_provider == "dpapi":
        return _encrypt_dpapi_secret(plaintext, application_key)
    if settings.secret_provider == "fernet":
        return encrypt_fernet_secret(
            plaintext, application_key, settings.secret_fernet_key_file
        )
    raise SecurityConfigurationError("Secret Provider 配置无效")


def decrypt_secret(ciphertext: str, application_key: str) -> str:
    if not isinstance(ciphertext, str):
        raise SecurityConfigurationError("Secret 密文格式不受支持")
    if ciphertext.startswith(DPAPI_PREFIX):
        return _decrypt_dpapi_secret(ciphertext, application_key)
    if ciphertext.startswith(FERNET_PREFIX):
        return decrypt_fernet_secret(
            ciphertext, application_key, get_settings().secret_fernet_key_file
        )
    raise SecurityConfigurationError("Secret 密文格式不受支持")
