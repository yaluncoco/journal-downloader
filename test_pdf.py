"""PDF correctness, credential boundaries and recovery using synthetic fixtures."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import xml.etree.ElementTree as ET

from PIL import Image

from engine import ActionRequired, Cancelled, Control, load_json, save_json
from pdf_assets import ImageCache, MissingImage, image_details, object_url
from pdf_document import Article, math_html
from pdf_export import PdfConfig, PdfExporter, RENDERER_VERSION, validate_pdf
from library_store import state_dir

DOI = "10.1016/j.example.2024.000001"
URL = "https://api.elsevier.com/content/object/eid/1-s2.0-S0000000000000000-gr1_lrg.jpg?httpAccept=%2A%2F%2A"


def fixture(body=None, floats="", objects=""):
    body = body or '<section><label>1</label><section-title>Introduction</section-title><para>Example body content with a DOI identity check.</para></section>'
    return f'''<root xmlns:ce="http://www.elsevier.com/xml/common/dtd" xmlns:m="http://www.w3.org/1998/Math/MathML">
<coredata><doi>{DOI}</doi><title>Example article</title><publicationName>Example Journal</publicationName><coverDate>2024-01-01</coverDate></coredata>
<originalText><article><floats>{floats}</floats><head><title>Example article</title><author-group><author><given-name>Ada</given-name><surname>Example</surname><contributor-role>DO NOT DUPLICATE ROLES</contributor-role></author></author-group><abstract><simple-para>A synthetic test of the PDF pipeline.</simple-para></abstract></head><body>{body}</body><tail><bibliography><section-title>References</section-title><bib-reference><reference>STRUCTURED DUPLICATE</reference><source-text>Example reference 2024.</source-text></bib-reference></bibliography></tail></article></originalText>{objects}</root>'''.encode()


def png():
    stream = io.BytesIO()
    Image.new("RGB", (100, 80), "teal").save(stream, format="PNG")
    return stream.getvalue()


class NoWait(Control):
    def __init__(self):
        super().__init__()
        self.delays = []

    def wait(self, seconds, reason=""):
        self.checkpoint()
        self.delays.append(seconds)


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class PdfTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = patch("library_store.STATE_HOME", Path(tmp.name))
        state.start()
        self.addCleanup(state.stop)

    def test_inline_high_resolution_images_use_text_scale(self):
        body = '<para>The best results are <inline-figure><link locator="fx1"/></inline-figure>.</para>'
        objects = '<object ref="fx1" type="IMAGE-HIGH-RES" width="353" height="55"/><object ref="fx1" type="IMAGE-DOWNSAMPLED" width="79" height="12"/>'
        document = Article(fixture(body, objects=objects)).render({"fx1": Path("sharp.png")})
        self.assertIn('class="inline-image" style="height:1.0000em"', document)
        self.assertNotIn('max-height: 12mm', document)
        fallback = Article(fixture(body)).render({"fx1": Path("sharp.png")})
        self.assertIn('style="height:0.9500em"', fallback)
        objects = '<object ref="fx1" type="IMAGE-DOWNSAMPLED" height="24"/>'
        fraction = Article(fixture(body, objects=objects)).render({"fx1": Path("fraction.png")})
        self.assertIn('style="height:2.0000em"', fraction)

    def test_url_rejects_external_and_removes_credentials(self):
        for url in ("http://api.elsevier.com/content/object/eid/x", "https://evil.test/content/object/eid/x",
                    "https://api.elsevier.com@evil.test/content/object/eid/x", "https://api.elsevier.com/content/article/eid/x",
                    "https://api.elsevier.com/content/object/eid/../../x"):
            with self.assertRaises(MissingImage):
                object_url(url)
        self.assertNotIn("secret", object_url(URL + "&apiKey=secret"))

    def test_cache_integrity_and_offline_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = ImageCache(temp, "PRIVATE_TEST_KEY", "direct", NoWait(), lambda **e: None)
            candidate = [{"url": URL, "type": "IMAGE-HIGH-RES"}]
            with patch.object(cache, "fetch", return_value=png()) as fetch:
                target, info = cache.get("gr1", candidate)
                cache.get("gr1", candidate)
                self.assertEqual(fetch.call_count, 1)
                target.write_bytes(b"truncated")
                cache.get("gr1", candidate)
                self.assertEqual(fetch.call_count, 2)
            self.assertNotIn("PRIVATE_TEST_KEY", target.with_suffix(".json").read_text())
            cache.key = ""
            extra = {"url": URL.replace("gr1_lrg", "gr1_new"), "type": "IMAGE-HIGH-RES"}
            self.assertEqual(cache.get("gr1", [extra] + candidate)[0], target)

    def test_missing_high_resolution_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = ImageCache(temp, "key", "direct", NoWait(), lambda **e: None)
            candidates = [{"url": URL, "type": "IMAGE-HIGH-RES"},
                          {"url": URL.replace("_lrg", ""), "type": "IMAGE-DOWNSAMPLED"}]
            with patch.object(cache, "fetch", side_effect=[None, png()]):
                self.assertEqual(cache.get("gr1", candidates)[1]["quality"], "IMAGE-DOWNSAMPLED")

    def test_network_auth_rate_limit_and_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            control = NoWait()
            cache = ImageCache(temp, "key", "direct", control, lambda **e: None)
            error = urllib.error.HTTPError(URL, 429, "limited", {"Retry-After": "75"}, None)
            with patch.object(cache.opener, "open", side_effect=[error, Response(png())]):
                self.assertEqual(cache.fetch(URL), png())
            self.assertIn(75, control.delays)
            error = urllib.error.HTTPError(URL, 403, "denied", {}, None)
            with patch.object(cache.opener, "open", side_effect=error) as request:
                with self.assertRaises(ActionRequired):
                    cache.fetch(URL)
                self.assertEqual(request.call_count, 1)
            control.stop.set()
            with self.assertRaises(Cancelled):
                cache.get("gr1", [{"url": URL, "type": "IMAGE-HIGH-RES"}])

    def test_truncated_image_response_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = ImageCache(temp, "key", "direct", NoWait(), lambda **e: None)
            bad = Response(b"short")
            bad.headers["Content-Length"] = "9999"
            with patch.object(cache.opener, "open", side_effect=[bad, Response(png())]) as request:
                self.assertEqual(cache.fetch(URL), png())
                self.assertEqual(request.call_count, 2)

    def test_float_placement_math_and_reference(self):
        floats = '<figure id="f1"><label>Fig. 1</label><caption><simple-para>Figure caption.</simple-para></caption><link locator="gr1"/></figure>'
        body = '<para>First paragraph <float-anchor refid="f1"/>contains <m:math><m:mfenced><m:mi>x</m:mi><m:mi>y</m:mi></m:mfenced></m:math> variables.</para><para>Second paragraph.</para>'
        article = Article(fixture(body, floats))
        with self.assertRaises(ValueError):
            article.render({})
        document = article.render({"gr1": Path("example.png")})
        self.assertEqual(document.count('<figure class="wide"'), 1)
        self.assertLess(document.index('First paragraph'), document.index('<figure class="wide"'))
        self.assertLess(document.index('<figure class="wide"'), document.index('Second paragraph'))
        self.assertIn('<mo>(</mo><mi>x</mi><mo>,</mo><mi>y</mi><mo>)</mo>', document)
        self.assertNotIn("STRUCTURED DUPLICATE", document)
        self.assertNotIn("DO NOT DUPLICATE ROLES", document)

    def test_html_no_arbitrary_remote_markup(self):
        article = Article(fixture('<para>&lt;script&gt;attack&lt;/script&gt;<inter-ref href="https://evil.test/x">external text</inter-ref><script>plain text</script></para>'))
        document = article.render({})
        self.assertNotIn("https://evil.test", document)
        self.assertNotIn("<script>attack", document)
        self.assertEqual(document.count("<script>"), 1)  # Only the fixed, CSP-hashed fitting code.
        self.assertIn("default-src 'none'", document)
        node = ET.fromstring('<math><mi onclick="attack()">x</mi></math>')
        self.assertNotIn("onclick", math_html(node))

    def test_table_rowspan_colspan_and_footnote(self):
        table = '<table><label>Table 1</label><caption>Caption</caption><tgroup cols="3"><colspec colname="a"/><colspec colname="b"/><colspec colname="c"/><spanspec spanname="ab" namest="a" nameend="b"/><tbody><row><entry spanname="ab" morerows="1">Merged</entry><entry colname="c">A</entry></row><row><entry>B</entry></row></tbody></tgroup><table-footnote>Footnote.</table-footnote></table>'
        document = Article(fixture(table)).render({})
        self.assertIn('colspan="2" rowspan="2">Merged', document)
        self.assertIn('colspan="1" rowspan="1">B', document)
        self.assertIn('Footnote.', document)

    def test_rawtext_cannot_claim_complete_illustrated_pdf(self):
        with self.assertRaisesRegex(ValueError, "rawtext"):
            Article(b'<root><coredata><doi>10.1/x</doi></coredata><originalText><rawtext>Only text</rawtext></originalText></root>')

    def test_simple_article_corrigendum_preserves_head_body_and_math(self):
        data = fixture('<sections><para>Correction <m:math><m:mi>x</m:mi></m:math> is complete.</para></sections>')
        for old, new in ((b'<article>', b'<simple-article>'), (b'</article>', b'</simple-article>'),
                         (b'<head>', b'<simple-head>'), (b'</head>', b'</simple-head>')):
            data = data.replace(old, new)
        rendered = Article(data).render({})
        for text in ('Example article', 'Ada', 'Correction', '<mi>x</mi>', 'Example reference'):
            self.assertIn(text, rendered)

    def test_batch_scans_old_and_new_xml_layouts_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, folder in enumerate((root / '2024', root / 'fulltext_xml/2024')):
                folder.mkdir(parents=True)
                (folder / (folder.parent.name + '.xml')).write_bytes(fixture().replace(DOI.encode(), (DOI + str(index)).encode()))
            (root / '2024/duplicate.xml').write_bytes((folder / (folder.parent.name + '.xml')).read_bytes())
            exporter = PdfExporter(PdfConfig(root))
            with patch.object(exporter, 'export_one', return_value={'status': 'pdf_saved', 'doi': DOI}) as export:
                result = exporter.run()
                self.assertEqual(result['saved'], 2)
                self.assertEqual(export.call_count, 2)

    def test_batch_failed_article_continues_and_checks_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "fulltext_xml" / "2024"
            source.mkdir(parents=True)
            (source / "a.xml").write_bytes(fixture())
            (source / "a.xml.sha256").write_text("wrong")
            (source / "b.xml").write_bytes(fixture())
            output = Path(temp) / "pdf"
            with patch("pdf_export.BrowserRenderer.ensure_ready"), \
                 patch("pdf_export.BrowserRenderer.render", return_value=b"fakepdf"), \
                 patch("pdf_export.validate_pdf", return_value=1):
                result = PdfExporter(PdfConfig(source.parent, output=output, method="browser")).run()
                self.assertEqual(result["saved"], 1)
                self.assertEqual(result["pending"], 1)
                result = PdfExporter(PdfConfig(source.parent, output=output, method="browser")).run()
                self.assertEqual(result["reused"], 1)
                (output / "2024" / "b.pdf").write_bytes(b"corrupted")
                result = PdfExporter(PdfConfig(source.parent, output=output, method="browser")).run()
                self.assertEqual(result["reused"], 0)
                self.assertEqual(result["saved"], 1)
            self.assertIn("SHA-256", (state_dir(output) / "pdf_pending.jsonl").read_text(encoding="utf-8"))
            self.assertTrue(all(p.suffix == ".pdf" for p in output.rglob("*") if p.is_file()))

    def test_resume_rejects_wrong_layout_xml_and_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "article.pdf"
            meta_path = target.with_suffix(".pdf.json")
            target.write_bytes(b"fakepdf")
            expected = {"xml_sha256": "123", "layout": "double", "renderer": RENDERER_VERSION, "doi": DOI}
            save_json(meta_path, {**expected, "pdf_sha256": hashlib.sha256(b"fakepdf").hexdigest(), "pages": 1})
            with patch("pdf_export.validate_pdf", return_value=1):
                self.assertTrue(PdfExporter.reusable(target, meta_path, expected))
                self.assertFalse(PdfExporter.reusable(target, meta_path, {**expected, "layout": "single"}))
                self.assertFalse(PdfExporter.reusable(target, meta_path, {**expected, "xml_sha256": "456"}))
            target.write_bytes(b"fake")
            self.assertFalse(PdfExporter.reusable(target, meta_path, expected))

    def test_bad_pdf_rejected(self):
        with self.assertRaises(ValueError):
            validate_pdf(b"%PDF-truncated", DOI)


if __name__ == "__main__":
    unittest.main()
