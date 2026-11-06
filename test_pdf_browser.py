"""Cross-machine printing failures, fallback, cancellation and offline checks."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from engine import ActionRequired, Cancelled, Config, Control, Downloader, PRESETS, load_json
from pdf_browser import BrowserFailure, BrowserRenderer, PROBE_DOI, render_pdf
from test_pipeline import sample_pdf
from smoke_test import cached_index


class BrowserTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.renderer = BrowserRenderer(self.root / "diagnostic.json")

    def test_fallback_and_report_survive_temporary_directory_cleanup(self):
        def render(document, browser, work, control):
            if browser.name == "edge.exe":
                (work / "browser.log").write_text("DLL load failed token=secret")
                raise BrowserFailure("exited", -1073741515)
            return sample_pdf(PROBE_DOI)
        with patch("pdf_browser.browsers", return_value=[Path("edge.exe"), Path("chrome.exe")]), \
             patch("pdf_browser.render_pdf", side_effect=render):
            self.renderer.ensure_ready()
        report = load_json(self.renderer.report)
        self.assertTrue(report["ok"])
        self.assertEqual(report["selected_browser"], "chrome.exe")
        self.assertEqual(report["attempts"][0]["returncode"], -1073741515)
        self.assertIn("DLL load failed", report["attempts"][0]["stderr"])
        self.assertNotIn("secret", self.renderer.report.read_text())

    def test_failed_preflight_stops_before_article_api_request(self):
        library = self.root / PRESETS[0].issn
        cached_index(library, [{"doi": "10.1016/j.demo.a", "year": 2024, "title": "Example"}])
        cfg = Config("SYNTHETIC_SECRET", PRESETS[0].url, 2024, 2024, self.root, output_mode="pdf")
        with patch("library_store.STATE_HOME", self.root / "state"), \
             patch("pdf_browser.browsers", return_value=[Path("edge.exe")]), \
             patch("pdf_browser.render_pdf", side_effect=BrowserFailure("Cannot print")):
            runner = Downloader(cfg)
            runner.fetch = Mock(side_effect=AssertionError("Unexpected API request"))
            with self.assertRaisesRegex(ActionRequired, "诊断文件"):
                runner.run()
            runner.fetch.assert_not_called()
        self.assertNotIn("SYNTHETIC_SECRET", next((self.root / "state").rglob("pdf-diagnostics.json")).read_text())

    def test_cancel_never_falls_back(self):
        with patch("pdf_browser.browsers", return_value=[Path("a"), Path("b")]), \
             patch("pdf_browser.render_pdf", side_effect=Cancelled()) as render:
            with self.assertRaises(Cancelled):
                self.renderer.ensure_ready()
            self.assertEqual(render.call_count, 1)

    def test_runtime_system_failure_stops_but_bad_article_can_continue(self):
        self.renderer.selected = Path("edge")
        self.renderer.candidates = [Path("edge")]
        with patch("pdf_browser.render_pdf", side_effect=BrowserFailure("failed")) as render:
            with self.assertRaises(ActionRequired):
                self.renderer.render("bad document", "10.1016/example")
            self.assertEqual(render.call_count, 2)  # One article and one fresh offline probe.
        self.renderer.selected = Path("edge")
        with patch("pdf_browser.render_pdf", side_effect=[b"bad PDF", sample_pdf(PROBE_DOI)]):
            with self.assertRaisesRegex(ValueError, "本篇 PDF"):
                self.renderer.render("bad document", "10.1016/example")

    def test_runtime_falls_back_and_reuses_working_browser(self):
        self.renderer.selected = Path("edge")
        self.renderer.candidates = [Path("edge"), Path("chrome")]
        doi = "10.1016/example"
        with patch("pdf_browser.render_pdf", side_effect=[BrowserFailure("failed"), sample_pdf(doi), sample_pdf(doi)]) as render:
            self.renderer.render("document", doi)
            self.renderer.render("document", doi)
            self.assertEqual([call.args[1] for call in render.call_args_list], [Path("edge"), Path("chrome"), Path("chrome")])

    def test_launch_cleans_frozen_environment_and_uses_private_profile(self):
        control = Control()
        process = Mock(returncode=1)
        process.poll.return_value = 1
        with patch.dict(os.environ, {"_PYI_APPLICATION_HOME_DIR": "PRIVATE", "_MEIPASS2": "PRIVATE"}), \
             patch("pdf_browser.clean_dll_search_path") as clean, \
             patch("pdf_browser.subprocess.Popen", return_value=process) as popen:
            with self.assertRaisesRegex(BrowserFailure, "退出码 1"):
                render_pdf("<html>test</html>", Path("browser.exe"), self.root, control)
        args, kwargs = popen.call_args
        self.assertTrue(clean.called)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", kwargs["env"])
        self.assertNotIn("_MEIPASS2", kwargs["env"])
        self.assertEqual(kwargs["cwd"], self.root.resolve())
        self.assertIn("--user-data-dir=" + str(self.root.resolve() / "browser-profile"), args[0])
        self.assertNotIn("--no-sandbox", args[0])

    def test_cancel_only_terminates_launched_pid(self):
        control = Control()
        control.stop.set()
        process = Mock(pid=24680)
        process.poll.return_value = None
        with patch("pdf_browser.subprocess.Popen", return_value=process), \
             patch("pdf_browser.subprocess.run") as kill:
            with self.assertRaises(Cancelled):
                render_pdf("test", Path("browser"), self.root, control)
        if os.name == "nt":
            self.assertEqual(kill.call_args.args[0], ["taskkill", "/PID", "24680", "/T", "/F"])

    def test_no_browser_has_readable_report(self):
        with patch("pdf_browser.browsers", return_value=[]):
            with self.assertRaisesRegex(ActionRequired, "未找到"):
                self.renderer.ensure_ready()
        self.assertFalse(load_json(self.renderer.report)["ok"])

    def test_bundle_path_is_removed_and_external_paths_preserved(self):
        import sys
        from app_runtime import independent_environment
        bundle = self.root / '_internal'
        outside = self.root / 'System32'
        with patch.object(sys, '_MEIPASS', str(bundle), create=True), \
             patch.dict(os.environ, {'PATH': os.pathsep.join([str(bundle), str(bundle / 'extra'), str(outside)])}):
            self.assertEqual(independent_environment()['PATH'], str(outside))


if __name__ == "__main__":
    unittest.main()
