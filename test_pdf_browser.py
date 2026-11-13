"""Cross-machine printing failures, fallback, cancellation and offline checks."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from engine import ActionRequired, Cancelled, Config, Control, Downloader, PRESETS, load_json
from pdf_browser import BrowserFailure, BrowserRenderer, PROBE_DOI, render_pdf
from test_pipeline import sample_pdf
from smoke_test import offline_search_page, cached_index


class BrowserTests(unittest.TestCase):
    def setUp(self):
        search = patch('article_search.request_page', side_effect=offline_search_page)
        search.start()
        self.addCleanup(search.stop)
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
        cfg = Config("SYNTHETIC_SECRET", PRESETS[0].url, 2024, 2024, self.root, output_mode="pdf", pdf_method='browser')
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
        self.assertFalse(load_json(self.renderer.report)["ok"], "A good probe cannot certify a failed article")

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
        process = Mock(returncode=1, pid=12345)
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
        self.assertIn("--enable-logging", args[0])
        self.assertEqual(kwargs["env"]["CHROME_LOG_FILE"], str(self.root.resolve() / "browser-debug.log"))
        self.assertNotIn("--no-sandbox", args[0])

    def _delayed_render(self, action, returncode=0):
        clock = [0.0]
        control = Control()
        process = Mock(returncode=returncode, pid=12345)
        process.poll.return_value = returncode
        def advance(delay):
            clock[0] = round(clock[0] + delay, 5)
            action(clock[0], control)
        with patch('pdf_browser.subprocess.Popen', return_value=process), \
             patch('pdf_browser.time.monotonic', side_effect=lambda: clock[0]), \
             patch.object(control.stop, 'wait', side_effect=advance):
            return render_pdf('test document', Path('msedge.exe'), self.root, control)

    def test_zero_exit_waits_for_handed_off_browser_to_finish_pdf(self):
        payload = sample_pdf(PROBE_DOI)
        def later(now, control):
            if now == .4:
                (self.root / 'article.pdf').write_bytes(payload)
        self.assertEqual(self._delayed_render(later), payload)
        details = load_json(self.root / 'launch.json')
        self.assertTrue(details['launcher_exited_before_pdf'])
        self.assertEqual(details['launcher_returncode'], 0)
        self.assertGreaterEqual(details['elapsed_seconds'], .7)

    def test_pdf_is_not_returned_before_a_late_writer_finishes(self):
        payload = sample_pdf(PROBE_DOI)
        def partial(now, control):
            if now == .1:
                (self.root / 'article.pdf').write_bytes(payload[:100])
            elif now == .7:
                (self.root / 'article.pdf').write_bytes(payload)
        self.assertEqual(self._delayed_render(partial), payload)
        self.assertGreaterEqual(load_json(self.root / 'launch.json')['elapsed_seconds'], 1.0)

    def test_zero_exit_without_pdf_still_fails_after_bounded_wait(self):
        with patch('pdf_browser.OUTPUT_GRACE_SECONDS', .6):
            with self.assertRaisesRegex(BrowserFailure, '退出码 0'):
                self._delayed_render(lambda *args: None)
        details = load_json(self.root / 'launch.json')
        self.assertFalse(details['pdf_exists'])
        self.assertEqual(details['elapsed_seconds'], .6)

    def test_cancel_during_post_exit_wait_is_immediate(self):
        def cancel(now, control):
            if now == .2:
                control.stop.set()
        with patch('pdf_browser.subprocess.run') as kill, self.assertRaises(Cancelled):
            self._delayed_render(cancel)
        kill.assert_not_called()  # The launcher has exited; never kill by browser name.
        self.assertEqual(load_json(self.root / 'launch.json')['elapsed_seconds'], .2)

    def test_diagnostics_keep_debug_log_and_launch_details_after_cleanup(self):
        def render(document, browser, work, control):
            (work / 'browser-debug.log').write_text('Synthetic launch failure token=private')
            from engine import save_json
            save_json(work / 'launch.json', {'launcher_returncode': 0, 'elapsed_seconds': 10})
            raise BrowserFailure('No output', 0)
        with patch('pdf_browser.browsers', return_value=[Path('msedge.exe')]), \
             patch('pdf_browser.configured_policies', return_value=[]), \
             patch('pdf_browser.render_pdf', side_effect=render):
            with self.assertRaises(ActionRequired):
                self.renderer.ensure_ready()
        report = load_json(self.renderer.report)
        attempt = report['attempts'][0]
        self.assertEqual(attempt['launch']['launcher_returncode'], 0)
        self.assertIn('Synthetic launch failure', attempt['browser_debug'])
        self.assertNotIn('private', self.renderer.report.read_text())
        self.assertFalse(Path(attempt['work_directory']).exists())
        self.assertIn('process_elevated', report)

    def test_managed_profile_is_not_opened_and_auto_mode_can_use_other_browser(self):
        policies = [{'hive': 'HKLM', 'view': 64, 'name': 'UserDataDir', 'value': 'Managed browser profile'}]
        with patch('pdf_browser.browsers', return_value=[Path('msedge.exe'), Path('chrome.exe')]), \
             patch('pdf_browser.configured_policies', side_effect=[policies, []]), \
             patch('pdf_browser.render_pdf', return_value=sample_pdf(PROBE_DOI)) as render:
            self.renderer.ensure_ready()
        self.assertEqual(render.call_count, 1)
        self.assertEqual(render.call_args.args[1], Path('chrome.exe'))
        self.assertIn('UserDataDir', load_json(self.renderer.report)['attempts'][0]['error'])

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

    def test_workspace_creation_failure_is_recorded_instead_of_escaping_diagnostics(self):
        with patch("pdf_browser.browsers", return_value=[Path("edge.exe")]), \
             patch("pdf_browser.print_workspace", side_effect=OSError("Temporary directory is unavailable")):
            with self.assertRaises(ActionRequired):
                self.renderer.ensure_ready()
        report = load_json(self.renderer.report)
        self.assertFalse(report['ok'])
        self.assertIn('Temporary directory is unavailable', report['attempts'][0]['error'])

    def test_long_and_unwritable_temp_roots_fall_back_without_permissions_change(self):
        from pdf_workspace import print_workspace
        blocked = self.root / 'file-not-directory'
        blocked.write_text('keep')
        long_root = self.root / ('long-' * 45)
        fallback = self.root / 'short'
        with patch('pdf_workspace.workspace_roots', return_value=[long_root, blocked, fallback]):
            with print_workspace() as work:
                self.assertEqual(work.parent, fallback.resolve())
                (work / 'test.html').write_text('test')
            self.assertFalse(work.exists())
        self.assertEqual(blocked.read_text(), 'keep')


if __name__ == "__main__":
    unittest.main()
