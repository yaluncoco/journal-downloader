"""Output integrity/order regressions, with real parseable synthetic PDFs."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from article_filter import filter_records
from engine import Config, Downloader, PRESETS, Cancelled, ActionRequired, paths_for, save_fulltext, load_json, save_json
from library_store import sidecar_path, state_dir
from pdf_export import PdfExporter, RENDERER_VERSION, validate_pdf
from pdf_inventory import scan_pdfs
from smoke_test import cached_index
from test_pdf import fixture, DOI, NoWait


def sample_pdf(doi):
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(f'BT /F1 12 Tf 50 790 Td ({doi}) Tj ET'.encode())
    page[NameObject('/Contents')] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def article(doi):
    return fixture().replace(DOI.encode(), doi.encode())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        state = patch('library_store.STATE_HOME', self.root / 'app-state')
        state.start()
        self.addCleanup(state.stop)
        self.cfg = Config('SYNTHETIC_KEY', PRESETS[0].url, 2024, 2024, self.root, interval=0, output_mode='xml_pdf')
        self.base = self.root / PRESETS[0].issn
        self.rows = [{'doi': f'10.1016/j.example.2024.{i:06}', 'year': 2024, 'date': '2024-01-01',
                      'title': title} for i, title in enumerate(('Forest deep learning', 'Crop simulation', 'Forest monitoring'))]
        cached_index(self.base, self.rows)
        self.order = []
        browser = patch('pdf_export.BrowserRenderer.ensure_ready')
        browser.start()
        self.addCleanup(browser.stop)
        self.render_patch = patch('pdf_export.BrowserRenderer.render', side_effect=self.render)
        self.render_mock = self.render_patch.start()
        self.addCleanup(self.render_patch.stop)

    def render(self, document, doi):
        doi = next(row['doi'] for row in self.rows if row['doi'] in document)
        self.order.append(('pdf', doi))
        return sample_pdf(doi)

    def runner(self):
        runner = Downloader(self.cfg, control=NoWait())
        def fetch(doi):
            self.order.append(('xml', doi))
            return article(doi), {'http': 200}
        runner.fetch = fetch
        return runner

    def test_sequential_download_render_and_keep_xml(self):
        runner = self.runner()
        runner.run()
        self.assertEqual(self.order, [(kind, row['doi']) for row in self.rows for kind in ('xml', 'pdf')])
        self.assertEqual(runner.new_count, 3)
        self.assertEqual(len(list(self.base.rglob('*.xml'))), 3)
        self.assertEqual(len(list(self.base.rglob('*.pdf'))), 3)
        for path in self.base.rglob('*'):
            if path.is_file():
                self.assertIn(path.suffix, ('.xml', '.pdf'))
                self.assertNotIn(b'SYNTHETIC_KEY', path.read_bytes())
        self.order.clear()
        again = self.runner()
        again.run()
        self.assertEqual((again.reused, again.new_count), (3, 0))
        self.assertEqual(self.order, [])

    def test_pdf_only_resume_without_xml_and_repair_truncated_pdf(self):
        self.cfg.output_mode = 'pdf'
        self.runner().run()
        self.assertEqual(list(self.base.rglob('*.xml')), [])
        self.order.clear()
        again = self.runner()
        again.run()
        self.assertEqual(again.reused, 3)
        self.assertEqual(self.order, [])
        target = next(self.base.rglob('*.pdf'))
        doi = load_json(sidecar_path(target))['doi']
        target.write_bytes(b'%PDF-truncated')
        repaired = self.runner()
        repaired.run()
        self.assertEqual((repaired.reused, repaired.new_count), (2, 1))
        self.assertEqual(self.order, [('xml', doi), ('pdf', doi)])
        self.assertEqual(list(self.base.rglob('*.xml')), [])
        self.assertEqual(len(scan_pdfs(self.base)['saved']), 3)

    def test_existing_xml_converts_without_request_before_next_article(self):
        row = self.rows[0]
        target, txt, _ = paths_for(self.base, row)
        save_fulltext(target, txt, article(row['doi']), row['doi'])
        self.cfg.max_downloads = 1
        runner = self.runner()
        runner.run()
        self.assertEqual(self.order, [('pdf', row['doi'])])
        self.assertEqual((runner.new_count, runner.reused), (1, 0))
        self.assertEqual(load_json(runner.state / 'download_summary.json')['stage'], 'limit_reached')

    def test_failure_preserves_xml_limit_counts_pdf_and_retry_uses_xml(self):
        first = self.rows[0]['doi']
        def render(document, *args):
            if first in document:
                raise ValueError('synthetic missing figure')
            return self.render(document, *args)
        self.render_mock.side_effect = render
        self.cfg.output_mode = 'pdf'
        self.cfg.max_downloads = 1
        runner = self.runner()
        runner.run()
        self.assertEqual(runner.new_count, 1)
        self.assertTrue(paths_for(self.base, self.rows[0])[0].exists())
        self.assertFalse(paths_for(self.base, self.rows[1])[0].exists())
        self.assertEqual(runner.failures[first]['status'], 'pdf_pending')
        self.render_mock.side_effect = self.render
        self.order.clear()
        retry = self.runner()
        retry.run()
        self.assertEqual(self.order, [('pdf', first)])
        self.assertEqual((retry.new_count, retry.reused), (1, 1))

    def test_cancel_and_permission_errors_keep_source(self):
        self.cfg.output_mode = 'pdf'
        for error in (Cancelled(), ActionRequired('synthetic permission')):
            self.render_mock.side_effect = error
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                self.runner().run()
            self.assertTrue(paths_for(self.base, self.rows[0])[0].exists())
            self.assertEqual(list(self.base.rglob('*.pdf')), [])

    def test_metadata_write_failure_does_not_delete_xml(self):
        self.cfg.output_mode = 'pdf'
        self.cfg.max_downloads = 1
        with patch('pdf_export.save_json', side_effect=OSError('disk unavailable')):
            runner = self.runner()
            runner.run()
        self.assertEqual(runner.new_count, 0)
        self.assertEqual(len(list(self.base.rglob('*.xml'))), 3)
        self.assertEqual(len(scan_pdfs(self.base)['saved']), 0)

    def test_changed_xml_rebuilds_pdf_and_unknown_pdf_is_not_complete(self):
        self.cfg.max_downloads = 1
        self.runner().run()
        row = self.rows[0]
        source, txt, _ = paths_for(self.base, row)
        save_fulltext(source, txt, article(row['doi']).replace(b'Example body', b'Changed body'), row['doi'])
        self.order.clear()
        runner = self.runner()
        runner.run()
        self.assertEqual(self.order, [('pdf', row['doi'])])
        pdf = next(self.base.rglob('*.pdf'))
        sidecar_path(pdf).unlink()
        inventory = scan_pdfs(self.base)
        self.assertEqual((len(inventory['saved']), inventory['unverified']), (0, 1))

    def test_pdf_content_identity_overrules_metadata_hash(self):
        self.cfg.output_mode = 'pdf'
        self.cfg.max_downloads = 1
        self.runner().run()
        target = next(self.base.rglob('*.pdf'))
        bad = sample_pdf('10.1016/j.wrong.article')
        target.write_bytes(bad)
        meta_path = sidecar_path(target)
        meta = load_json(meta_path)
        meta['pdf_sha256'] = hashlib.sha256(bad).hexdigest()
        save_json(meta_path, meta)
        self.assertEqual(scan_pdfs(self.base)['invalid'], 1)
        self.order.clear()
        self.runner().run()
        self.assertEqual(self.order[0], ('xml', self.rows[0]['doi']))

    def test_filter_does_not_destroy_complete_index_or_manifest(self):
        self.cfg.output_mode = 'xml'
        self.cfg.title_filter = 'forest, deep learning'
        self.cfg.filter_mode = 'all'
        first = self.runner()
        first.run()
        self.assertEqual([row['doi'] for row in first.records], [self.rows[0]['doi']])
        self.assertEqual(len(load_json(first.state / 'indexes/2024-2024.json')['records']), 3)
        self.assertEqual(len((first.state / 'manifest.jsonl').read_text().splitlines()), 3)
        self.cfg.title_filter = ''
        self.order.clear()
        resumed = self.runner()
        resumed.run()
        self.assertEqual((resumed.reused, resumed.new_count), (1, 2))
        self.assertEqual(self.order, [('xml', row['doi']) for row in self.rows[1:]])

    def test_zero_matches_makes_no_api_or_pdf_requests(self):
        self.cfg.title_filter = 'unmatched keyword'
        runner = self.runner()
        runner.run()
        self.assertEqual(self.order, [])
        self.assertEqual(load_json(runner.state / 'download_summary.json')['excluded'], 3)


class ArticleFilterTests(unittest.TestCase):
    def test_phrase_any_all_case_hyphens_and_boundaries(self):
        rows = [{'title': 'Deep-learning for Forest Mapping'}, {'title': 'Crop mapping'}, {'title': 'Rainforest'}]
        self.assertEqual(filter_records(rows, 'forest，deep learning', 'all'), rows[:1])
        self.assertEqual(filter_records(rows, 'FOREST; crop', 'any'), rows[:2])
        self.assertEqual(filter_records(rows, ''), rows)
        self.assertEqual(filter_records([{'title': '<i>Crop</i> mapping'}], 'crop'), [{'title': '<i>Crop</i> mapping'}])
        with self.assertRaises(ValueError):
            filter_records(rows, ', ; ')


if __name__ == '__main__':
    unittest.main()
