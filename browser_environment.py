"""Read only the Windows state relevant to isolated PDF browser launches."""
import ctypes
import os
from pathlib import Path


def process_elevated():
    if os.name != "nt":
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return None


def configured_policies(browser):
    """Report configured platform values; cloud policy precedence is not inferred."""
    if os.name != "nt":
        return []
    import winreg
    product = {"msedge.exe": "Microsoft\\Edge", "chrome.exe": "Google\\Chrome"}.get(Path(browser).name.lower())
    if not product:
        return []
    result = []
    for label, hive in (("HKLM", winreg.HKEY_LOCAL_MACHINE), ("HKCU", winreg.HKEY_CURRENT_USER)):
        for bits, view in ((64, winreg.KEY_WOW64_64KEY), (32, winreg.KEY_WOW64_32KEY)):
            try:
                with winreg.OpenKey(hive, "SOFTWARE\\Policies\\" + product, 0, winreg.KEY_READ | view) as key:
                    for name in ("HeadlessModeEnabled", "PrintingEnabled", "UserDataDir"):
                        try:
                            value, kind = winreg.QueryValueEx(key, name)
                        except FileNotFoundError:
                            continue
                        if kind not in (winreg.REG_DWORD, winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                            continue
                        result.append({"hive": label, "view": bits, "name": name,
                                       "value": value if isinstance(value, int) else str(value)[:1024]})
            except FileNotFoundError:
                continue
            except OSError as error:
                result.append({"hive": label, "view": bits, "read_error": getattr(error, "winerror", None)})
    return result


def profile_policy_conflict(policies):
    # UserDataDir can override --user-data-dir and forward our command to an
    # existing personal session. Do not attempt that launch or change the policy.
    return any(item.get("name") == "UserDataDir" and item.get("value") for item in policies)
