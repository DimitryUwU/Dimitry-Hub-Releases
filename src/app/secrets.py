from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import platform
from ctypes import wintypes
from pathlib import Path

from .database import DATA_DIR, atomic_write_bytes

SECRET_FILE = DATA_DIR / "secrets.dat"
_ENTROPY = b"Dimitry Hub credential store v1"


class SecretStoreError(RuntimeError):
    pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_encrypt(data: bytes) -> bytes:
    in_blob, in_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "Dimitry Hub",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    )
    _ = (in_buffer, entropy_buffer)
    if not ok:
        raise SecretStoreError("Windows no pudo proteger la credencial")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_decrypt(data: bytes) -> bytes:
    in_blob, in_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    )
    _ = (in_buffer, entropy_buffer)
    if not ok:
        raise SecretStoreError("Windows no pudo leer la credencial protegida")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _fallback_key() -> bytes:
    identity = f"{platform.node()}|{platform.system()}|{DATA_DIR.resolve()}|DimitryHub-v1"
    return hashlib.sha256(identity.encode("utf-8", "ignore")).digest()


def _fallback_encrypt(data: bytes) -> bytes:
    key = _fallback_key()
    mixed = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    return b"FALLBACK1:" + base64.urlsafe_b64encode(mixed)


def _fallback_decrypt(data: bytes) -> bytes:
    if not data.startswith(b"FALLBACK1:"):
        raise SecretStoreError("El almacén de credenciales no pertenece a este equipo")
    mixed = base64.urlsafe_b64decode(data.split(b":", 1)[1])
    key = _fallback_key()
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(mixed))


def storage_mode() -> str:
    return "windows-dpapi" if os.name == "nt" else "local-fallback"


def _read_all() -> dict[str, str]:
    if not SECRET_FILE.exists():
        return {}
    raw = SECRET_FILE.read_bytes()
    if not raw:
        return {}
    try:
        clear = _dpapi_decrypt(raw) if os.name == "nt" else _fallback_decrypt(raw)
        payload = json.loads(clear.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        raise SecretStoreError(f"No se pudo abrir el almacén de credenciales: {exc}") from exc


def _write_all(values: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    clear = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    protected = _dpapi_encrypt(clear) if os.name == "nt" else _fallback_encrypt(clear)
    atomic_write_bytes(SECRET_FILE, protected)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass


def get_secret(name: str) -> str:
    env_name = name.upper()
    if os.environ.get(env_name):
        return os.environ[env_name]
    return _read_all().get(name, "")


def set_secret(name: str, value: str) -> None:
    clean_name = name.strip().lower()
    if not clean_name or not clean_name.replace("_", "").isalnum():
        raise SecretStoreError("Nombre de credencial inválido")
    values = _read_all()
    if value:
        values[clean_name] = value.strip()
    else:
        values.pop(clean_name, None)
    _write_all(values)


def delete_secret(name: str) -> None:
    set_secret(name, "")


def has_secret(name: str) -> bool:
    try:
        return bool(get_secret(name))
    except SecretStoreError:
        return False


def masked_secret(name: str) -> str:
    value = get_secret(name)
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:3]}••••{value[-4:]}"
