"""Exercise the real GUI controller with a deterministic in-process backend."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import controller
from engine import Downloader, Config, Control, PRESETS, VERSION
from smoke_test import cached_index, xml
from ui import App
from pdf_export import PdfConfig
from updater import Release


class ControllerTests(unittest.TestCase):
    def quiet_app(self):
        app = App(check_updates=False)
        app.withdraw()
        def cleanup():
            app._cancel_update_notice()
            if app.worker and app.worker.is_alive():
                app.control.stop.set()
                app.worker.join(5)
            for callback in app.tk.call("after", "info"):
                app.tk.call("after", "cancel", callback)
            app.destroy()
        self.addCleanup(cleanup)
        return app

    def test_silent_update_check_runs_once_without_changing_download_state(self):
        app = self.quiet_app()
        control = app.control = Control()
        app._busy(True)
        app.status.set("正在下载")
        app.detail.set("当前论文")
        with patch("update_ui.Updater") as client:
            client.return_value.latest.return_value = Release("v99.0.0", "", "", "", 100000, "")
            app._start_update_notice()
            app.update_notice_worker.join(5)
            app._start_update_notice()
            app._poll()
            self.assertEqual(client.return_value.latest.call_count, 1)
        self.assertTrue(app.update_available)
        self.assertEqual(app.update_badge.winfo_manager(), "place")
        self.assertEqual(app.status.get(), "正在下载")
        self.assertEqual(app.detail.get(), "当前论文")
        self.assertIs(app.control, control)
        self.assertIsNone(app.worker)
        self.assertEqual(app.start_button.cget("state"), "disabled")

    def test_failed_silent_check_does_not_clear_known_update(self):
        app = self.quiet_app()
        app._set_update_available(Release("v99.0.0", "", "", "", 100000, ""))
        status = app.status.get()
        with patch("update_ui.Updater") as client:
            client.return_value.latest.side_effect = OSError("offline")
            app._start_update_notice()
            app.update_notice_worker.join(5)
            app._poll()
        self.assertTrue(app.update_available)
        self.assertEqual(app.status.get(), status)

    def test_manual_check_supersedes_silent_result_and_clears_badge(self):
        app = self.quiet_app()
        app._set_update_available(Release("v99.0.0", "", "", "", 100000, ""))
        stale_generation = getattr(app, "update_notice_generation", 0)
        with patch("update_ui.Updater") as client:
            client.return_value.latest.return_value = Release("v" + VERSION, "", "", "", 100000, "")
            app._update_dialog()
            app.update_window.withdraw()
            app.worker.join(5)
            app._poll()
        self.assertFalse(app.update_available)
        self.assertEqual(app.update_badge.winfo_manager(), "")
        app.events.put({"kind": "update_notice", "generation": stale_generation,
                        "release": Release("v99.0.0", "", "", "", 100000, "")})
        app._poll()
        self.assertFalse(app.update_available)
        self.assertEqual(app.status.get(), "已是最新版本")
        app._close_update()

    def test_limit_is_saved_and_task_progress_reaches_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [{"doi": f"10.1016/j.demo.2024.limit{i}", "year": 2024, "date": "2024", "title": "Test"}
                    for i in range(3)]
            cached_index(root / PRESETS[0].issn, rows)
            app = self.quiet_app()
            app.key.set("LIMIT_PRIVATE_TEST_KEY")
            app.url.set(PRESETS[0].url)
            app.start_year.set("2024")
            app.end_year.set("2024")
            app.max_downloads.set("1")
            app.output.set(str(root))
            with patch.object(controller, "SETTINGS_DIR", root / "settings"), \
                 patch.object(Downloader, "fetch", lambda self, doi: (xml(doi), {"http": 200})):
                for saved, reused in ((1, 0), (2, 1)):
                    app.start()
                    app.worker.join(5)
                    self.assertFalse(app.worker.is_alive())
                    app._poll()
                    self.assertEqual(app.status.get(), "已达到本次数量")
                    self.assertEqual(app.progress.get(), 1)
                    self.assertIn("1/1", app.percent.get())
                    self.assertEqual(app.metrics["saved"].get(), str(saved))
                    self.assertEqual(app.metrics["reused"].get(), str(reused))
            settings = json.loads((root / "settings/settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["max_downloads"], 1)
            self.assertNotIn("PRIVATE_TEST_KEY", json.dumps(settings))

    def test_invalid_limit_does_not_start_worker(self):
        app = self.quiet_app()
        app.key.set("TEST_PRIVATE_KEY")
        app.start_year.set("2024")
        app.end_year.set("2024")
        app.url.set(PRESETS[0].url)
        app.output.set(str(Path.cwd()))
        for value in ("", "-1", "1.5", "abc"):
            with self.subTest(value=value), patch("controller.messagebox.showerror") as error:
                app.max_downloads.set(value)
                app.start()
                error.assert_called_once()
                self.assertIsNone(app.worker)

    def test_update_dialog_shows_available_release(self):
        app = App(check_updates=False)
        app.withdraw()
        release = Release("v99.0.0", "", "", "", 100000, "")
        try:
            with patch("update_ui.Updater") as client:
                client.return_value.latest.return_value = release
                app._update_dialog()
                app.update_window.withdraw()
                app.worker.join(5)
                app._poll()
            self.assertEqual(app.status.get(), "发现新版本")
            self.assertIn("v99.0.0", app.update_message.get())
            self.assertEqual(app.update_action.cget("state"), "normal")
            self.assertEqual(app.update_button.cget("state"), "normal")
            self.assertTrue(app.update_available)
            self.assertEqual(app.update_badge.winfo_manager(), "place")
            app._close_update()
        finally:
            for callback in app.tk.call("after", "info"):
                app.tk.call("after", "cancel", callback)
            app.destroy()

    def test_pdf_worker_completion_and_pending(self):
        class FakeExporter:
            def __init__(self, cfg, emit, control):
                self.key, self.emit = cfg.api_key, emit

            def run(self):
                self.emit(kind="counts", total=2, saved=1, reused=1, pending=1,
                          stage="pdf_finished_with_pending")

        app = App(check_updates=False)
        app.withdraw()
        try:
            app.active_job = "pdf"
            cfg = PdfConfig(Path.cwd(), api_key="PRIVATE_PDF_TEST_KEY")
            app._pdf_work(cfg, FakeExporter)
            app._poll()
            self.assertEqual(app.status.get(), "合成结束 · 部分待处理")
            self.assertEqual(app.metrics["saved"].get(), "1")
            self.assertEqual(cfg.api_key, "")
            self.assertEqual(app.pdf_button.cget("state"), "normal")
            self.assertIn("pdf_pending.jsonl", app.detail.get())
        finally:
            for callback in app.tk.call("after", "info"):
                app.tk.call("after", "cancel", callback)
            app.destroy()

    def test_worker_settings_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cached_index(root / PRESETS[0].issn, [{"doi": "10.1016/j.demo.2024.123456", "year": 2024,
                                                "date": "2024", "title": "Test article"}])
            app = App(check_updates=False)
            app.withdraw()
            app.key.set("CONTROLLER_PRIVATE_TEST_KEY")
            app.url.set(PRESETS[0].url)
            app.start_year.set("2024")
            app.end_year.set("2024")
            app.output.set(str(root))
            app.max_downloads.set("0")
            try:
                with patch.object(controller, "SETTINGS_DIR", root / "settings"), \
                     patch.object(Downloader, "fetch", lambda self, doi: (xml(doi), {"http": 200})):
                    app.start()
                    deadline = time.monotonic() + 5
                    while app.worker.is_alive() and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.01)
                    self.assertFalse(app.worker.is_alive())
                    app._poll()
                    self.assertEqual(app.status.get(), "下载完成")
                    self.assertEqual(app.metrics["saved"].get(), "1")
                    self.assertEqual(app.progress.get(), 1)
                    self.assertEqual(app.start_button.cget("state"), "normal")
                    saved = (root / "settings" / "settings.json").read_text(encoding="utf-8")
                    self.assertNotIn("PRIVATE_TEST_KEY", saved)
                    self.assertNotIn("api_key", saved)
                    app.start()
                    app.worker.join(5)
                    app._poll()
                    self.assertEqual(app.metrics["reused"].get(), "1")
            finally:
                if app.worker and app.worker.is_alive():
                    app.control.stop.set()
                    app.worker.join(5)
                for callback in app.tk.call("after", "info"):
                    app.tk.call("after", "cancel", callback)
                app.destroy()


if __name__ == "__main__":
    unittest.main()
