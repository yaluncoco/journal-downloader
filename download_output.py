"""Sequential output policy; PDF generation is shared with batch export."""
import hashlib
from pathlib import Path

from engine import Cancelled, ActionRequired, append_log, timestamp
from pdf_export import PdfConfig, PdfExporter, RENDERER_VERSION
from pdf_inventory import read_pdf, scan_pdfs


class DownloadOutput:
    def __init__(self, cfg, base, emit, control):
        self.cfg, self.base, self.emit, self.control = cfg, base.resolve(), emit, control
        self.exporter = PdfExporter(PdfConfig(base, cfg.api_key, cfg.network, cfg.start_year, cfg.end_year,
                                             browser=cfg.pdf_browser), emit, control)
        # Also accept renamed legacy XML directly inside the selected library.
        self.exporter.xml_root = self.base
        self.pdfs = {}

    def session(self):
        return self.exporter.session()

    def scan(self):
        self.pdfs = scan_pdfs(self.base, self.cfg.start_year, self.cfg.end_year, self.control.checkpoint)["saved"]

    def expected(self, doi, xml=None):
        return {"doi": doi, "layout": "double", "renderer": RENDERER_VERSION,
                **({"xml_sha256": xml["sha256"]} if xml else {})}

    def reusable(self, row, xml=None):
        if self.cfg.output_mode == "xml_pdf" and not xml:
            return None
        item = self.pdfs.get(row["doi"])
        if not item:
            return None
        try:
            checked = read_pdf(Path(item["path"]), self.expected(row["doi"], xml))
            if xml and self.cfg.output_mode == "pdf":
                self.remove_xml(self.base / xml["file"], checked)
            return {"status": "pdf_saved", "file": str(Path(item["path"]).relative_to(self.base)), **checked}
        except (Cancelled, ActionRequired):
            raise
        except Exception:
            return None

    def remove_xml(self, source, pdf):
        """Delete only the just-verified XML after durable PDF and sidecar writes."""
        self.control.checkpoint()
        if (source.is_symlink() or not source.resolve().is_relative_to(self.base.resolve())
                or source.suffix.lower() != ".xml"):
            raise ValueError("XML 路径不在当前文库内，已保留")
        if hashlib.sha256(source.read_bytes()).hexdigest() != pdf["xml_sha256"]:
            raise ValueError("XML 在合成后已变更，已保留")
        source.unlink()
        # Integrity records in application data may remain as recovery evidence.

    def process(self, row, xml):
        self.control.checkpoint()
        source = self.base / xml["file"]
        record = {"doi": row["doi"], "year": row["year"], "checked_at": timestamp()}
        try:
            result = self.exporter.export_one(source, row["year"])
            if result["status"] not in ("pdf_saved", "already_saved") or result.get("doi", "").lower() != row["doi"]:
                raise ValueError("PDF 与请求的论文身份不一致")
            checked = read_pdf(Path(result["pdf"]), self.expected(row["doi"], xml))
            self.pdfs[row["doi"]] = checked
            if self.cfg.output_mode == "pdf":
                self.remove_xml(source, checked)
            record.update(result, status="pdf_saved", file=str(Path(result["pdf"]).relative_to(self.base)))
            self.emit(kind="log", text=f"{row['doi']} → PDF 已合成并校验" + ("，已移除该篇 XML" if self.cfg.output_mode == "pdf" else ""))
        except (Cancelled, ActionRequired):
            raise
        except Exception as error:
            record.update(status="pdf_pending", message=str(error).replace(self.cfg.api_key, "[已隐藏]"))
            self.emit(kind="log", text=f"{row['doi']} → PDF 待处理，XML 已保留：{record['message']}")
        append_log(self.exporter.state / "pdf_log.jsonl", record)
        return record
