"""Windows DPAPI storage, scoped to the current Windows account."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

from xml_utils import atomic_bytes

HEADER = b"JournalDownloader-DPAPI-1\0"


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    if os.name != "nt":
        raise OSError("本机 Key 记忆需要 Windows DPAPI。")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    if decrypt:
        operation = crypt.CryptUnprotectData
        description_type = ctypes.POINTER(wintypes.LPWSTR)
        description = None
    else:
        operation = crypt.CryptProtectData
        description_type = wintypes.LPCWSTR
        description = "JournalDownloader Elsevier API Key"
    operation.argtypes = [ctypes.POINTER(Blob), description_type, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    try:
        # UI_FORBIDDEN only: omit LOCAL_MACHINE so protection stays user-scoped.
        if not operation(ctypes.byref(incoming), description, None, None, None, 1, ctypes.byref(outgoing)):
            raise OSError("Windows 无法处理本机加密 Key。")
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.memset(buffer, 0, len(data))
        if outgoing.pbData:
            ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
            kernel.LocalFree(outgoing.pbData)


def key_file(directory):
    return Path(directory) / "elsevier-key.dpapi"


def load_key(directory):
    path = key_file(directory)
    try:
        if path.stat().st_size > 65536:
            return ""
        data = path.read_bytes()
        if not data.startswith(HEADER):
            return ""
        return _crypt(data[len(HEADER):], decrypt=True).decode("ascii")
    except (OSError, ValueError, UnicodeError):
        return ""


def save_key(directory, key):
    key = key.strip()
    if not key:
        forget_key(directory)
        return
    if len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("API Key 格式无效。")
    atomic_bytes(key_file(directory), HEADER + _crypt(key.encode("ascii")))


def forget_key(directory):
    key_file(directory).unlink(missing_ok=True)
