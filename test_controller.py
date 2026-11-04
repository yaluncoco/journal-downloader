"""Exercise the real GUI controller with a deterministic in-process backend."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import controller
from engine import Downloader, Config, PRESETS
from smoke_test import cached_index, xml
from ui import App
from pdf_export import PdfConfig
from updater import Release


class ControllerTests(unittest.TestCase):
    def test_update_dialog_shows_available_release(self):
        app = App()
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

        app = App()
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
            app = App()
            app.withdraw()
            app.key.set("CONTROLLER_PRIVATE_TEST_KEY")
            app.url.set(PRESETS[0].url)
            app.start_year.set("2024")
            app.end_year.set("2024")
            app.output.set(str(root))
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
