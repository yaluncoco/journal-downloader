"""Regression checks for font restrictions, event load and inventory contention."""
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from engine import PRESETS, save_json
from library_inventory import read_xml, scan_existing
from library_store import sidecar_path
from pdf_inventory import scan_pdfs
from preview_cache import PreviewCache
from test_inventory import identified_xml
from test_pipeline import sample_pdf
from ui import App, ctk


class PreviewCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        state = patch('library_store.STATE_HOME', self.root / 'state')
        state.start()
        self.addCleanup(state.stop)

    def test_unchanged_previews_reuse_parse_but_download_scan_always_revalidates(self):
        folder = self.root / 'fulltext_xml/2024'
        folder.mkdir(parents=True)
        path = folder / 'example.xml'
        path.write_bytes(identified_xml('10.1016/j.example.cache'))
        cache = PreviewCache()
        with patch('library_inventory.read_xml', wraps=read_xml) as reader:
            for _ in range(3):
                self.assertEqual(len(scan_existing(self.root, preview_cache=cache)['saved']), 1)
            self.assertEqual(reader.call_count, 1)
            scan_existing(self.root)
            self.assertEqual(reader.call_count, 2)
            # The remembered checksum is part of the cache identity.
            record = sidecar_path(path)
            record.parent.mkdir(parents=True)
            record.write_text('incorrect')
            result = scan_existing(self.root, preview_cache=cache)
            self.assertEqual((len(result['saved']), result['invalid']), (0, 1))

    def test_modified_pdf_and_changed_metadata_invalidate_preview(self):
        from pdf_inventory import read_pdf
        path = self.root / 'fulltext_pdf/2024/test.pdf'
        path.parent.mkdir(parents=True)
        doi = '10.1016/j.example.cache'
        payload = sample_pdf(doi)
        path.write_bytes(payload)
        meta = {'doi': doi, 'pdf_sha256': hashlib.sha256(payload).hexdigest(), 'pages': 1}
        save_json(sidecar_path(path), meta)
        cache = PreviewCache()
        with patch('pdf_inventory.read_pdf', wraps=read_pdf) as reader:
            for _ in range(2):
                self.assertEqual(len(scan_pdfs(self.root, preview_cache=cache)['saved']), 1)
            self.assertEqual(reader.call_count, 1)
            meta['pages'] = 2
            save_json(sidecar_path(path), meta)
            self.assertEqual(scan_pdfs(self.root, preview_cache=cache)['invalid'], 1)
            self.assertEqual(reader.call_count, 2)
            meta['pages'] = 1
            save_json(sidecar_path(path), meta)
            self.assertEqual(len(scan_pdfs(self.root, preview_cache=cache)['saved']), 1)
            path.write_bytes(b'truncated')
            self.assertEqual(scan_pdfs(self.root, preview_cache=cache)['invalid'], 1)

    def test_changed_during_read_is_not_cached_and_cache_is_bounded(self):
        cache = PreviewCache(limit=2)
        path = self.root / 'moving.xml'
        path.write_text('one')
        def changing(file):
            file.write_text('changed')
            return {'test': 'changed during validation'}
        cache.read(path, changing)
        self.assertFalse(cache.entries)
        for i in range(3):
            item = self.root / f'{i}.xml'
            item.write_text('synthetic')
            cache.read(item, lambda p: {'path': str(p)})
        self.assertEqual(len(cache.entries), 2)


class UICompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state = patch('library_store.STATE_HOME', self.root / 'state')
        state.start()
        self.addCleanup(state.stop)
        self.app = App(check_updates=False, remember_credentials=False, scan_libraries=False,
                       settings={}, visible=False)
        self.errors = []
        self.app.report_callback_exception = lambda *args: self.errors.append(str(args[1]))

    def tearDown(self):
        app = self.app
        app._cancel_library_scan()
        if getattr(app, 'scan_worker', None):
            app.scan_worker.join(3)
        for handle in app.tk.call('after', 'info'):
            app.tk.call('after', 'cancel', handle)
        app.destroy()
        gc.collect()
        self.assertEqual(self.errors, [])

    def spin(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_windowed_font_failure_starts_without_private_glyphs(self):
        report = self.root / 'no-font.json'
        code = '''import ctypes, json, sys
from pathlib import Path
ctypes.windll.gdi32.AddFontResourceExW = lambda *args: 0
sys.stdout = sys.stderr = None
from ui import App, ctk
app = App(check_updates=False, remember_credentials=False, scan_libraries=False, settings={}, visible=False)
app.update()
def count(widget):
    own = len(widget.find_withtag('ctk_aa_circle_font_element')) if isinstance(widget, ctk.CTkCanvas) else 0
    return own + sum(count(child) for child in widget.winfo_children())
Path(sys.argv[1]).write_text(json.dumps({'drawing': ctk.DrawEngine.preferred_drawing_method, 'glyphs': count(app)}))
for timer in app.tk.call('after','info'): app.tk.call('after','cancel',timer)
app.destroy()
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, str(report)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(report.read_text()), {'drawing': 'polygon_shapes', 'glyphs': 0})

    def test_progress_burst_yields_to_input_and_keeps_terminal_event_order(self):
        app = self.app
        for i in range(500):
            app.events.put({'kind': 'counts', 'total': 500, 'saved': i, 'reused': 0,
                            'pending': 500-i, 'stage': 'downloading'})
        app.events.put({'kind': 'done', 'outcome': 'stopped', 'text': 'Finished synthetic burst'})
        app._poll()
        self.assertFalse(app.events.empty(), 'One UI callback must not drain a long redraw burst')
        heartbeat = []
        app.after(0, lambda: heartbeat.append(True))
        self.spin(lambda: bool(heartbeat))
        self.spin(lambda: app.events.empty())
        self.assertEqual(app.metrics['saved'].get(), '499')
        self.assertEqual(app.status.get(), '已停止')

    def test_log_batch_redacts_key_and_bounds_text(self):
        self.app.key.set('SYNTHETIC_PRIVATE_KEY')
        self.app._log_batch([f'{i}: SYNTHETIC_PRIVATE_KEY' for i in range(2000)])
        value = self.app.logbox.get('1.0', 'end')
        self.assertNotIn('SYNTHETIC_PRIVATE_KEY', value)
        self.assertIn('1999: [已隐藏]', value)
        self.assertLess(len(value.splitlines()), 805)

    def test_directory_changes_never_overlap_inventory_workers(self):
        app = self.app
        entered, release = threading.Event(), threading.Event()
        calls = []
        def scan(base, start, end, checkpoint, **kwargs):
            calls.append(str(base))
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            checkpoint()
            return {'saved': {}, 'invalid': 0, 'base': str(base)}
        app.scan_libraries = True
        app._init_library_watch()
        app.output.set(str(self.root / 'first'))
        with patch('library_inventory.scan_existing', side_effect=scan), \
             patch('pdf_inventory.scan_pdfs', return_value={'saved': {}, 'invalid': 0, 'invalid_dois': set(), 'unverified': 0}):
            app.after_cancel(app.scan_after)
            app._start_library_scan()
            self.assertTrue(entered.wait(3))
            first = app.scan_worker
            try:
                for i in range(10):
                    app.output.set(str(self.root / f'next-{i}'))
                    app.after_cancel(app.scan_after)
                    app._start_library_scan()
                    self.assertIs(app.scan_worker, first)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
            first.join(3)
            self.spin(lambda: app.library is not None)
            self.assertEqual(len(calls), 2)
            self.assertEqual(app.library, self.root / 'next-9' / PRESETS[0].issn)

    def test_catalog_reuses_cards_and_closes_with_pending_search(self):
        app = self.app
        app._catalog_dialog()
        app.catalog_window.attributes('-alpha', 0)
        def cards():
            return [w for w in app.catalog_results.winfo_children() if isinstance(w, ctk.CTkFrame)]
        self.spin(lambda: len(cards()) == 30)
        original = tuple(cards())
        app.catalog_query.set('remote sensing')
        self.spin(lambda: len(app.catalog_matches) == 7)
        app.catalog_query.set('computer and')
        self.spin(lambda: app.catalog_matches and app.catalog_matches[0]['name'] == 'Computers and Composition')
        self.assertEqual(tuple(cards()), original)
        app.catalog_query.set('a pending final query')
        app.catalog_window.destroy()
        # Pending render/search timers must not touch destroyed Tk widgets.
        deadline = time.monotonic() + .3
        self.spin(lambda: time.monotonic() > deadline)


if __name__ == '__main__':
    unittest.main()
