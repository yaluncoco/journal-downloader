import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import Config, Downloader, PRESETS, library_path, paths_for, save_fulltext
from library_inventory import scan_existing, xml_files, detect_journal
from library_store import sidecar_path
from smoke_test import offline_search_page, xml, cached_index


def identified_xml(doi, journal=PRESETS[0]):
    return xml(doi).replace(b"</coredata>", (
        f"<prism:issn>{journal.issn}</prism:issn><prism:publicationName>{journal.name}</prism:publicationName>"
        "<prism:coverDate>2024-01-01</prism:coverDate></coredata>").encode())


class InventoryTests(unittest.TestCase):
    def setUp(self):
        search = patch('article_search.request_page', side_effect=offline_search_page)
        search.start()
        self.addCleanup(search.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        state = patch("library_store.STATE_HOME", self.root / "state")
        state.start()
        self.addCleanup(state.stop)

    def test_scan_checks_fulltext_checksum_years_and_ignores_review(self):
        library = self.root / "library"
        folder = library / "fulltext_xml/2024"
        folder.mkdir(parents=True)
        (folder / "renamed.xml").write_bytes(identified_xml("10.1016/j.demo.good"))
        (folder / "broken.xml").write_bytes(b"<truncated>")
        (folder / "abstract.xml").write_bytes(xml("10.1016/j.demo.abstract", body=False))
        bad = folder / "modified.xml"
        bad.write_bytes(identified_xml("10.1016/j.demo.modified"))
        sidecar_path(bad).parent.mkdir(parents=True, exist_ok=True)
        sidecar_path(bad).write_text("wrong")
        later = library / "fulltext_xml/2025/next.xml"
        later.parent.mkdir()
        later.write_bytes(identified_xml("10.1016/j.demo.next"))
        review = library / "review_required/review.xml"
        review.parent.mkdir()
        review.write_bytes(identified_xml("10.1016/j.demo.review"))
        result = scan_existing(library, 2024, 2024)
        self.assertEqual(set(result["saved"]), {"10.1016/j.demo.good"})
        self.assertEqual((result["invalid"], result["outside"]), (3, 1))
        self.assertFalse(sidecar_path(folder / "renamed.xml").exists(), "Preview is read-only")

    def test_moved_library_identifies_xml_and_year_folder_without_local_state(self):
        library = self.root / "my-moved-library"
        folder = library / "fulltext_xml/2024"
        folder.mkdir(parents=True)
        (folder / "my-paper.xml").write_bytes(identified_xml("10.1016/j.demo.moved"))
        for selected in (library, library / "fulltext_xml", folder):
            self.assertEqual(library_path(selected, PRESETS[0]), library)
            self.assertEqual(library_path(selected, PRESETS[1]), library / PRESETS[1].issn)

    def test_renamed_xml_skips_api_and_limit_counts_only_new_files(self):
        library = self.root / PRESETS[0].issn
        rows = [{"doi": f"10.1016/j.demo.resume{i}", "year": 2024, "date": "2024", "title": "Test"} for i in range(3)]
        cached_index(library, rows)
        folder = library / "fulltext_xml/2024"
        folder.mkdir(parents=True)
        (folder / "user-renamed-paper.xml").write_bytes(identified_xml(rows[0]["doi"]))
        cfg = Config("SYNTHETIC_KEY", PRESETS[0].url, 2024, 2024, folder, interval=0, max_downloads=1)
        runner = Downloader(cfg)
        runner.public_json = lambda *_: self.fail("Cached list should be reused")
        calls = []
        def fetch(doi):
            calls.append(doi)
            return identified_xml(doi), {"http": 200}
        runner.fetch = fetch
        runner.run()
        self.assertEqual((runner.reused, runner.new_count), (1, 1))
        self.assertEqual(calls, [rows[1]["doi"]])
        self.assertEqual(len(list(folder.glob("*.xml"))), 2)

    def test_corrupt_existing_file_is_downloaded_again(self):
        library = self.root / PRESETS[0].issn
        row = {"doi": "10.1016/j.demo.corrupt", "year": 2024, "date": "2024", "title": "Test"}
        cached_index(library, [row])
        target, txt, _ = paths_for(library, row)
        save_fulltext(target, txt, identified_xml(row["doi"]), row["doi"])
        target.write_bytes(target.read_bytes().replace(b"Test content", b"Tampered content"))
        runner = Downloader(Config("SYNTHETIC_KEY", PRESETS[0].url, 2024, 2024, self.root, interval=0))
        calls = []
        def fetch(doi):
            calls.append(doi)
            return identified_xml(doi), {"http": 200}
        runner.fetch = fetch
        runner.run()
        self.assertEqual(calls, [row["doi"]])
        self.assertEqual((runner.reused, runner.new_count), (0, 1))

    def test_same_doi_in_multiple_files_counts_once(self):
        folder = self.root / "fulltext_xml/2024"
        folder.mkdir(parents=True)
        for name in ("first.xml", "second.xml"):
            (folder / name).write_bytes(identified_xml("10.1016/j.demo.duplicate"))
        result = scan_existing(self.root)
        self.assertEqual(len(result["saved"]), 1)
        self.assertEqual(result["duplicates"], 1)

    def test_legacy_years_and_partial_new_directory_resume_without_api(self):
        library = self.root / PRESETS[0].issn
        rows = [{"doi": f"10.1016/j.demo.legacy{i}", "year": 2024, "date": "2024", "title": "Test"} for i in range(3)]
        cached_index(library, rows)
        old = library / "2024"
        old.mkdir(parents=True)
        for row in rows:
            (old / (row["doi"].split("/")[-1] + ".xml")).write_bytes(identified_xml(row["doi"]))
        self.assertEqual(library_path(library, PRESETS[0]), library)
        self.assertEqual(detect_journal(library)["issn"], PRESETS[0].issn)
        # Creating a new, empty canonical directory must not hide the old files.
        new = library / "fulltext_xml" / "2024"
        new.mkdir(parents=True)
        self.assertEqual(len(scan_existing(library)["saved"]), 3)
        (new / "duplicate.xml").write_bytes(identified_xml(rows[0]["doi"]))
        self.assertEqual(scan_existing(library)["duplicates"], 1)
        runner = Downloader(Config("SYNTHETIC_KEY", PRESETS[0].url, 2024, 2024, library, interval=0))
        runner.fetch = lambda *_: self.fail("Complete legacy XML triggered an API request")
        runner.run()
        self.assertEqual((runner.reused, runner.new_count), (3, 0))
        self.assertEqual(len(list(old.glob("*.xml"))), 3)

    def test_junction_enumeration_is_read_only_and_cycle_safe(self):
        import os
        if os.name != "nt":
            self.skipTest("Windows directory junction regression")
        import _winapi
        library, backing = self.root / "library", self.root / "backing"
        library.mkdir()
        backing.mkdir()
        source = backing / "paper.xml"
        source.write_bytes(identified_xml("10.1016/j.demo.linked"))
        _winapi.CreateJunction(str(backing), str(library / "fulltext_xml"))
        _winapi.CreateJunction(str(library), str(backing / "cycle"))
        self.assertEqual(len(list(xml_files(library))), 1)
        self.assertEqual(len(scan_existing(library)["saved"]), 1)
        self.assertTrue(source.exists())

    def test_unreadable_directory_is_not_silently_empty(self):
        def denied(root, **kwargs):
            kwargs["onerror"](PermissionError(13, "denied", str(root / "2024")))
            yield  # Match os.walk's lazy behavior.
        with patch("library_inventory.os.walk", side_effect=denied):
            with self.assertRaisesRegex(OSError, "避免重复下载"):
                scan_existing(self.root)
