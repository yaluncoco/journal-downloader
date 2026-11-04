"""Offline end-to-end check, also runnable from the frozen EXE."""
import json
from pathlib import Path
import tempfile

from engine import Config, Downloader, PRESETS, paths_for, save_json


def xml(doi="10.1016/j.demo.2024.123456", body=True):
    return (f'<full-text-retrieval-response xmlns:dc="http://purl.org/dc/elements/1.1/" '
            f'xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">'
            f'<coredata><prism:doi>{doi}</prism:doi><dc:title>Offline test article</dc:title></coredata>'
            + ('<originalText><article><body><section><section-title>Results</section-title>'
               '<para>Test content for integrity verification.</para></section></body></article></originalText>' if body else '')
            + '</full-text-retrieval-response>').encode()


def cached_index(base, rows, issn=PRESETS[0].issn):
    save_json(base / "indexes" / "2024-2024.json", {"complete": True, "issn": issn,
        "years": [2024, 2024], "created_at": "offline fixture", "records": rows})


def run(output, online=False, pdf=False):
    results = {}
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        results["tk"] = str(root.tk.call("info", "patchlevel"))
        root.destroy()
        from ui import App
        app = App()
        app.withdraw()
        app.update_idletasks()
        app._on_preset("ISPRS")
        assert app.url.get() == PRESETS[1].url
        app._on_preset("RSE")
        assert app.url.get() == PRESETS[0].url
        app._busy(True)
        app._busy(False)
        app.events.put({"kind": "counts", "total": 100, "saved": 60, "reused": 20,
                        "pending": 40, "stage": "downloading"})
        app._poll()
        assert app.metrics["saved"].get() == "60"
        assert app.percent.get() == "60%"
        app.destroy()
        results["modern_ui_events"] = True
        from updater import version_tuple, safe_url
        assert version_tuple("v1.10.0") > version_tuple("1.9.9")
        assert safe_url("https://github.com/yaluncoco/journal-downloader")
        results["update_module_loaded"] = True
        with tempfile.TemporaryDirectory(prefix="journal-downloader-test-") as tmp:
            base = Path(tmp) / PRESETS[0].issn
            row = {"doi": "10.1016/j.demo.2024.123456", "year": 2024, "title": "Offline test", "date": "2024-01"}
            cached_index(base, [row])
            config = Config("OFFLINE_TEST_CREDENTIAL", PRESETS[0].url, 2024, 2024, Path(tmp), interval=0)
            downloader = Downloader(config)
            downloader.fetch = lambda doi: (xml(doi), {"http": 200})
            downloader.run()
            assert downloader.new_count == 1
            results["download_and_validate"] = True
            resumed = Downloader(config)
            def no_request(doi):
                raise AssertionError("Valid saved file triggered a network request")
            resumed.fetch = no_request
            resumed.run()
            assert resumed.reused == 1 and resumed.new_count == 0
            results["resume_skips_valid_xml"] = True
            target, _, _ = paths_for(base, row)
            target.write_bytes(b"<truncated>")
            repaired = Downloader(config)
            repaired.fetch = lambda doi: (xml(doi), {"http": 200})
            repaired.run()
            assert repaired.new_count == 1 and repaired.reused == 0
            results["corrupt_file_redownloaded"] = True
            for path in Path(tmp).rglob("*"):
                if path.is_file():
                    assert config.api_key.encode() not in path.read_bytes()
            results["no_credential_on_disk"] = True
            if pdf:
                from pdf_export import PdfConfig, PdfExporter
                pdf_xml = Path(tmp) / "pdf-fixture" / "2024"
                pdf_xml.mkdir(parents=True)
                data = xml().replace(b'<dc:title>Offline test article</dc:title>',
                    b'<dc:title>Offline test article</dc:title><prism:coverDate>2024-01-01</prism:coverDate>')
                data = data.replace(b'<article><body>',
                    b'<article><head><title>Offline test article</title></head><body>')
                (pdf_xml / "sample.xml").write_bytes(data)
                pdf_cfg = PdfConfig(source=pdf_xml.parent, output=Path(tmp) / "pdf-output")
                first = PdfExporter(pdf_cfg).run()
                assert first["saved"] == 1 and first["pending"] == 0
                second = PdfExporter(pdf_cfg).run()
                assert second["reused"] == 1
                pdf_file = next(pdf_cfg.output.rglob("*.pdf"))
                pdf_file.write_bytes(b"truncated PDF")
                third = PdfExporter(pdf_cfg).run()
                assert third["saved"] == 1 and third["reused"] == 0
                results["pdf_render_resume_repair"] = True
            if online:
                results["online_metadata"] = []
                for journal in PRESETS:
                    probe = Downloader(Config("PUBLIC_METADATA_ONLY", journal.url, 2024, 2024, Path(tmp)))
                    metadata = probe.public_json(f"https://api.crossref.org/journals/{journal.issn}/works?rows=1&filter=from-print-pub-date:2024-01-01,until-print-pub-date:2024-12-31")["message"]
                    results["online_metadata"].append({"journal": journal.name, "count": metadata["total-results"], "sample_doi": metadata["items"][0]["DOI"]})
        results["ok"] = True
    except Exception as error:
        results["ok"] = False
        results["error"] = f"{type(error).__name__}: {error}"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not results["ok"]:
        raise RuntimeError(results["error"])


if __name__ == "__main__":
    import sys
    run(Path(sys.argv[1]))
