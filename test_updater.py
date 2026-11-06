"""Release origin, checksum and executable replacement boundaries."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import os

from engine import Cancelled, Control
from updater import (ASSET, LATEST, REPOSITORY, Release, UpdateRedirect,
                     Updater, prepare_install, launch_install, installation_status, safe_url, version_tuple)


DATA = b"MZ" + b"synthetic executable fixture" * 5000
DIGEST = hashlib.sha256(DATA).hexdigest()


def release_metadata():
    prefix = f"https://github.com/{REPOSITORY}/releases/download/v9.0.0/"
    return {"tag_name": "v9.0.0", "draft": False, "prerelease": False,
            "html_url": f"https://github.com/{REPOSITORY}/releases/tag/v9.0.0",
            "assets": [{"name": ASSET, "size": len(DATA), "browser_download_url": prefix + ASSET,
                        "digest": "sha256:" + DIGEST},
                       {"name": "SHA256.txt", "browser_download_url": prefix + "SHA256.txt"}]}


class Response(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


class UpdaterTests(unittest.TestCase):
    def test_versions(self):
        self.assertGreater(version_tuple("v1.10.0"), version_tuple("1.9.9"))
        for tag in ("v1.1", "nightly", "1.2.3-rc1", "../../1.2.3"):
            with self.assertRaises(ValueError):
                version_tuple(tag)

    def test_redirect_blocks_external_http_and_userinfo(self):
        for url in ("http://github.com/test", "https://evil.test/update.exe",
                    "https://github.com@evil.test/test", "https://github.com:8443/test"):
            with self.assertRaises(ValueError):
                safe_url(url)
        self.assertEqual(safe_url("https://release-assets.githubusercontent.com/test"), "https://release-assets.githubusercontent.com/test")
        with self.assertRaises(ValueError):
            UpdateRedirect().redirect_request(urllib.request.Request(LATEST), None, 302, "", {}, "https://evil.test/key")

    def test_metadata_origin_and_digest(self):
        client = Updater()
        meta = release_metadata()
        with patch.object(client, "read", return_value=json.dumps(meta).encode()):
            release = client.latest()
        self.assertEqual(release.tag, "v9.0.0")
        meta["assets"][0]["browser_download_url"] = "https://github.com/other/repository/update.exe"
        with patch.object(client, "read", return_value=json.dumps(meta).encode()):
            with self.assertRaises(ValueError):
                client.latest()

    def test_download_verified_and_reused(self):
        client = Updater()
        meta = release_metadata()
        with patch.object(client, "read", return_value=json.dumps(meta).encode()):
            release = client.latest()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(client.opener, "open", side_effect=[
                    Response((DIGEST + "  " + ASSET).encode()), Response(DATA)]):
                target, digest = client.download(release, tmp)
            self.assertEqual(target.read_bytes(), DATA)
            self.assertEqual(digest, DIGEST)
            with patch.object(client.opener, "open", return_value=Response((DIGEST + "  " + ASSET).encode())) as fetch:
                client.download(release, tmp)
                self.assertEqual(fetch.call_count, 1)

    def test_rejects_hash_mismatch_and_cleans_partial(self):
        client = Updater()
        meta = release_metadata()
        with patch.object(client, "read", return_value=json.dumps(meta).encode()):
            release = client.latest()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(client.opener, "open", side_effect=[
                    Response((DIGEST + "  " + ASSET).encode()), Response(b"XX" + DATA[2:])]):
                with self.assertRaisesRegex(ValueError, "校验失败"):
                    client.download(release, tmp)
            self.assertFalse((Path(tmp) / ASSET).exists())
            self.assertFalse((Path(tmp) / (ASSET + ".part")).exists())

    def test_conflicting_checksum_and_truncated_response(self):
        client = Updater()
        with patch.object(client, "read", return_value=json.dumps(release_metadata()).encode()):
            release = client.latest()
        with tempfile.TemporaryDirectory() as tmp:
            checksum = ("0" * 64 + "  " + ASSET).encode()
            with patch.object(client.opener, "open", return_value=Response(checksum)):
                with self.assertRaisesRegex(ValueError, "不一致"):
                    client.download(release, tmp)
            with patch.object(client.control, "wait"), patch.object(client.opener, "open", side_effect=lambda *a, **k: Response(b"partial", 999)):
                with self.assertRaisesRegex(ValueError, "不完整"):
                    client.read(LATEST, 1024)

    def test_cancel(self):
        control = Control()
        control.stop.set()
        with self.assertRaises(Cancelled):
            Updater(control).read(LATEST, 1024)

    def test_retries_truncated_download_from_start(self):
        client = Updater()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "test.part"
            with patch.object(client.control, "wait") as wait, patch.object(client.opener, "open",
                    side_effect=[Response(b"partial", len(DATA)), Response(DATA)]):
                client.read(LATEST, len(DATA), output, len(DATA))
            self.assertEqual(output.read_bytes(), DATA)
            wait.assert_called_once()

    def test_no_retry_for_auth_failure_and_cancel_during_backoff(self):
        client = Updater()
        with patch.object(client.opener, "open", side_effect=urllib.error.HTTPError(LATEST, 403, "Forbidden", {}, None)) as fetch:
            with self.assertRaises(urllib.error.HTTPError):
                client.read(LATEST, 1024)
            self.assertEqual(fetch.call_count, 1)
        with patch.object(client.control, "wait", side_effect=Cancelled), patch.object(client.opener, "open", side_effect=TimeoutError):
            with self.assertRaises(Cancelled):
                client.read(LATEST, 1024)

    def test_installer_validation_environment_and_completion(self):
        with tempfile.TemporaryDirectory(prefix="installer 中文 ") as tmp:
            root = Path(tmp)
            setup = root / "v9.0.0" / ASSET
            setup.parent.mkdir()
            setup.write_bytes(DATA)
            target = root / "installed app" / "JournalDownloader.exe"
            target.parent.mkdir()
            target.write_bytes(b"old")
            (target.parent / "installation.ini").write_text("[Install]")
            (target.parent / "unins000.exe").write_bytes(b"uninstaller")
            arguments, record = prepare_install(setup, DIGEST, "v9.0.0", target)
            self.assertIn("/DIR=" + str(target.parent.resolve()), arguments)
            self.assertNotIn("/VERYSILENT", arguments)
            with patch.dict(os.environ, {"_PYI_APPLICATION_HOME_DIR": "stale", "_PYI_PARENT_PROCESS_LEVEL": "1"}):
                with patch("updater.subprocess.Popen") as start:
                    start.return_value.pid = 123
                    launch_install(arguments, record)
                    environment = start.call_args.kwargs["env"]
                    self.assertFalse(any(k.startswith("_PYI_") for k in environment))
                    self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")
            self.assertEqual(installation_status(root, "8.0.0", target)[0], "pending")
            self.assertEqual(installation_status(root, "9.0.0", target)[0], "complete")
            self.assertIsNone(installation_status(root, "9.0.0", target)[0])
            self.assertEqual(target.read_bytes(), b"old")

    def test_portable_migration_tamper_and_old_failure_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup = root / "v9.0.0" / ASSET
            setup.parent.mkdir()
            setup.write_bytes(DATA)
            args, record = prepare_install(setup, DIGEST, "v9.0.0", root / "portable.exe")
            self.assertFalse(any(a.startswith("/DIR=") for a in args))
            setup.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "发生变化"), patch("updater.subprocess.Popen") as start:
                launch_install(args, record)
            start.assert_not_called()
            record.unlink()
            (setup.parent / "install-result.json").write_text('{"ok":false,"error":"old failure"}')
            self.assertIsNone(installation_status(root)[0])

    def test_launch_failure_is_logged_and_not_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup = Path(tmp) / ASSET
            setup.write_bytes(DATA)
            args, record = prepare_install(setup, DIGEST, "v9.0.0")
            with patch("updater.subprocess.Popen", side_effect=OSError("blocked")):
                with self.assertRaises(OSError):
                    launch_install(args, record)
            data = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(data["state"], "launch_failed")



if __name__ == "__main__":
    unittest.main()
