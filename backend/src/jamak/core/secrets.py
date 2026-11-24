"""Protect API keys at rest with Windows DPAPI (current-user scope), via ctypes.

Limit: any program running as the same Windows user can decrypt the blob. DPAPI only
protects the database file when it is copied to another PC or read by another user.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_ENTROPY = b"jamak_converter/api-key/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(plain: str) -> bytes:
    if sys.platform != "win32":
        raise SecretError("API keys can only be stored on Windows (DPAPI)")
    src, _keep1 = _blob(plain.encode("utf-8"))
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(src), "jamak api key", ctypes.byref(ent), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise SecretError(f"CryptProtectData failed ({ctypes.GetLastError()})")
    return _take(out)


def unprotect(blob: bytes) -> str:
    if sys.platform != "win32":
        raise SecretError("API keys can only be read on Windows (DPAPI)")
    src, _keep1 = _blob(blob)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(src), None, ctypes.byref(ent), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        # never include the blob or the key in the message
        raise SecretError("다른 Windows 사용자나 PC에서 저장한 키입니다. 키를 다시 등록하세요")
    return _take(out).decode("utf-8")


def mask(last4: str) -> str:
    return f"••••{last4}"
