"""Windows 当前用户 DPAPI 保护及测试注入接口。"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Protocol

from runner.errors import ProtectionError, ProtectionUnavailableError


class Protector(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _CtypesDpapiBackend:
    """极小的 ctypes 包装，避免引入会把凭证带出 DPAPI 的替代加密。"""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise ProtectionUnavailableError("Windows DPAPI 仅在 Windows 上可用")
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_pointer = ctypes.POINTER(_DataBlob)
        function_arguments = [
            blob_pointer,
            wintypes.LPCWSTR,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        self._crypt32.CryptProtectData.argtypes = function_arguments
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = function_arguments
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    @staticmethod
    def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(value or b"\x00")
        blob = _DataBlob(
            len(value),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        return blob, buffer

    def _protect(self, value: bytes, *, unprotect: bool) -> bytes:
        input_blob, input_buffer = self._blob(value)
        output_blob = _DataBlob()
        function = self._crypt32.CryptUnprotectData if unprotect else self._crypt32.CryptProtectData
        succeeded = function(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        del input_buffer
        if not succeeded or not output_blob.pbData:
            error_code = ctypes.get_last_error()
            action = "解密" if unprotect else "加密"
            raise ProtectionError(f"DPAPI {action}失败（系统错误 {error_code}）")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            self._kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))

    def protect(self, value: bytes) -> bytes:
        return self._protect(value, unprotect=False)

    def unprotect(self, value: bytes) -> bytes:
        return self._protect(value, unprotect=True)


class DpapiProtector:
    """默认保护器：Windows 使用当前用户 DPAPI，其他平台明确拒绝。"""

    def __init__(self, backend: Protector | None = None) -> None:
        self._backend = backend or _CtypesDpapiBackend()

    def protect(self, value: bytes) -> bytes:
        return self._backend.protect(value)

    def unprotect(self, value: bytes) -> bytes:
        return self._backend.unprotect(value)


class MemoryProtector:
    """仅用于单元测试的显式注入保护器，不作为生产默认实现。"""

    _PREFIX = b"TEST-PROTECTED:"

    def protect(self, value: bytes) -> bytes:
        return self._PREFIX + value[::-1]

    def unprotect(self, value: bytes) -> bytes:
        if not value.startswith(self._PREFIX):
            raise ProtectionError("测试保护数据无效")
        return value[len(self._PREFIX) :][::-1]
