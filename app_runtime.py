"""Windows application lifecycle shared with the installer."""
import ctypes
import os
import sys
from contextlib import contextmanager

APP_MUTEX = "Local\\JournalDownloader-6EC76596-6DF5-4BEA-A645-E8D8084EC6D8"


def independent_environment():
    environment = os.environ.copy()
    for name in list(environment):
        if name.startswith("_PYI_") or name in {"_MEIPASS2", "PSModulePath"}:
            environment.pop(name, None)
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


@contextmanager
def clean_dll_search_path():
    """External installers must not inherit PyInstaller's private DLL directory."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        yield
        return
    kernel = ctypes.windll.kernel32
    buffer = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    setter = kernel.SetDllDirectoryW
    setter.argtypes = [ctypes.c_wchar_p]
    if not setter(None):
        raise ctypes.WinError()
    try:
        yield
    finally:
        setter(buffer.value or None)


def hold_app_mutex():
    # Keep the handle until process exit so Setup/Uninstall won't replace a live app.
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    create = ctypes.windll.kernel32.CreateMutexW
    create.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    create.restype = wintypes.HANDLE
    handle = create(None, False, APP_MUTEX)
    if not handle:
        raise ctypes.WinError()
    return handle
