"""Discover installed PDF browsers without opening a user's browser profile."""
from contextlib import suppress
import os
from pathlib import Path
import re
import shutil


def browser_name(path):
    name = Path(path).name.lower()
    return {"msedge.exe": "Microsoft Edge", "msedge": "Microsoft Edge",
            "chrome.exe": "Google Chrome", "chrome": "Google Chrome"}.get(name, Path(path).name)


def executable_path(value):
    """App Paths stores a path; StartMenuInternet stores a quoted command."""
    value = os.path.expandvars(str(value).strip())
    match = re.match(r'^"([^"]+)"(?:\s|$)', value)
    if match:
        value = match[1]
    elif match := re.match(r'^(.*?\.exe)(?:\s|$)', value, re.IGNORECASE):
        value = match[1]
    return Path(value)


def selected_browser(value):
    path = Path(str(value)).expanduser()
    if not path.is_absolute() or path.name.lower() not in ("msedge.exe", "chrome.exe"):
        raise ValueError("请选择已安装的 msedge.exe（Microsoft Edge）或 chrome.exe（Google Chrome）。")
    if not path.is_file():
        raise ValueError("指定的浏览器程序不存在，请重新选择或切回自动检测。")
    return path.resolve()


def registry_paths():
    if os.name != "nt":
        return []
    import winreg
    paths = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            access = winreg.KEY_READ | view
            for name in ("msedge.exe", "chrome.exe"):
                with suppress(OSError):
                    with winreg.OpenKey(hive, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + name,
                                        0, access) as key:
                        paths.append(winreg.QueryValueEx(key, "")[0])
            # Some per-user/custom installations only register the browser client.
            with suppress(OSError):
                with winreg.OpenKey(hive, "SOFTWARE\\Clients\\StartMenuInternet", 0, access) as clients:
                    for index in range(winreg.QueryInfoKey(clients)[0]):
                        with suppress(OSError):
                            name = winreg.EnumKey(clients, index)
                            with winreg.OpenKey(clients, name + "\\shell\\open\\command", 0, access) as command:
                                paths.append(winreg.QueryValueEx(command, "")[0])
    return paths


def browsers(selection=""):
    if selection:
        # An explicit choice is exclusive: diagnostics must test that browser.
        return [selected_browser(selection)]
    candidates = []
    for name in ("PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMFILES", "LOCALAPPDATA"):
        folder = os.environ.get(name)
        if folder:
            candidates.extend(Path(folder) / suffix for suffix in (
                "Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"))
    for value in registry_paths():
        path = executable_path(value)
        if path.is_absolute() and path.name.lower() in ("msedge.exe", "chrome.exe"):
            candidates.append(path)
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        if binary := shutil.which(name):
            candidates.append(Path(binary))
    unique = {}
    for path in candidates:
        with suppress(OSError, ValueError):
            if path.is_file():
                resolved = path.resolve()
                unique.setdefault(os.path.normcase(str(resolved)), resolved)
    # Installation root order must not put x86 Chrome ahead of x64 Edge.
    return sorted(unique.values(), key=lambda path: path.name.lower() not in ("msedge.exe", "msedge"))
