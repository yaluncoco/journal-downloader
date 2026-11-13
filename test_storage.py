"""Storage migration, intact-paper resume and encrypted-key regression tests."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import credential_store as credentials
import library_store as storage
from engine import (ActionRequired, Config, Downloader, PRESETS, library_lock,
                    load_json, paths_for, save_json, save_lines, verify_saved)
from smoke_test import xml, offline_search_page


class StorageTests(unittest.TestCase):
    def setUp(self):
        search = patch('article_search.request_page', side_effect=offline_search_page)
        search.start()
        self.addCleanup(search.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        state = patch.object(storage, "STATE_HOME", self.root / "app-data")
        state.start()
        self.addCleanup(state.stop)
        self.library = self.root / PRESETS[0].issn
        self.library.mkdir()

    def test_migration_keeps_only_papers_and_skips_fulltext_after_list_update(self):
        row = {"doi": "10.1016/j.demo.2024.old", "year": 2024, "date": "2024", "title": "Example"}
        target, txt, _ = paths_for(self.library, row)
        target.parent.mkdir(parents=True)
        target.write_bytes(xml(row["doi"]))
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        target.with_suffix(".xml.sha256").write_text(digest)
        txt.parent.mkdir(parents=True)
        txt.write_text("legacy reading text")
        save_json(self.library / "journal.json", {"issn": PRESETS[0].issn})
        save_json(self.library / "indexes/2024-2024.json", {"complete": True, "issn": PRESETS[0].issn,
            "years": [2024, 2024], "records": [row]})
        save_lines(self.library / "manifest.jsonl", [row])
        (self.library / ".xml_download.lock").write_bytes(b"0")
        pdf = self.library / "fulltext_pdf/2024/article.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"original PDF bytes")
        save_json(pdf.with_suffix(".pdf.json"), {"pdf_sha256": "example"})
        cache = pdf.parent.parent / ".image-cache/article/picture.image"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"original cached image")
        with library_lock(self.library, managed=True):
            storage.migrate_library(self.library)
        with library_lock(pdf.parent.parent, managed=True):
            storage.migrate_pdf(pdf.parent.parent)
        self.assertEqual({p.name for p in self.library.iterdir()}, {"fulltext_xml", "fulltext_pdf"})
        self.assertTrue(all(p.suffix in (".xml", ".pdf") for p in self.library.rglob("*") if p.is_file()))
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), digest)
        self.assertEqual(storage.sidecar_path(target).read_text(), digest)
        self.assertEqual(pdf.read_bytes(), b"original PDF bytes")
        self.assertEqual((storage.state_dir(pdf.parent.parent) / "image-cache/article/picture.image").read_bytes(), b"original cached image")
        cfg = Config("TEST_KEY", PRESETS[0].url, 2024, 2024, self.library)
        resumed = Downloader(cfg)
        resumed.public_json = resumed.fetch = lambda *a: self.fail("Migrated XML must skip fulltext requests")
        resumed.run()
        self.assertEqual((resumed.reused, resumed.new_count), (1, 0))
        self.assertEqual(resumed.base, self.library)

    def test_legacy_corrupt_checksum_still_rejects_file_after_move(self):
        row = {"doi": "10.1016/j.demo.corrupt", "year": 2024}
        target, _, _ = paths_for(self.library, row)
        target.parent.mkdir(parents=True)
        target.write_bytes(xml(row["doi"]))
        target.with_suffix(".xml.sha256").write_text("wrong")
        storage.migrate_library(self.library)
        self.assertIsNone(verify_saved(self.library, row))

    def test_migration_preserves_unknown_files_and_conflicting_auxiliary_files(self):
        unknown = self.library / "notes.txt"
        unknown.write_text("my handwritten notes")
        save_json(self.library / "journal.json", {"old": True})
        save_json(storage.state_dir(self.library) / "journal.json", {"new": True})
        storage.migrate_library(self.library)
        self.assertEqual(unknown.read_text(), "my handwritten notes")
        self.assertEqual(load_json(storage.state_dir(self.library) / "journal.json"), {"new": True})
        conflict = next((storage.state_dir(self.library) / "migration-conflicts").iterdir())
        self.assertEqual(load_json(conflict), {"old": True})

    def test_legacy_and_current_locks_are_respected(self):
        with library_lock(self.library):
            with self.assertRaises(ActionRequired):
                with library_lock(self.library, managed=True):
                    self.fail("Should not enter a locked legacy library")
        with library_lock(self.library, managed=True):
            with self.assertRaises(ActionRequired):
                with library_lock(self.library, managed=True):
                    self.fail("Should not enter a locked current library")

    def test_migration_rejects_paths_outside_library(self):
        outside = self.root / "important.txt"
        outside.write_text("keep")
        with self.assertRaises(OSError):
            storage._checked_move(outside, storage.state_dir(self.library) / "important.txt", self.library)
        self.assertEqual(outside.read_text(), "keep")


@unittest.skipUnless(os.name == "nt", "Windows DPAPI")
class CredentialTests(unittest.TestCase):
    def test_encrypted_roundtrip_replace_forget_and_corrupt(self):
        with tempfile.TemporaryDirectory() as temp:
            secret = "SYNTHETIC_TEST_KEY_FOR_DPAPI"
            credentials.save_key(temp, secret)
            self.assertEqual(credentials.load_key(temp), secret)
            self.assertNotIn(secret.encode(), credentials.key_file(temp).read_bytes())
            credentials.save_key(temp, "REPLACEMENT_TEST_KEY")
            self.assertEqual(credentials.load_key(temp), "REPLACEMENT_TEST_KEY")
            credentials.key_file(temp).write_bytes(credentials.HEADER + b"truncated")
            self.assertEqual(credentials.load_key(temp), "")
            credentials.save_key(temp, secret)
            credentials.forget_key(temp)
            self.assertEqual(credentials.load_key(temp), "")
            self.assertFalse(credentials.key_file(temp).exists())

    def test_encryption_failure_never_falls_back_to_plaintext(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(credentials, "_crypt", side_effect=OSError("failed")):
                with self.assertRaises(OSError):
                    credentials.save_key(temp, "TEST_PRIVATE_KEY")
            self.assertFalse(credentials.key_file(temp).exists())


if __name__ == "__main__":
    unittest.main()
