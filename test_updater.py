"""Release origin, checksum and executable replacement boundaries."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from engine import Cancelled, Control
from updater import (ASSET, INSTALL_SCRIPT, LATEST, REPOSITORY, Release, UpdateRedirect,
                     Updater, prepare_install, safe_url, version_tuple)


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
            with patch.object(client.opener, "open", return_value=Response(b"partial", 999)):
                with self.assertRaisesRegex(ValueError, "不完整"):
                    client.read(LATEST, 1024)

    def test_cancel(self):
        control = Control()
        control.stop.set()
        with self.assertRaises(Cancelled):
            Updater(control).read(LATEST, 1024)

    def test_install_script_uses_literal_paths_and_health_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "user's app.exe"
            new = root / "new.exe"
            target.write_bytes(b"old")
            new.write_bytes(DATA)
            args, result = prepare_install(new, DIGEST, target, wait_pid=0, launch=False)
            self.assertIn(str(target.resolve()), args)
            self.assertIn("-NoLaunch", args)
            self.assertFalse(result.exists())
            self.assertEqual(target.read_bytes(), b"old")
            self.assertIn("--self-test", INSTALL_SCRIPT)
            self.assertIn("Move-Item -LiteralPath $backup -Destination $targetFull", INSTALL_SCRIPT)
            self.assertIn("Get-UpdateHash $incoming", INSTALL_SCRIPT)
            self.assertNotIn(str(target), INSTALL_SCRIPT)


if __name__ == "__main__":
    unittest.main()
