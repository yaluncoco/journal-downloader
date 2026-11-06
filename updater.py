"""User-initiated updates from this project's GitHub releases only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error

from engine import Control, VERSION, save_json
from app_runtime import independent_environment, clean_dll_search_path

REPOSITORY = "yaluncoco/journal-downloader"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET = "JournalDownloader-Setup.exe"
MAX_EXE_SIZE = 200 * 1024 * 1024
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


class IncompleteDownload(ValueError):
    pass


class Updater:
    def __init__(self, control=None, progress=lambda text: None):
        self.control = control or Control()
        self.progress = progress
        # GitHub follows the system proxy, independently of the publisher network setting.
        self.opener = urllib.request.build_opener(UpdateRedirect())

    def read(self, url, limit, destination=None, expected_size=None):
        for attempt in range(3):
            try:
                return self._read_once(url, limit, destination, expected_size)
            except urllib.error.HTTPError as error:
                if error.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError,
                    http.client.IncompleteRead, IncompleteDownload):
                if attempt == 2:
                    raise
            self.progress(f"连接中断，正在重试（{attempt + 1}/2）…")
            self.control.wait(2 ** (attempt + 1))

    def _read_once(self, url, limit, destination=None, expected_size=None):
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
                    raise IncompleteDownload("更新文件下载不完整，请重试。")
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
            raise ValueError("该版本缺少安装包或校验文件，请到 GitHub Releases 下载安装版。")
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


def prepare_install(downloaded, expected_sha, tag, target=None):
    """Validate Setup again and prepare an interactive, logged installation."""
    version_tuple(tag)
    downloaded = Path(downloaded).resolve()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("更新摘要无效。")
    data = downloaded.read_bytes()
    if not data.startswith(b"MZ") or hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError("待安装文件已发生变化。")
    executable = Path(target or sys.executable).resolve()
    record = downloaded.parent / "installation-request.json"
    log = downloaded.parent / "setup.log"
    arguments = [str(downloaded), "/SP-", "/NORESTART", "/LOG=" + str(log)]
    # Installed copies upgrade in place. Portable copies use Setup's normal destination.
    if (executable.parent / "installation.ini").is_file() and (executable.parent / "unins000.exe").is_file():
        arguments.append("/DIR=" + str(executable.parent))
    save_json(record, {"version": tag, "source_exe": str(executable), "installer": str(downloaded),
        "sha256": expected_sha, "log": str(log), "state": "ready",
        "requested_at": datetime.now(timezone.utc).isoformat()})
    return arguments, record


def launch_install(arguments, record):
    data = json.loads(Path(record).read_text(encoding="utf-8"))
    if hashlib.sha256(Path(arguments[0]).read_bytes()).hexdigest() != data["sha256"]:
        raise ValueError("待安装文件已发生变化。")
    try:
        with clean_dll_search_path():
            process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                env=independent_environment())
    except OSError as error:
        data.update(state="launch_failed", error=str(error))
        save_json(record, data)
        raise
    data.update(state="installer_started", pid=process.pid)
    try:
        save_json(record, data)
    except OSError:
        pass  # Setup already started; don't launch a second copy after a log failure.
    return process


def installation_status(updates, current_version=VERSION, executable=None):
    """Only a successfully started newer app can confirm completion."""
    executable = Path(executable or sys.executable).resolve()
    records = list(Path(updates).glob("*/installation-request.json"))
    for record in sorted(records, key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            if data.get("state") == "completed":
                continue
            requested = version_tuple(data["version"])
            if requested <= version_tuple(current_version):
                data.update(state="completed", running_version=current_version)
                save_json(record, data)
                return "complete", data
            if Path(data.get("source_exe", "")).resolve() == executable:
                return "pending", data
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None, None
