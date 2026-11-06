"""Isolated Windows install/upgrade/uninstall integration test; no real library data."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

from app_runtime import independent_environment
from engine import VERSION
from updater import prepare_install, launch_install, installation_status

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iscc", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True, help="Previous portable EXE, used only as a test fixture")
    args = parser.parse_args()
    stage = (ROOT / "qa" / f"installer-v{VERSION}").resolve()
    stage.mkdir(parents=True, exist_ok=True)
    installed = stage / "安装测试 app"
    assert installed.is_relative_to((ROOT / "qa").resolve())
    report = {}
    silent = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]

    def run(command, log_name, timeout=120):
        with (stage / log_name).open("wb") as log:
            return subprocess.run(list(map(str, command)), cwd=ROOT, env=independent_environment(),
                creationflags=subprocess.CREATE_NO_WINDOW, stdout=log, stderr=subprocess.STDOUT,
                timeout=timeout).returncode

    def compile_setup(version, source, name):
        out = stage / name
        out.mkdir(exist_ok=True)
        code = run([args.iscc.resolve(), "/Q", "/DQA", "/DAppVersion=" + version,
            "/DSourceDir=" + str(source.resolve()), "/DOutputDir=" + str(out), ROOT / "installer.iss"], name + ".log")
        assert code == 0, (name, code)
        return out / "JournalDownloader-Setup.exe"

    # The test installer has a different AppId and makes no Start menu/desktop shortcuts.
    uninstaller = installed / "unins000.exe"
    if uninstaller.exists():
        assert run([uninstaller, *silent], "cleanup-previous-test.log") == 0
        deadline = time.monotonic() + 15
        while uninstaller.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not uninstaller.exists()
    old_source = stage / "previous-payload"
    old_source.mkdir(exist_ok=True)
    shutil.copy2(args.previous, old_source / "JournalDownloader.exe")
    old_setup = compile_setup("1.5.0", old_source, "old-setup")
    new_source = ROOT / "release" / f"v{VERSION}" / "JournalDownloader"
    new_setup = compile_setup(VERSION, new_source, "new-setup")
    install_args = [*silent, "/DIR=" + str(installed)]
    assert run([old_setup, *install_args, "/LOG=" + str(stage / "first-install.log")], "first-run.log") == 0
    executable = installed / "JournalDownloader.exe"
    old_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    assert old_hash == hashlib.sha256(args.previous.read_bytes()).hexdigest()
    report["first_install"] = True
    paper = installed / "user-paper.xml"
    paper.write_text("synthetic user-owned paper", encoding="utf-8")
    personal = stage / "synthetic-app-data" / "settings.json"
    personal.parent.mkdir(exist_ok=True)
    personal.write_text('{"output":"synthetic-library"}', encoding="utf-8")

    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = kernel.CreateMutexW(None, False, "Local\\JournalDownloader-Isolated-QA")
    assert mutex
    try:
        code = run([new_setup, *install_args, "/LOG=" + str(stage / "running-blocked.log")], "running.log", 45)
        assert code != 0, "Installer must not replace a running application"
        assert hashlib.sha256(executable.read_bytes()).hexdigest() == old_hash
        report["running_app_blocks_install"] = code
    finally:
        kernel.CloseHandle(mutex)

    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    locked = kernel.CreateFileW(str(executable), 0x80000000, 1, None, 3, 0, None)
    assert locked not in (None, ctypes.c_void_p(-1).value)
    try:
        code = run([new_setup, *install_args, "/LOG=" + str(stage / "locked-file.log")], "locked.log", 45)
        assert code != 0, "Locked executable must not be silently replaced"
        assert hashlib.sha256(executable.read_bytes()).hexdigest() == old_hash
        report["locked_file_rolls_back"] = code
    finally:
        kernel.CloseHandle(locked)

    # Exercise the production launcher with an inherited, obsolete PyInstaller environment.
    digest = hashlib.sha256(new_setup.read_bytes()).hexdigest()
    command, record = prepare_install(new_setup, digest, f"v{VERSION}", executable)
    dll_before = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(dll_before), dll_before)
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    private_dll = str(new_source / "_internal")
    kernel.SetDllDirectoryW(private_dll)
    try:
        with patch.dict("os.environ", {"_PYI_APPLICATION_HOME_DIR": "C:\\missing-old-runtime",
                "_PYI_PARENT_PROCESS_LEVEL": "1", "_PYI_ARCHIVE_FILE": str(executable)}), patch("sys.frozen", True, create=True):
            process = launch_install(command + silent, record)
            assert process.wait(timeout=120) == 0
        restored = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(restored), restored)
        assert restored.value == private_dll
    finally:
        kernel.SetDllDirectoryW(dll_before.value or None)
    assert hashlib.sha256(executable.read_bytes()).hexdigest() == hashlib.sha256((new_source / executable.name).read_bytes()).hexdigest()
    report["upgrade_through_update_launcher"] = True
    health = stage / "installed-health.json"
    assert run([executable, "--self-test", health, "--pdf"], "health-run.log", 120) == 0
    data = json.loads(health.read_text(encoding="utf-8"))
    assert data.get("ok") and data.get("version") == VERSION, data
    report["installed_pdf_and_ui_self_test"] = data
    assert installation_status(record.parent.parent, VERSION, executable)[0] == "complete"
    assert run([new_setup, *install_args, "/LOG=" + str(stage / "repair-install.log")], "repair.log") == 0
    report["repeat_install"] = True
    assert run([uninstaller, *silent, "/LOG=" + str(stage / "uninstall.log")], "uninstall-run.log") == 0
    # Inno's initial process exits before its relocated uninstaller removes itself.
    deadline = time.monotonic() + 15
    while (executable.exists() or uninstaller.exists()) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not executable.exists() and not uninstaller.exists()
    assert paper.read_text(encoding="utf-8") == "synthetic user-owned paper"
    assert json.loads(personal.read_text(encoding="utf-8"))["output"] == "synthetic-library"
    report["uninstall_preserves_user_files"] = True
    (stage / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
