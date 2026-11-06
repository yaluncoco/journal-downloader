"""Build an onedir application and an Inno Setup installer."""
import argparse
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys

from engine import VERSION

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iscc", type=Path, required=True, help="Path to Inno Setup 7 ISCC.exe")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    if not args.iscc.is_file():
        parser.error("Install Inno Setup 7 and pass the path to ISCC.exe")
    if not args.skip_tests:
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], cwd=ROOT, check=True)
    output = ROOT / "release" / f"v{VERSION}"
    output.mkdir(parents=True, exist_ok=True)
    work = ROOT / "build" / f"v{VERSION}"
    work.mkdir(parents=True, exist_ok=True)
    version = tuple(int(part) for part in VERSION.split(".")) + (0,)
    resource = work / "version.txt"
    resource.write_text(f'''VSVersionInfo(ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r},
mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)),
kids=[StringFileInfo([StringTable('040904B0', [
StringStruct('CompanyName', 'yaluncoco'), StringStruct('FileDescription', 'Journal Downloader'),
StringStruct('FileVersion', '{VERSION}'), StringStruct('ProductVersion', '{VERSION}'),
StringStruct('ProductName', 'Journal Downloader'), StringStruct('OriginalFilename', 'JournalDownloader.exe')
])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])''', encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir", "--windowed",
        "--collect-all", "customtkinter", "--name", "JournalDownloader", "--distpath", str(output),
        "--workpath", str(work), "--specpath", str(work), "--icon", str(ROOT / "app.ico"),
        "--version-file", str(resource)]
    for name in ("app.ico", "app.png", "journals.json", "journal_metadata.json", "DATA_SOURCES.md"):
        command += ["--add-data", str(ROOT / name) + ";."]
    command += ["--add-data", str(ROOT / "data-licenses") + ";data-licenses", str(ROOT / "app.py")]
    subprocess.run(command, cwd=ROOT, check=True)
    application = output / "JournalDownloader"
    for name in ("README.md", "使用说明.md", "INSTALLATION.txt", "DATA_SOURCES.md", "IEEE接口说明.md"):
        shutil.copy2(ROOT / name, application / name)
    shutil.copytree(ROOT / "data-licenses", application / "data-licenses", dirs_exist_ok=True)
    for package in ("customtkinter", "pillow", "pypdf", "pyinstaller", "darkdetect", "packaging"):
        distribution = importlib.metadata.distribution(package)
        for file in distribution.files or []:
            if "license" in file.name.lower() or "copying" in file.name.lower():
                target = application / "third-party-licenses" / package / file.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(file), target)
    python_license = Path(sys.base_prefix) / "LICENSE_PYTHON.txt"
    if python_license.is_file():
        shutil.copy2(python_license, application / "third-party-licenses" / "Python-LICENSE.txt")
    subprocess.run([str(args.iscc.resolve()), "/Qp", f"/DAppVersion={VERSION}",
        f"/DSourceDir={application}", f"/DOutputDir={output}", str(ROOT / "installer.iss")], cwd=ROOT, check=True)
    print(output / "JournalDownloader-Setup.exe")


if __name__ == "__main__":
    main()
