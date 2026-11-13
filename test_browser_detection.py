"""Edge-only, custom installations and browser-independent XML downloads."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from browser_detection import browsers, executable_path, selected_browser
from engine import Config, Downloader, PRESETS, load_json
from pdf_browser import BrowserRenderer, PROBE_DOI
from smoke_test import offline_search_page, cached_index, xml
from test_pipeline import sample_pdf


class BrowserDetectionTests(unittest.TestCase):
    def setUp(self):
        search = patch('article_search.request_page', side_effect=offline_search_page)
        search.start()
        self.addCleanup(search.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.environment = {name: str(self.root / name) for name in
            ('PROGRAMFILES(X86)', 'PROGRAMW6432', 'PROGRAMFILES', 'LOCALAPPDATA')}
        env = patch.dict(os.environ, self.environment)
        env.start()
        self.addCleanup(env.stop)
        for target in ('browser_detection.registry_paths', 'browser_detection.shutil.which'):
            fake = patch(target, return_value=[] if target.endswith('registry_paths') else None)
            fake.start()
            self.addCleanup(fake.stop)

    def installed(self, root, relative):
        path = Path(self.environment[root]) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path.resolve()

    def test_only_edge_installed_under_each_windows_root(self):
        for root in self.environment:
            with self.subTest(root=root):
                edge = self.installed(root, 'Microsoft/Edge/Application/msedge.exe')
                self.assertEqual(browsers(), [edge])
                edge.unlink()

    def test_edge_has_priority_even_when_chrome_is_under_earlier_root(self):
        chrome = self.installed('PROGRAMFILES(X86)', 'Google/Chrome/Application/chrome.exe')
        edge = self.installed('PROGRAMFILES', 'Microsoft/Edge/Application/msedge.exe')
        with patch('browser_detection.registry_paths', return_value=[str(edge), str(chrome)]):
            self.assertEqual(browsers(), [edge, chrome])

    def test_registry_expands_variables_parses_command_and_ignores_unrelated_clients(self):
        edge = self.root / '中文 Edge' / 'msedge.exe'
        edge.parent.mkdir()
        edge.touch()
        unrelated = self.root / 'unknown.exe'
        unrelated.touch()
        with patch.dict(os.environ, {'JOURNAL_TEST_EDGE': str(edge.parent)}), \
             patch('browser_detection.registry_paths', return_value=[
                 '"%JOURNAL_TEST_EDGE%/msedge.exe" --single-argument %1', str(edge), str(unrelated)]):
            self.assertEqual(browsers(), [edge.resolve()])
        self.assertEqual(executable_path(f'{edge} --single-argument %1'), edge)

    def test_manual_choice_is_exclusive_and_missing_or_unrelated_program_rejected(self):
        edge = self.installed('LOCALAPPDATA', 'Custom Edge/msedge.exe')
        self.installed('PROGRAMFILES', 'Google/Chrome/Application/chrome.exe')
        with patch('browser_detection.registry_paths', side_effect=AssertionError('No auto discovery')):
            self.assertEqual(browsers(str(edge)), [edge])
        for path in (self.root / 'missing/msedge.exe', self.root / 'other.exe', Path('msedge.exe')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                selected_browser(path)

    def test_edge_only_preflight_and_render_never_attempt_chrome(self):
        edge = self.installed('LOCALAPPDATA', 'Microsoft/Edge/Application/msedge.exe')
        report = self.root / 'report.json'
        renderer = BrowserRenderer(report)
        with patch('pdf_browser.render_pdf', return_value=sample_pdf(PROBE_DOI)) as render:
            renderer.render('test document', PROBE_DOI)
        self.assertEqual([call.args[1] for call in render.call_args_list], [edge, edge])
        self.assertEqual(load_json(report)['selected_browser'], str(edge))

    def test_invalid_manual_path_is_diagnosed_without_launching_other_browser(self):
        from engine import ActionRequired
        report = self.root / 'report.json'
        renderer = BrowserRenderer(report, browser=str(self.root / 'missing/msedge.exe'))
        with patch('pdf_browser.render_pdf') as render, self.assertRaises(ActionRequired):
            renderer.ensure_ready()
        render.assert_not_called()
        self.assertFalse(load_json(report)['ok'])
        self.assertIn('不存在', load_json(report)['error'])

    def test_xml_download_and_resume_require_no_browser_even_with_stale_selection(self):
        doi = '10.1016/j.demo.edge-only'
        cfg = Config('SYNTHETIC_KEY', PRESETS[0].url, 2024, 2024, self.root,
                     pdf_browser=str(self.root / 'missing/msedge.exe'))
        base = self.root / PRESETS[0].issn
        cached_index(base, [{'doi': doi, 'year': 2024, 'title': 'Offline test'}])
        with patch('library_store.STATE_HOME', self.root / 'state'), \
             patch('pdf_browser.browsers', side_effect=AssertionError('XML must not need a browser')):
            runner = Downloader(cfg)
            runner.fetch = Mock(return_value=(xml(doi), {'http': 200}))
            runner.run()
            self.assertEqual(runner.new_count, 1)
            retry = Downloader(cfg)
            retry.fetch = Mock(side_effect=AssertionError('Must reuse saved XML'))
            retry.run()
            self.assertEqual(retry.reused, 1)


if __name__ == '__main__':
    unittest.main()
