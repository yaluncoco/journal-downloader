"""User-initiated updates from this project's GitHub releases only."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid

from engine import Control, VERSION, atomic_bytes

REPOSITORY = "yaluncoco/journal-downloader"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET = "JournalDownloader.exe"
MAX_EXE_SIZE = 150 * 1024 * 1024
UPDATE_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("更新版本号格式无效。")
    return tuple(int(n) for n in match.groups())


def safe_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in UPDATE_HOSTS
            or parsed.port not in (None, 443) or parsed.username or parsed.password):
        raise ValueError("更新地址不属于受信任的 GitHub 下载服务器。")
    return value


class UpdateRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class Release:
    tag: str
    url: str
    exe_url: str
    checksum_url: str
    size: int
    digest: str


class Updater:
    def __init__(self, control=None, progress=lambda text: None):
        self.control = control or Control()
        self.progress = progress
        # GitHub follows the system proxy, independently of the publisher network setting.
        self.opener = urllib.request.build_opener(UpdateRedirect())

    def read(self, url, limit, destination=None, expected_size=None):
        request = urllib.request.Request(safe_url(url), headers={
            "User-Agent": f"JournalDownloader/{VERSION}", "Accept": "application/octet-stream" if destination else "application/json"})
        self.control.checkpoint()
        with self.opener.open(request, timeout=30) as response:
            size, pieces = 0, []
            handle = destination.open("wb") if destination else None
            try:
                while True:
                    self.control.checkpoint()
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("更新文件超过允许大小。")
                    if handle:
                        handle.write(chunk)
                        self.progress(f"下载更新 · {size / 1024 / 1024:.1f} / {(expected_size or size) / 1024 / 1024:.1f} MB")
                    else:
                        pieces.append(chunk)
                length = response.headers.get("Content-Length", "")
                if (length.isdigit() and size != int(length)) or (expected_size is not None and size != expected_size):
                    raise ValueError("更新文件下载不完整，请重试。")
            finally:
                if handle:
                    handle.close()
        return b"".join(pieces)

    def latest(self):
        data = json.loads(self.read(LATEST, 2 * 1024 * 1024))
        tag = data.get("tag_name", "")
        version_tuple(tag)
        if data.get("draft") or data.get("prerelease"):
            raise ValueError("没有可用的正式版本。")
        url = f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
        if data.get("html_url") != url:
            raise ValueError("版本来源校验失败。")
        assets = {item.get("name"): item for item in data.get("assets", [])}
        exe, checksum = assets.get(ASSET), assets.get("SHA256.txt")
        if not exe or not checksum:
            raise ValueError("该版本缺少 EXE 或校验文件，请稍后重试。")
        prefix = f"https://github.com/{REPOSITORY}/releases/download/{tag}/"
        if exe.get("browser_download_url") != prefix + ASSET or checksum.get("browser_download_url") != prefix + "SHA256.txt":
            raise ValueError("更新附件来源校验失败。")
        size = exe.get("size", 0)
        if not isinstance(size, int) or not 100_000 <= size <= MAX_EXE_SIZE:
            raise ValueError("更新附件大小无效。")
        digest = exe.get("digest", "") or ""
        if digest and not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise ValueError("GitHub 更新摘要格式无效。")
        return Release(tag, url, prefix + ASSET, prefix + "SHA256.txt", size, digest)

    def download(self, release, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        checksums = self.read(release.checksum_url, 64 * 1024).decode("ascii")
        expected = []
        for line in checksums.splitlines():
            match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
            if match and match[2] == ASSET:
                expected.append(match[1].lower())
        if len(expected) != 1:
            raise ValueError("更新文件的 SHA-256 校验记录缺失或重复。")
        if release.digest and release.digest.lower() != "sha256:" + expected[0]:
            raise ValueError("GitHub 摘要与校验文件不一致，已取消更新。")
        target = directory / ASSET
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected[0]:
            return target, expected[0]
        temporary = directory / (ASSET + ".part")
        try:
            self.read(release.exe_url, MAX_EXE_SIZE, temporary, release.size)
            data = temporary.read_bytes()
            if not data.startswith(b"MZ") or hashlib.sha256(data).hexdigest() != expected[0]:
                raise ValueError("更新文件校验失败，已取消更新。")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        self.progress("下载完成，校验通过。")
        return target, expected[0]


def prepare_install(downloaded, expected_sha, target=None, wait_pid=None, launch=True):
    """Write a reviewable updater script; caller launches it only after user clicks Update."""
    target = Path(target or sys.executable).resolve()
    downloaded = Path(downloaded).resolve()
    if target == downloaded or target.suffix.lower() != ".exe" or not target.is_file():
        raise ValueError("无法确定需要更新的 EXE。")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("更新摘要无效。")
    if hashlib.sha256(downloaded.read_bytes()).hexdigest() != expected_sha:
        raise ValueError("待安装文件已发生变化。")
    token = uuid.uuid4().hex[:12]
    # Verify write access before closing the user's app.
    probe = target.parent / (".journal-update-" + token + ".probe")
    try:
        with probe.open("xb") as handle:
            handle.write(b"write access check")
    except OSError:
        raise ValueError("程序所在文件夹无法写入。请把 EXE 放到有写入权限的文件夹后再更新。") from None
    finally:
        probe.unlink(missing_ok=True)
    script = downloaded.parent / ("install-" + token + ".ps1")
    atomic_bytes(script, ("\ufeff" + INSTALL_SCRIPT).encode("utf-8"))
    result = downloaded.parent / "install-result.json"
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    arguments = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                 "-TargetPath", str(target), "-NewPath", str(downloaded), "-ExpectedSha", expected_sha,
                 "-WaitPid", str(os.getpid() if wait_pid is None else wait_pid), "-Token", token,
                 "-ResultPath", str(result)]
    if not launch:
        arguments.append("-NoLaunch")
    return arguments, result


def launch_install(arguments):
    environment = os.environ.copy()
    environment.pop("PSModulePath", None)  # Do not inherit PowerShell 7's incompatible module path.
    return subprocess.Popen(arguments, creationflags=subprocess.CREATE_NO_WINDOW,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True, env=environment)


INSTALL_SCRIPT = r'''
param([string]$TargetPath, [string]$NewPath, [string]$ExpectedSha, [int]$WaitPid,
      [string]$Token, [string]$ResultPath, [switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
$targetFull = [IO.Path]::GetFullPath($TargetPath)
$targetDir = [IO.Path]::GetDirectoryName($targetFull)
$incoming = Join-Path $targetDir ('.journal-update-' + $Token + '.exe')
$backup = $targetFull + '.previous-' + $Token
$movedOld = $false
$installedNew = $false
function Get-UpdateHash([string]$FilePath) {
    $stream = [IO.File]::OpenRead($FilePath)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
try {
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($incoming)) -ne $targetDir) { throw 'Invalid staging path' }
    if ((Get-UpdateHash $NewPath) -ne $ExpectedSha) { throw 'Update hash mismatch' }
    Copy-Item -LiteralPath $NewPath -Destination $incoming
    if ((Get-UpdateHash $incoming) -ne $ExpectedSha) { throw 'Staged file hash mismatch' }
    $selfTest = Join-Path ([IO.Path]::GetDirectoryName($ResultPath)) ('self-test-' + $Token + '.json')
    $testArgs = @('--self-test', ('"' + $selfTest + '"'))
    $check = Start-Process -FilePath $incoming -ArgumentList $testArgs -PassThru -WindowStyle Hidden
    if (-not $check.WaitForExit(60000)) { $check.Kill(); throw 'New version self-test timed out' }
    if ($check.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $selfTest)) { throw 'New version self-test failed' }
    $health = Get-Content -LiteralPath $selfTest -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $health.ok) { throw 'New version health check failed' }
    $deadline = [DateTime]::UtcNow.AddMinutes(2)
    while ($WaitPid -gt 0 -and (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Current application is still running' }
        Start-Sleep -Milliseconds 400
    }
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try { Move-Item -LiteralPath $targetFull -Destination $backup; $movedOld = $true; break }
        catch { if ($attempt -eq 29) { throw }; Start-Sleep -Milliseconds 500 }
    }
    Move-Item -LiteralPath $incoming -Destination $targetFull
    $installedNew = $true
    if (-not $NoLaunch) { Start-Process -FilePath $targetFull -WorkingDirectory $targetDir }
    @{ok=$true; backup=$backup; target=$targetFull} | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
} catch {
    $failure = $_.Exception.Message
    if ($movedOld) {
        if ($installedNew -and (Test-Path -LiteralPath $targetFull)) { Remove-Item -LiteralPath $targetFull }
        Move-Item -LiteralPath $backup -Destination $targetFull
    }
    @{ok=$false; error=$failure; target=$targetFull} | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    if (-not $NoLaunch -and (Test-Path -LiteralPath $targetFull)) { Start-Process -FilePath $targetFull -WorkingDirectory $targetDir }
} finally {
    if (Test-Path -LiteralPath $incoming) { Remove-Item -LiteralPath $incoming }
}
'''
