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
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = patch("library_store.STATE_HOME", Path(tmp.name))
        state.start()
        self.addCleanup(state.stop)

    def quiet_app(self, settings=None):
        app = App(check_updates=False, remember_credentials=False, scan_libraries=False,
                  settings={} if settings is None else settings, visible=False)
        app.withdraw()
        def cleanup():
            app._cancel_update_notice()
            if app.worker and app.worker.is_alive():
                app.control.stop.set()
                app.worker.join(5)
            for callback in app.tk.call("after", "info"):
                app.tk.call("after", "cancel", callback)
            app.destroy()
            # Tcl-owned variable cycles must be finalized on the UI thread.
            import gc
            gc.collect()
        self.addCleanup(cleanup)
        return app

    def test_search_defaults_and_legacy_filter_migration(self):
        app = self.quiet_app({'title_filter': 'forest, crop', 'filter_mode': 'all'})
        self.assertEqual(app.search_scope.get(), '本地题名筛选')
        self.assertEqual(app.filter_mode.get(), '全部词组')
        app.search_scope.set('全文 · ScienceDirect')
        app.filter_mode.set('官网语法')
        app.search_scope.set('本地题名筛选')
        self.assertEqual(app.filter_mode.get(), '任一词组')

    def test_search_preview_runs_in_worker_and_never_downloads(self):
        from test_article_search import entry, response
        from test_engine import Opener
        app = self.quiet_app()
        self.assertEqual(app.search_scope.get(), '全文 · ScienceDirect')
        self.assertEqual(app.filter_mode.get(), '官网语法')
        with tempfile.TemporaryDirectory() as tmp, patch.object(controller, 'SETTINGS_DIR', Path(tmp) / 'settings'), \
             patch('engine.elsevier_opener', return_value=Opener([response([entry()])])), \
             patch.object(Downloader, 'fetch', side_effect=AssertionError('preview fetched fulltext')):
            app.key.set('SYNTHETIC_PREVIEW_KEY')
            app.start_year.set('2024')
            app.end_year.set('2024')
            app.output.set(tmp)
            app.title_filter.set('forest')
            app.start(preview=True)
            app.worker.join(5)
            self.assertFalse(app.worker.is_alive())
            until = time.monotonic() + 2
            while not app.events.empty() and time.monotonic() < until:
                app._poll()
            self.assertEqual(app.status.get(), '检索完成')
            self.assertEqual(app.metrics['total'].get(), '1')
            self.assertTrue(app.search_results_window.winfo_exists())
            self.assertFalse(list(Path(tmp).rglob('*.xml')))
            saved = json.loads((Path(tmp) / 'settings/settings.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['search_scope'], 'fulltext')
            self.assertEqual(saved['filter_mode'], 'native')

    def test_api_query_change_does_not_show_crossref_title_count(self):
        from article_search import collect_search
        from test_article_search import entry, response
        from test_engine import Opener
        app = self.quiet_app()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config('SYNTHETIC_PREVIEW_KEY', PRESETS[0].url, 2024, 2024, Path(tmp),
                         title_filter='forest', filter_mode='native', search_scope='fulltext')
            runner = Downloader(cfg)
            runner.initialize_library()
            runner.opener = Opener([response([entry()])])
            collect_search(runner)
            cached_index(runner.base, [{'doi': '10.1016/j.other', 'title': 'crop', 'year': 2024, 'date': '2024'}])
            app.output.set(tmp)
            app.start_year.set('2024')
            app.end_year.set('2024')
            app.scan_detect = False
            for query, expected in [('forest', '1'), ('crop', '待获取')]:
                app.title_filter.set(query)
                app._start_library_scan()
                app.scan_worker.join(5)
                until = time.monotonic() + 2
                while not app.events.empty() and time.monotonic() < until:
                    app._poll()
                self.assertEqual(app.metrics['total'].get(), expected)

    def test_pdf_footer_reports_complete_partial_empty_and_stopped_truthfully(self):
        app = self.quiet_app()
        app.active_job = "pdf"
        for total, saved, pending, stage, outcome, expected in (
                (2, 2, 0, "pdf_finished", "complete", "PDF 已生成完成 · 2 篇"),
                (2, 1, 1, "pdf_finished_with_pending", "complete", "1 篇待处理"),
                (0, 0, 0, "pdf_finished", "complete", "未找到可合成"),
                (2, 1, 1, "pdf", "stopped", "已停止")):
            with self.subTest(stage=stage, outcome=outcome):
                app.events.put({"kind": "counts", "total": total, "saved": saved, "reused": 0,
                                "pending": pending, "stage": stage})
                app.events.put({"kind": "done", "outcome": outcome, "text": "Test result"})
                app._poll()
                self.assertIn(expected, app.pdf_status.get())
                self.assertEqual(app.pdf_progress.get(), saved / max(1, total))
        footer = app.pdf_status.get()
        app.active_job = "xml"
        app.events.put({"kind": "counts", "total": 5, "saved": 3, "reused": 3, "pending": 2, "stage": "downloading"})
        app._poll()
        self.assertEqual(app.pdf_status.get(), footer)

    def test_offline_pdf_diagnostic_button_reports_result_without_changing_library(self):
        app = self.quiet_app()
        folder = app.output.get()
        app._pdf_dialog()
        app.pdf_window.withdraw()
        app.pdf_browser.set("C:/Synthetic Edge/msedge.exe")
        with tempfile.TemporaryDirectory() as tmp:
            report = str(Path(tmp) / "diagnostic.json")
            with patch("controller.filedialog.asksaveasfilename", return_value=report), \
                 patch("pdf_browser.diagnose") as diagnose:
                app.check_pdf_environment()
                app.worker.join(5)
                app._poll()
                diagnose.assert_called_once()
                self.assertEqual(diagnose.call_args.args[0], report)
                self.assertEqual(diagnose.call_args.kwargs["browser"], app.pdf_browser.get())
            self.assertEqual(app.status.get(), "PDF 环境检测通过")
            self.assertIn(report, app.detail.get())
            self.assertEqual(app.output.get(), folder)

    def test_folder_scan_restores_existing_library_and_ignores_stale_results(self):
        from test_inventory import identified_xml
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "moved-isprs"
            folder = library / "fulltext_xml/2024"
            folder.mkdir(parents=True)
            (folder / "article.xml").write_bytes(identified_xml("10.1016/j.demo.preview", PRESETS[1]))
            app = self.quiet_app()
            app.scan_libraries = True
            app._init_library_watch()
            app.url.set(PRESETS[0].url)
            app.start_year.set("2024")
            app.end_year.set("2024")
            app.output.set(str(folder))
            app.after_cancel(app.scan_after)
            app.scan_after = None
            app._start_library_scan()
            app.scan_worker.join(5)
            app._poll()
            self.assertFalse(app.scan_worker.is_alive())
            self.assertEqual(app.metrics["saved"].get(), "1")
            self.assertEqual(app.metrics["total"].get(), "待获取")
            self.assertIn("1 篇完整 XML", app.local_hint.get())
            self.assertEqual(app.preset.get(), "ISPRS")
            self.assertEqual(app.library, library)
            stale = app.scan_generation
            app.output.set(str(root / "new"))
            app.events.put({"kind": "library_preview", "generation": stale, "error": "stale error"})
            app._poll()
            self.assertNotIn("stale", app.local_hint.get())
            app._cancel_library_scan()

    def test_catalog_dialog_searches_locally_and_pages_without_network(self):
        app = self.quiet_app()
        app._catalog_dialog()
        app.catalog_window.withdraw()
        app.catalog_query.set("remote sensing")
        deadline = time.monotonic() + 2
        while not app.catalog_matches and time.monotonic() < deadline:
            app.update()
            time.sleep(0.02)
        self.assertIn("0034-4257", [item["issn"] for item in app.catalog_matches])
        self.assertIn("0924-2716", [item["issn"] for item in app.catalog_matches])
        app.catalog_window.destroy()

    def test_folder_pdf_status_reads_actual_files_and_clears_immediately(self):
        from test_pipeline import sample_pdf, article
        from engine import paths_for, save_fulltext, save_json
        from library_store import sidecar_path
        from pdf_export import RENDERER_VERSION
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / PRESETS[0].issn
            row = {'doi': '10.1016/j.example.2024.folder', 'year': 2024, 'title': 'Forest study', 'date': '2024'}
            cached_index(base, [row])
            source, txt, _ = paths_for(base, row)
            digest = save_fulltext(source, txt, article(row['doi']), row['doi'])
            target = base / 'fulltext_pdf/2024/example.pdf'
            target.parent.mkdir(parents=True)
            payload = sample_pdf(row['doi'])
            target.write_bytes(payload)
            save_json(sidecar_path(target), {'doi': row['doi'], 'xml_sha256': digest, 'pages': 1,
                'pdf_sha256': hashlib.sha256(payload).hexdigest(), 'layout': 'double', 'renderer': RENDERER_VERSION})
            app = self.quiet_app()
            app.scan_libraries = True
            app._init_library_watch()
            app.start_year.set('2024')
            app.end_year.set('2024')
            app.url.set(PRESETS[0].url)
            app.title_filter.set('')
            app.output_mode.set('XML + PDF')
            def scan(folder):
                app.output.set(str(folder))
                self.assertEqual(app.pdf_progress.get(), 0)
                self.assertIn('正在读取当前目录', app.pdf_status.get())
                app.after_cancel(app.scan_after)
                app._start_library_scan()
                app.scan_worker.join(5)
                self.assertFalse(app.scan_worker.is_alive())
                app._poll()
            scan(base)
            self.assertIn('PDF 1/1', app.pdf_status.get())
            self.assertEqual(app.pdf_progress.get(), 1)
            self.assertEqual(app.metrics['saved'].get(), '1')
            target.write_bytes(b'truncated')
            scan(base)
            self.assertEqual(app.pdf_progress.get(), 0)
            self.assertIn('待修复', app.pdf_status.get())
            self.assertEqual(app.metrics['saved'].get(), '0')
            scan(root / 'empty')
            self.assertIn('暂无 XML / PDF', app.pdf_status.get())
            self.assertEqual(app.pdf_progress.get(), 0)
            app._cancel_library_scan()

    def test_pipeline_pdf_counts_do_not_replace_download_limit_progress(self):
        app = self.quiet_app()
        app.active_job = 'xml'
        app.pipeline_pdf = True
        app.events.put({'kind': 'counts', 'total': 100, 'saved': 10, 'reused': 9, 'pending': 90,
                        'stage': 'limit_reached', 'max_downloads': 1, 'download_target': 1, 'downloaded_this_run': 1})
        app.events.put({'kind': 'pdf_counts', 'total': 100, 'saved': 10, 'pending': 90, 'stage': 'limit_reached'})
        app.events.put({'kind': 'done', 'outcome': 'complete', 'text': 'Done'})
        app._poll()
        self.assertEqual(app.progress.get(), 1)
        self.assertEqual(app.pdf_progress.get(), .1)
        self.assertEqual(app.metrics['saved'].get(), '10')
        self.assertIn('新增 1 篇', app.pdf_status.get())

    def test_new_options_persist_without_key(self):
        app = self.quiet_app()
        with tempfile.TemporaryDirectory() as temp, patch.object(controller, 'SETTINGS_DIR', Path(temp)):
            cfg = Config('SYNTHETIC_PRIVATE', PRESETS[0].url, 2024, 2024, Path(temp),
                         output_mode='pdf', title_filter='forest, deep learning', filter_mode='all',
                         pdf_browser='C:/Custom Edge/msedge.exe')
            app._save_settings(cfg)
            saved = json.loads((Path(temp) / 'settings.json').read_text(encoding='utf-8'))
            self.assertEqual((saved['output_mode'], saved['filter_mode'], saved['title_filter']), ('pdf', 'all', 'forest, deep learning'))
            self.assertNotIn('SYNTHETIC_PRIVATE', str(saved))
            self.assertEqual(saved['pdf_browser'], cfg.pdf_browser)

    def test_browser_picker_persists_without_credentials_and_reset_restores_auto(self):
        app = self.quiet_app()
        with tempfile.TemporaryDirectory() as temp, patch.object(controller, 'SETTINGS_DIR', Path(temp)):
            path = Path(temp) / 'msedge.exe'
            path.touch()
            with patch('controller.filedialog.askopenfilename', return_value=str(path)):
                app._choose_pdf_browser(app)
            self.assertEqual(app.pdf_browser.get(), str(path.resolve()))
            self.assertIn('Microsoft Edge', app.pdf_browser_caption.get())
            saved = json.loads((Path(temp) / 'settings.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['pdf_browser'], str(path.resolve()))
            self.assertNotIn('api_key', saved)
            app._set_pdf_browser('')
            self.assertIn('自动检测', app.pdf_browser_caption.get())
            self.assertEqual(json.loads((Path(temp) / 'settings.json').read_text(encoding='utf-8'))['pdf_browser'], '')

    def test_key_persists_across_restart_and_opt_out_removes_it(self):
        from credential_store import load_key, key_file
        def close(app):
            for callback in app.tk.call("after", "info"):
                app.tk.call("after", "cancel", callback)
            app.close()
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(controller, "SETTINGS_DIR", Path(temp)), \
             patch("ui.SETTINGS_DIR", Path(temp)):
            first = App(check_updates=False)
            first.withdraw()
            first.key.set("GUI_PRIVATE_SYNTHETIC_KEY")
            close(first)
            self.assertEqual(load_key(temp), "GUI_PRIVATE_SYNTHETIC_KEY")
            second = App(check_updates=False)
            second.withdraw()
            self.assertEqual(second.key.get(), "GUI_PRIVATE_SYNTHETIC_KEY")
            self.assertEqual(second.key_entry.cget("show"), "●")
            second.remember_key.set(False)
            second._persist_api_key()
            close(second)
            self.assertFalse(key_file(temp).exists())
            third = App(check_updates=False)
            third.withdraw()
            self.assertEqual(third.key.get(), "")
            self.assertFalse(third.remember_key.get())
            close(third)
            self.assertNotIn("PRIVATE_SYNTHETIC_KEY", (Path(temp) / "settings.json").read_text())

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
                    # UI polling has a time budget; a completed worker may leave
                    # several batches of progress before the terminal event.
                    deadline = time.monotonic() + 2
                    while not app.events.empty() and time.monotonic() < deadline:
                        app._poll()
                    self.assertTrue(app.events.empty())
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
        app = App(check_updates=False, remember_credentials=False, settings={}, scan_libraries=False)
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

        app = App(check_updates=False, remember_credentials=False, settings={}, scan_libraries=False)
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
            self.assertIn("本机应用数据", app.detail.get())
        finally:
            for callback in app.tk.call("after", "info"):
                app.tk.call("after", "cancel", callback)
            app.destroy()

    def test_worker_settings_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cached_index(root / PRESETS[0].issn, [{"doi": "10.1016/j.demo.2024.123456", "year": 2024,
                                                "date": "2024", "title": "Test article"}])
            app = App(check_updates=False, remember_credentials=False, settings={}, scan_libraries=False)
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
