"""No-browser output, page-preview rejection, scientific content and resume."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import urllib.error

from PIL import Image, ImageStat
from pypdf import PdfReader
from lxml import html

from engine import Config, Downloader, PRESETS, ActionRequired, Cancelled, load_json
from pdf_export import PdfConfig, PdfExporter, validate_pdf
from pdf_native import NativeRenderer, ReadingDocument
from pdf_official import OfficialPdfClient, pdf_facts
from library_store import sidecar_path
from test_pdf import fixture, DOI, NoWait, png
from test_pipeline import sample_pdf
from test_engine import Opener, Response
from smoke_test import cached_index


class NativePdfTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        state = patch('library_store.STATE_HOME', self.root / 'state')
        state.start()
        self.addCleanup(state.stop)

    def test_default_download_never_instantiates_browser_and_resumes_pdf_only(self):
        cfg = Config('SYNTHETIC_KEY', PRESETS[0].url, 2024, 2024, self.root, output_mode='pdf', interval=0)
        base = self.root / PRESETS[0].issn
        cached_index(base, [{'doi': DOI, 'year': 2024, 'date': '2024-01-01', 'title': 'Example'}])
        with patch('pdf_export.BrowserRenderer', side_effect=AssertionError('browser started')), \
             patch('pdf_official.fetch_article', side_effect=AssertionError('No page evidence must not fetch PDF')):
            runner = Downloader(cfg)
            runner.fetch = Mock(return_value=(fixture(), {'http': 200}))
            runner.run()
            self.assertEqual(runner.new_count, 1)
            self.assertFalse(list(base.rglob('*.xml')))
            target = next(base.rglob('*.pdf'))
            self.assertEqual(load_json(sidecar_path(target))['source_kind'], 'xml_native')
            resumed = Downloader(cfg)
            resumed.fetch = Mock(side_effect=AssertionError('downloaded complete file again'))
            resumed.run()
            self.assertEqual(resumed.reused, 1)
            self.assertGreater(validate_pdf(target.read_bytes(), DOI), 0)

    def test_native_content_images_tables_math_and_no_remote_fetch(self):
        from pdf_document import Article
        figure = self.root / 'figure.png'
        figure.write_bytes(png())
        floats = '<figure id="f1"><label>Fig. 1</label><caption><simple-para>Complete figure caption</simple-para></caption><link locator="gr1"/></figure>'
        body = ('<section><section-title>Methods</section-title><para>Native content <float-anchor refid="f1"/>'
            '<m:math><m:mfrac><m:mi>x</m:mi><m:mn>2</m:mn></m:mfrac></m:math> is preserved.</para>'
            '<table><label>Table 1</label><caption>Native table caption</caption><tgroup cols="2">'
            '<thead><row><entry>A</entry><entry>B</entry></row></thead><tbody>'
            '<row><entry>Tree sample</entry><entry>42.5</entry></row></tbody></tgroup></table>'
            '<para>Final paragraph with α and é.</para></section>')
        article = Article(fixture(body, floats))
        pdf = NativeRenderer(NoWait()).render(article.render({'gr1': figure}), DOI)
        reader = PdfReader(io.BytesIO(pdf))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        for value in ('Methods', 'Native content', 'Complete figure caption', 'Native table caption',
                      'Tree sample', '42.5', 'Final paragraph', 'Example reference 2024.'):
            self.assertIn(value, text)
        self.assertGreaterEqual(sum(len(page.images) for page in reader.pages), 2)
        document = ReadingDocument(self.root, NoWait())
        document.math(html.fromstring('<math><mfrac><mi>x</mi><mn>2</mn></mfrac></math>'), 400)
        path, _, _ = next(iter(document.math_cache.values()))
        with Image.open(path) as image:
            extrema = ImageStat.Stat(image.convert('L')).extrema[0]
            self.assertLess(extrema[0], 50)  # SVG2 symbols must not become blank.
            self.assertGreater(extrema[1], 240)

    def test_invalid_math_cancel_and_remote_images_fail_closed(self):
        renderer = NativeRenderer(NoWait())
        renderer.control.stop.set()
        with self.assertRaises(Cancelled):
            renderer.render('<html/>', DOI)
        doc = ReadingDocument(self.root, NoWait())
        with self.assertRaises(ValueError):
            doc.image_path(html.fromstring('<img src="https://example.test/secret">'))
        with self.assertRaisesRegex(ValueError, '暂不支持'):
            doc.math(html.fromstring('<math><unknown>danger</unknown></math>'), 400)

    def test_publisher_save_is_verified_and_never_renders_or_fetches_figures(self):
        source = self.root / '2024'
        source.mkdir()
        data = fixture().replace(b'</root>', b'<x:web-pdf-page-count xmlns:x="http://www.elsevier.com/xml/xocs/dtd">1</x:web-pdf-page-count></root>')
        (source / 'article.xml').write_bytes(data)
        exporter = PdfExporter(PdfConfig(source, api_key='KEY', output=self.root / 'out'))
        with patch('pdf_official.fetch_article', return_value=(sample_pdf(DOI), {'http': 200})), \
             patch.object(exporter.renderer, 'render', side_effect=AssertionError('rendered publisher PDF')):
            result = exporter.run()
            self.assertEqual(result['saved'], 1)
            target = next((self.root / 'out').rglob('*.pdf'))
            meta = load_json(sidecar_path(target))
            self.assertEqual((meta['source_kind'], meta['pages'], meta['expected_pages']), ('publisher', 1, 1))
            self.assertEqual(exporter.run()['reused'], 1)


class OfficialPdfTests(unittest.TestCase):
    def setUp(self):
        self.client = OfficialPdfClient('SYNTHETIC_SECRET', 'direct', NoWait(), lambda **event: None)
        self.facts = {'expected_pages': 17, 'open_access': False}

    def test_http_200_first_page_is_rejected_and_not_retried_per_subscription(self):
        with patch('pdf_official.fetch_article', return_value=(sample_pdf(DOI), {'http': 200})) as fetch:
            data, reason = self.client.get(DOI, self.facts)
            self.assertIsNone(data)
            self.assertIn('1/17', reason)
            self.client.get(DOI, self.facts)
            self.assertEqual(fetch.call_count, 1)
            data, _ = self.client.get(DOI, {'expected_pages': 1, 'open_access': True})
            self.assertTrue(data.startswith(b'%PDF-'))

    def test_identity_missing_evidence_and_authorization(self):
        with patch('pdf_official.fetch_article') as fetch:
            self.client.get(DOI, {'expected_pages': None, 'open_access': True})
            fetch.assert_not_called()
        with patch('pdf_official.fetch_article', return_value=(sample_pdf('10.1016/wrong'), {'http': 200})):
            self.assertIsNone(self.client.get(DOI, {'expected_pages': 1, 'open_access': True})[0])
        with patch('pdf_official.fetch_article', return_value=(None, {'http': 403, 'status': 'authentication_error'})) as fetch:
            self.client.get(DOI, self.facts)
            self.client.get(DOI, self.facts)
            self.assertEqual(fetch.call_count, 1)

    def test_full_view_header_bounded_read_and_quota(self):
        self.client.opener = Opener([Response(sample_pdf(DOI))])
        self.client.get(DOI, {'expected_pages': 1, 'open_access': True})
        request = self.client.opener.requests[0]
        self.assertIn('view=FULL', request.full_url)
        self.assertNotIn(self.client.key, request.full_url)
        self.assertEqual(request.get_header('Accept'), 'application/pdf')
        with patch('pdf_official.fetch_article', return_value=(None, {'wait_until': 9999999999})):
            with self.assertRaises(ActionRequired):
                self.client.get(DOI, self.facts)

    def test_page_count_must_be_authoritative_not_reference_page_numbers(self):
        self.assertIsNone(pdf_facts(b'<root><web-pdf-page-count>1</web-pdf-page-count></root>')['expected_pages'])
        self.assertEqual(pdf_facts(b'<root xmlns:x="http://www.elsevier.com/xml/xocs/dtd"><x:web-pdf-page-count>17</x:web-pdf-page-count></root>')['expected_pages'], 17)

    def test_last_successful_quota_response_is_saved_before_next_request_stops(self):
        with patch('pdf_official.fetch_article', return_value=(sample_pdf(DOI), {'http': 200, 'remaining': '0'})) as fetch:
            data, _ = self.client.get(DOI, {'expected_pages': 1, 'open_access': True})
            self.assertIsNotNone(data)
            with self.assertRaises(ActionRequired):
                self.client.get(DOI, {'expected_pages': 1, 'open_access': True})
            self.assertEqual(fetch.call_count, 1)
