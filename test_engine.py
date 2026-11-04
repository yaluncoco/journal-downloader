import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import engine as e
from smoke_test import xml, cached_index


class Response(io.BytesIO):
    def __init__(self, data, headers=None, status=200):
        super().__init__(data)
        self.headers = headers or {"Content-Length": str(len(data))}
        self.status = status


class Opener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_patch = patch("library_store.STATE_HOME", self.root / "app-state")
        self.state_patch.start()
        self.cfg = e.Config("PRIVATE_TEST_KEY", e.PRESETS[0].url, 2024, 2024, self.root, interval=0)
        self.base = self.root / e.PRESETS[0].issn
        self.row = {"doi": "10.1016/j.test.2024.123", "year": 2024, "date": "2024-01", "title": "Test"}

    def tearDown(self):
        self.state_patch.stop()
        self.tmp.cleanup()

    def runner(self):
        r = e.Downloader(self.cfg)
        r.control.wait = lambda *a, **k: r.control.checkpoint()
        return r

    def test_presets_and_search_url(self):
        for j in e.PRESETS:
            self.assertTrue(e.valid_issn(j.issn))
            self.assertEqual(e.parse_journal_url(j.url)[0], j.slug)
        self.assertEqual(e.parse_journal_url("https://www.sciencedirect.com/search?pub=Remote+Sensing+of+Environment&date=2024-2027")[0], e.PRESETS[0].slug)

    def test_reject_wrong_url_and_credentials(self):
        for url in ["https://evil.example/journal/a", "https://www.sciencedirect.com.evil.example/journal/a",
                    "https://key@www.sciencedirect.com/journal/a", "https://www.sciencedirect.com/science/article/pii/S123",
                    "https://www.sciencedirect.com:9000/journal/a", "https://www.sciencedirect.com/search?pub=a&pub=b"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                e.parse_journal_url(url)

    def test_config_rejects_bad_years_and_header(self):
        self.assertNotIn(self.cfg.api_key, repr(self.cfg))
        self.cfg.start_year = 2025
        with self.assertRaises(ValueError):
            self.cfg.validate()
        self.cfg.start_year = 2024
        self.cfg.api_key = "secret\nheader"
        with self.assertRaises(ValueError):
            self.cfg.validate()

    def test_print_year_takes_precedence(self):
        item = {"DOI": "10.1016/J.TEST", "title": ["A &amp; B"], "published-print": {"date-parts": [[2024, 1]]},
                "published-online": {"date-parts": [[2023, 11, 20]]}}
        row = e.metadata_record(item, 2024, 2024)
        self.assertEqual(row["year"], 2024)
        self.assertEqual(row["title"], "A & B")
        self.assertIsNone(e.metadata_record(item, 2023, 2023))

    def test_download_limit_validation(self):
        for value in (-1, 1.5, True, "10", None):
            self.cfg.max_downloads = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.cfg.validate()
        for value in (0, 1, 1000):
            self.cfg.max_downloads = value
            self.cfg.validate()

    def test_limit_counts_successes_and_preserves_resume_manifest(self):
        rows = [{**self.row, "doi": f"10.1016/j.test.2024.limit{i}"} for i in range(5)]
        cached_index(self.base, rows)
        target, txt, _ = e.paths_for(self.base, rows[0])
        e.save_fulltext(target, txt, xml(rows[0]["doi"]), rows[0]["doi"])
        corrupt, _, _ = e.paths_for(self.base, rows[1])
        corrupt.write_bytes(b"<broken")
        self.cfg.max_downloads = 2
        calls = []
        def fetch(doi):
            calls.append(doi)
            return xml(doi, body=doi != rows[2]["doi"]), {"http": 200}
        runner = self.runner()
        runner.fetch = fetch
        runner.run()
        self.assertEqual(calls, [row["doi"] for row in rows[1:4]])
        self.assertEqual((runner.new_count, runner.reused, len(runner.saved)), (2, 1, 3))
        summary = e.load_json(e.state_dir(self.base) / "download_summary.json")
        self.assertEqual(summary["stage"], "limit_reached")
        self.assertEqual(summary["pending"], 2)
        self.assertEqual(len((e.state_dir(self.base) / "manifest.jsonl").read_text().splitlines()), 5)
        self.assertIsNotNone(e.verify_saved(self.base, rows[1]))
        calls.clear()
        resumed = self.runner()
        resumed.fetch = fetch
        resumed.run()
        self.assertEqual(calls, [rows[4]["doi"]])
        self.assertEqual((resumed.new_count, resumed.reused), (1, 3))
        self.assertEqual(e.load_json(e.state_dir(self.base) / "download_summary.json")["stage"], "finished_with_pending")

    def test_limit_finishes_before_quota_wait(self):
        rows = [self.row, {**self.row, "doi": "10.1016/j.test.2024.second"}]
        cached_index(self.base, rows)
        self.cfg.max_downloads = 1
        deadline = time.time() + 3600
        runner = self.runner()
        runner.control.wait = lambda *a, **kw: self.fail("Limit reached: must not wait for the next request")
        runner.fetch = lambda doi: (xml(doi), {"http": 200, "remaining": "0", "quota_reset_epoch": str(deadline)})
        runner.run()
        self.assertEqual(runner.new_count, 1)
        self.assertEqual(e.load_json(e.state_dir(self.base) / "server_pause.json")["deadline"], deadline)

    def test_limit_leaves_deferred_network_failures_for_next_run(self):
        rows = [{**self.row, "doi": f"10.1016/j.test.2024.net{i}"} for i in range(3)]
        cached_index(self.base, rows)
        self.cfg.max_downloads = 1
        runner = self.runner()
        calls = []
        def fetch(doi):
            calls.append(doi)
            return (None, {"status": "network_error"}) if doi == rows[0]["doi"] else (xml(doi), {"http": 200})
        runner.fetch = fetch
        runner.run()
        self.assertEqual(calls, [rows[0]["doi"], rows[1]["doi"]])
        self.assertEqual(runner.new_count, 1)
        calls.clear()
        resumed = self.runner()
        resumed.fetch = lambda doi: (calls.append(doi) or xml(doi), {"http": 200})
        resumed.run()
        self.assertEqual(calls, [rows[0]["doi"]])
        self.assertEqual((resumed.new_count, resumed.reused), (1, 1))

    def test_zero_and_large_limits_download_available_records(self):
        rows = [{**self.row, "doi": f"10.1016/j.test.2024.all{i}"} for i in range(3)]
        for limit in (0, 10):
            with self.subTest(limit=limit):
                self.cfg.output = self.root / str(limit)
                self.cfg.max_downloads = limit
                cached_index(self.cfg.output / e.PRESETS[0].issn, rows)
                runner = self.runner()
                runner.fetch = lambda doi: (xml(doi), {"http": 200})
                runner.run()
                self.assertEqual(runner.new_count, 3)
                self.assertEqual(e.load_json(runner.state / "download_summary.json")["stage"], "complete")

    def test_custom_journal_exact_resolution(self):
        self.cfg.url = "https://www.sciencedirect.com/journal/synthetic-forest-measurements"
        r = self.runner()
        r.public_json = lambda u: {"message": {"items": [{"title": "Synthetic Forest Measurements", "ISSN": ["0168-1923"]}]}}
        self.assertEqual(r.resolve().issn, "0168-1923")
        r.public_json = lambda u: {"message": {"items": [{"title": "Wrong Journal", "ISSN": ["0168-1923"]}]}}
        with self.assertRaises(e.ActionRequired):
            r.resolve()

    def test_preset_issn_mismatch(self):
        self.cfg.issn = "0924-2716"
        with self.assertRaises(e.ActionRequired):
            self.runner().resolve()

    def test_structured_and_rawtext_and_missing_doi(self):
        doi = self.row["doi"]
        self.assertEqual(e.inspect_xml(xml(doi), doi)["status"], "fulltext_structured")
        self.assertEqual(e.inspect_xml(xml(doi, False), doi)["status"], "no_fulltext")
        self.assertEqual(e.inspect_xml(xml(doi), "10.other/wrong")["status"], "doi_mismatch")
        raw = xml(doi, False).replace(b"</full-text-retrieval-response>", b"<originalText><rawtext>Editorial board content</rawtext></originalText></full-text-retrieval-response>")
        self.assertEqual(e.inspect_xml(raw, doi)["status"], "fulltext_rawtext")
        no_id = xml(doi).replace(f"<prism:doi>{doi}</prism:doi>".encode(), b"")
        self.assertEqual(e.inspect_xml(no_id, doi)["status"], "needs_review_identity")

    def test_hash_mismatch_is_not_skipped(self):
        target, txt, _ = e.paths_for(self.base, self.row)
        e.save_fulltext(target, txt, xml(self.row["doi"]), self.row["doi"])
        self.assertIsNotNone(e.verify_saved(self.base, self.row))
        target.write_bytes(target.read_bytes().replace(b"Test content", b"Changed content"))
        self.assertIsNone(e.verify_saved(self.base, self.row))

    def test_legacy_file_adoption_and_changed_year(self):
        target, _, _ = e.paths_for(self.base, self.row)
        e.atomic_bytes(target, xml(self.row["doi"]))
        self.assertIsNotNone(e.verify_saved(self.base, self.row))
        self.assertTrue(e.sidecar_path(target).exists())
        self.assertIsNotNone(e.verify_saved(self.base, {**self.row, "year": 2025}))

    def test_legacy_folder_recognition(self):
        e.save_json(self.root / "index_summary.json", {"issn": "0034-4257"})
        self.assertEqual(e.library_path(self.root, e.PRESETS[0]), self.root)
        self.assertEqual(e.library_path(self.root, e.PRESETS[1]), self.root / "0924-2716")

    def test_legacy_index_reuse_without_network(self):
        e.save_json(self.base / "index_summary.json", {"issn": "0034-4257", "requested_years": [2024, 2027],
                    "records": 1, "queries": [{"exhausted": True}]})
        e.save_lines(e.state_dir(self.base) / "manifest.jsonl", [self.row])
        r = self.runner()
        r.base, r.journal = self.base, e.PRESETS[0]
        r.public_json = lambda url: self.fail("Complete legacy index must work offline")
        self.assertEqual(r.collect(), [self.row])

    def test_response_length_mismatch(self):
        with self.assertRaises(e.http.client.IncompleteRead):
            self.runner().read_response(Response(b"truncated", {"Content-Length": "999"}))

    def test_fetch_auth_stops_once_and_redacts(self):
        r = self.runner()
        error = urllib.error.HTTPError("https://api.elsevier.com", 403, "denied", {}, io.BytesIO(self.cfg.api_key.encode()))
        r.opener = Opener([error])
        data, info = r.fetch(self.row["doi"])
        self.assertIsNone(data)
        self.assertEqual(info["status"], "authentication_error")
        self.assertEqual(len(r.opener.requests), 1)
        self.assertNotIn(self.cfg.api_key, json.dumps(info))
        self.assertNotIn(self.cfg.api_key, r.opener.requests[0].full_url)

    def test_transient_fetch_retries(self):
        r = self.runner()
        r.opener = Opener([TimeoutError("temporary"), Response(xml(self.row["doi"]))])
        data, info = r.fetch(self.row["doi"])
        self.assertEqual(info["attempts"], 2)
        self.assertEqual(data, xml(self.row["doi"]))

    def test_throttling_honors_retry_after(self):
        r = self.runner()
        r.opener = Opener([urllib.error.HTTPError("https://api.elsevier.com", 429, "busy", {"Retry-After": "3600"}, io.BytesIO(b"busy"))])
        _, info = r.fetch(self.row["doi"])
        self.assertGreater(info["wait_until"], time.time() + 3590)

    def test_unknown_quota_requires_action(self):
        r = self.runner()
        r.opener = Opener([urllib.error.HTTPError("https://api.elsevier.com", 429, "quota", {"X-RateLimit-Remaining": "0"}, io.BytesIO(b"quota"))])
        _, info = r.fetch(self.row["doi"])
        self.assertIn("action_required", info)

    def test_bad_retry_after_and_deadline(self):
        for value in ("garbage", "nan", "inf", None):
            self.assertIsNone(e.retry_seconds(value))
            self.assertIsNone(e.quota_deadline(value))

    def test_redirect_does_not_forward_key(self):
        redirect = e.ElsevierRedirect()
        request = urllib.request.Request("https://api.elsevier.com/content/article/doi/test", headers={"X-ELS-APIKey": self.cfg.api_key})
        for target in ["https://elsewhere.example/", "http://api.elsevier.com/", "https://api.elsevier.com:8000/", "https://user@api.elsevier.com/"]:
            with self.subTest(target=target), self.assertRaises(e.ActionRequired):
                redirect.redirect_request(request, None, 302, "redirect", {}, target)

    def test_direct_opener_ignores_environment_proxy(self):
        with patch.dict(e.os.environ, {"https_proxy": "http://127.0.0.1:9"}):
            opener = e.elsevier_opener("direct")
            proxies = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
            self.assertFalse(proxies)

    def test_stop_interrupts_pause(self):
        control = e.Control()
        control.paused.set()
        ended = []
        def wait():
            try:
                control.wait(999)
            except e.Cancelled:
                ended.append(True)
        thread = threading.Thread(target=wait)
        thread.start()
        control.stop.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(ended, [True])

    def test_lock_prevents_two_downloaders(self):
        self.base.mkdir()
        with e.library_lock(self.base):
            with self.assertRaises(e.ActionRequired):
                with e.library_lock(self.base):
                    pass
        with e.library_lock(self.base):
            pass

    def test_full_download_resume_corrupt_and_secrets(self):
        cached_index(self.base, [self.row])
        r = self.runner()
        r.fetch = lambda doi: (xml(doi), {"http": 200})
        r.run()
        self.assertEqual(r.new_count, 1)
        resumed = self.runner()
        resumed.fetch = lambda doi: self.fail("Should skip verified file")
        resumed.run()
        self.assertEqual(resumed.reused, 1)
        target, _, _ = e.paths_for(self.base, self.row)
        target.write_bytes(b"<broken")
        repaired = self.runner()
        repaired.fetch = lambda doi: (xml(doi), {"http": 200})
        repaired.run()
        self.assertEqual(repaired.new_count, 1)
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.cfg.api_key.encode(), path.read_bytes())

    def test_ambiguous_not_counted_and_explicit_retry(self):
        cached_index(self.base, [self.row])
        r = self.runner()
        r.fetch = lambda doi: (xml(doi, False), {"http": 200})
        r.run()
        self.assertEqual(len(r.saved), 0)
        second = self.runner()
        second.fetch = lambda doi: self.fail("Should not repeatedly request metadata-only responses")
        second.run()
        self.cfg.retry_failed = True
        third = self.runner()
        third.fetch = lambda doi: (xml(doi), {"http": 200})
        third.run()
        self.assertEqual(third.new_count, 1)

    def test_permanent_failure_does_not_block_next_record(self):
        other = {**self.row, "doi": "10.1016/j.test.2024.999"}
        cached_index(self.base, [self.row, other])
        r = self.runner()
        r.fetch = lambda doi: (None, {"http": 404, "status": "http_error"}) if doi == self.row["doi"] else (xml(doi), {"http": 200})
        r.run()
        self.assertEqual(r.new_count, 1)
        self.assertEqual(len(r.failures), 1)

    def test_network_failure_recovers_next_pass(self):
        cached_index(self.base, [self.row])
        r = self.runner()
        responses = iter([(None, {"status": "network_error"}), (xml(self.row["doi"]), {"http": 200})])
        r.fetch = lambda doi: next(responses)
        r.run()
        self.assertEqual(r.new_count, 1)
        self.assertEqual(len(r.failures), 0)

    def test_index_collect_union_and_repeated_cursor(self):
        r = self.runner()
        r.base = self.base
        r.journal = e.PRESETS[0]
        item = {"DOI": self.row["doi"], "title": ["Test"], "published-print": {"date-parts": [[2024, 1]]}}
        pages = iter([{"items": [item], "total-results": 1, "next-cursor": "*"},
                      {"items": [], "total-results": 1},
                      {"items": [item], "total-results": 1, "next-cursor": "cursor"},
                      {"items": [], "total-results": 1}])
        r.public_json = lambda url: {"message": next(pages)}
        records = r.collect()
        self.assertEqual(len(records), 1)
        self.assertTrue(e.load_json(e.state_dir(self.base) / "indexes" / "2024-2024.json")["complete"])

    def test_index_bad_pagination_not_persisted(self):
        r = self.runner()
        r.base, r.journal = self.base, e.PRESETS[0]
        item = {"DOI": self.row["doi"], "published-print": {"date-parts": [[2024]]}}
        r.public_json = lambda url: {"message": {"items": [item], "total-results": 2, "next-cursor": "same"}}
        with self.assertRaises(e.ActionRequired):
            r.collect()
        self.assertFalse((e.state_dir(self.base) / "indexes" / "2024-2024.json").exists())

    def test_cancelled_scan_keeps_existing_xml(self):
        cached_index(self.base, [self.row])
        target, txt, _ = e.paths_for(self.base, self.row)
        e.save_fulltext(target, txt, xml(self.row["doi"]), self.row["doi"])
        r = self.runner()
        r.control.stop.set()
        with self.assertRaises(e.Cancelled):
            r.run()
        self.assertEqual(target.read_bytes(), xml(self.row["doi"]))


if __name__ == "__main__":
    unittest.main()
