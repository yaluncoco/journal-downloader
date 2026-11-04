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
    import library_store
    original = library_store.STATE_HOME
    with tempfile.TemporaryDirectory(prefix="journal-state-test-") as state:
        library_store.STATE_HOME = Path(state)
        try:
            _run(output, online, pdf)
        finally:
            library_store.STATE_HOME = original


def _run(output, online=False, pdf=False):
    results = {}
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        results["tk"] = str(root.tk.call("info", "patchlevel"))
        root.destroy()
        from ui import App
        app = App(check_updates=False, remember_credentials=False, scan_libraries=False)
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
        assert app.update_button.cget("text") == ""
        from updater import Release
        app._set_update_available(Release("v99.0.0", "", "", "", 100000, ""))
        assert app.update_available and app.update_badge.winfo_manager() == "place"
        app._set_update_available(Release("v0.0.0", "", "", "", 100000, ""))
        assert not app.update_available and not app.update_badge.winfo_manager()
        results["update_arrow_and_badge"] = True
        from journal_catalog import catalog, search
        from engine import local_journal
        assert catalog()["count"] > 5000
        assert any(item["issn"] == "0034-4257" for item in search("remote sensing environment"))
        assert local_journal("https://www.sciencedirect.com/science/journal/01681923").issn == "0168-1923"
        results["bundled_journal_catalog"] = catalog()["count"]
        app.active_job = "pdf"
        app.events.put({"kind": "counts", "total": 2, "saved": 2, "reused": 1,
                        "pending": 0, "stage": "pdf_finished"})
        app.events.put({"kind": "done", "outcome": "complete", "text": "Done"})
        app._poll()
        assert app.pdf_progress.get() == 1 and "已生成完成" in app.pdf_status.get()
        results["pdf_completion_footer"] = True
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
            limited_root = Path(tmp) / "limited"
            limited_base = limited_root / PRESETS[0].issn
            rows = [{**row, "doi": f"10.1016/j.demo.2024.limit{i}"} for i in range(3)]
            cached_index(limited_base, rows)
            limited_cfg = Config("OFFLINE_TEST_CREDENTIAL", PRESETS[0].url, 2024, 2024,
                                 limited_root, interval=0, max_downloads=1)
            for expected_reused in (0, 1):
                limited = Downloader(limited_cfg)
                calls = []
                def limited_fetch(doi):
                    calls.append(doi)
                    return xml(doi), {"http": 200}
                limited.fetch = limited_fetch
                limited.run()
                assert limited.new_count == 1 and limited.reused == expected_reused
                assert len(calls) == 1
            results["download_limit_and_resume"] = True
            from library_inventory import scan_existing
            inventory = scan_existing(base, 2024, 2024)
            assert len(inventory["saved"]) == 1
            renamed = target.with_name("user-renamed.xml")
            target.rename(renamed)
            imported = Downloader(config)
            imported.fetch = no_request
            imported.run()
            assert imported.reused == 1 and imported.new_count == 0
            results["renamed_xml_resume"] = True
            assert all(path.suffix == ".xml" for path in base.rglob("*") if path.is_file())
            results["clean_paper_folders"] = True
            from credential_store import save_key, load_key, forget_key, key_file
            private_dir = Path(tmp) / "credentials"
            save_key(private_dir, config.api_key)
            assert load_key(private_dir) == config.api_key
            assert config.api_key.encode() not in key_file(private_dir).read_bytes()
            forget_key(private_dir)
            results["encrypted_key_roundtrip"] = True
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
