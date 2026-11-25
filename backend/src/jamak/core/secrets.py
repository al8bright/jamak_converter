"""Protect API keys at rest.

Windows: DPAPI (current-user scope) via ctypes; the database holds the encrypted blob.
Limit: any program running as the same Windows user can decrypt the blob. DPAPI only
protects the database file when it is copied to another PC or read by another user.

macOS: the login Keychain (via `keyring`'s macOS backend, which calls Security.framework directly, so
the key never appears on a command line). The database holds only `keychain:<random id>`.
Limit: macOS may ask once to let a new build of the app read the item.
"""

from __future__ import annotations

import ctypes
import os
import sys

_ENTROPY = b"jamak_converter/api-key/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretError(RuntimeError):
    pass


KEYCHAIN_PREFIX = b"keychain:"
KEYCHAIN_SERVICE = "jamak_converter"


def _keychain():
    from keyring.backends.macOS import Keyring  # macOS only (dependency marker in pyproject.toml)

    return Keyring()


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(plain: str) -> bytes:
    if sys.platform == "darwin":
        ref = os.urandom(16).hex()
        try:
            _keychain().set_password(KEYCHAIN_SERVICE, ref, plain)
        except Exception as e:  # never include the key in the message
            raise SecretError(f"키체인에 저장하지 못했습니다 ({type(e).__name__})") from None
        return KEYCHAIN_PREFIX + ref.encode()
    if sys.platform != "win32":
        raise SecretError("API keys can only be stored on Windows (DPAPI) or macOS (Keychain)")
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
    if blob.startswith(KEYCHAIN_PREFIX):
        if sys.platform != "darwin":
            raise SecretError("Mac 키체인에 저장한 키입니다. 이 PC에서 키를 다시 등록하세요")
        try:
            value = _keychain().get_password(KEYCHAIN_SERVICE, blob[len(KEYCHAIN_PREFIX):].decode())
        except Exception as e:
            raise SecretError(f"키체인에서 키를 읽지 못했습니다 ({type(e).__name__})") from None
        if value is None:
            raise SecretError("키체인에서 키를 찾지 못했습니다. 키를 다시 등록하세요")
        return value
    if sys.platform != "win32":
        raise SecretError("Windows에서 저장한 키입니다. 이 Mac에서 키를 다시 등록하세요")
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


def forget(blob: bytes) -> None:
    """Delete what `protect` stored outside the database (the Keychain item); DPAPI blobs need nothing."""
    if blob.startswith(KEYCHAIN_PREFIX) and sys.platform == "darwin":
        try:
            _keychain().delete_password(KEYCHAIN_SERVICE, blob[len(KEYCHAIN_PREFIX):].decode())
        except Exception:  # already gone
            pass


def mask(last4: str) -> str:
    return f"••••{last4}"
