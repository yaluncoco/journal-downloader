"""Short, writable print directories independent of library and installation paths."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import tempfile


def local_appdata():
    """Ask Windows directly when environment variables point at redirected paths."""
    if os.name != "nt":
        return None
    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 28, None, 0, buffer) == 0:
        return Path(buffer.value)
    return None


def workspace_roots():
    roots = []
    try:
        roots.append(Path(tempfile.gettempdir()))
    except OSError:
        pass
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "JournalXMLDownloader/pdf-work")
    actual = local_appdata()
    if actual:
        roots.append(actual / "JournalXMLDownloader/pdf-work")
    return list(dict.fromkeys(roots))


@contextmanager
def print_workspace():
    failures = []
    temporary = None
    for root in workspace_roots():
        try:
            root = root.resolve()
            # Leave room for Chromium's nested profile files on Windows systems
            # without long-path support. Never change the system's path policy.
            if os.name == "nt" and len(str(root)) > 140:
                failures.append(f"{root}：路径过长")
                continue
            root.mkdir(parents=True, exist_ok=True)
            temporary = tempfile.TemporaryDirectory(prefix="jpdf-", dir=root, ignore_cleanup_errors=True)
            break
        except OSError as error:
            failures.append(f"{root}：{error}")
    if temporary is None:
        raise OSError("无法创建 PDF 临时目录；目录可能过长或不可写。" + "；".join(failures))
    with temporary as name:
        yield Path(name)
