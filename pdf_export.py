"""Resumable XML + figure objects to a locally rendered reading PDF."""
from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
import hashlib
import io
from pathlib import Path
import re

from pypdf import PdfReader

from engine import (ActionRequired, Cancelled, Control, append_log, atomic_bytes,
                    library_lock, load_json, save_json, save_lines, timestamp)
from pdf_assets import ImageCache, image_objects
from pdf_document import Article
from pdf_browser import BrowserRenderer
from library_inventory import read_xml, xml_files
from library_store import state_dir, existing_sidecar, sidecar_path, migrate_pdf

RENDERER_VERSION = "1.3.0-layout5"


@dataclass
class PdfConfig:
    source: Path
    api_key: str = field(default="", repr=False)
    network: str = "direct"
    start_year: int = 1800
    end_year: int = 2100
    layout: str = "double"
    output: Path | None = None

    def validate(self):
        if not self.source.is_dir():
            raise ValueError("请选择含已下载 XML 的文库或 fulltext_xml 文件夹。")
        if not 1800 <= self.start_year <= self.end_year <= 2100:
            raise ValueError("起止年份无效。")
        if self.layout not in ("single", "double") or self.network not in ("direct", "system"):
            raise ValueError("PDF 排版或网络设置无效。")
        if self.api_key and any(ord(c) > 126 or ord(c) < 33 for c in self.api_key):
            raise ValueError("API Key 不能含空格或换行。")


def validate_pdf(data, doi):
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
        raise ValueError("PDF 文件未完整写入。")
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or not reader.pages:
        raise ValueError("PDF 不可读取。")
    pages = len(reader.pages)
    identity = " ".join(page.extract_text() or "" for page in reader.pages[:2])
    if doi.lower() not in re.sub(r"\s+", "", identity).lower():
        raise ValueError("PDF 身份校验失败：未找到本篇 DOI。")
    # Traverse every page to detect broken streams, not just a valid trailer.
    for page in reader.pages:
        page.get_contents().get_data()
    return pages


class PdfExporter:
    def __init__(self, cfg, emit=lambda **event: None, control=None):
        cfg.validate()
        self.cfg, self.key, self.emit = cfg, cfg.api_key, emit
        self.control = control or Control()
        self.control.emit = emit
        source = cfg.source.resolve()
        self.xml_root = source
        library = self.xml_root.parent if self.xml_root.name == "fulltext_xml" else source
        if source.parent.name == "fulltext_xml":
            library = source.parent.parent
        self.output = (cfg.output or library / "fulltext_pdf").resolve()
        self.state = state_dir(self.output)
        self.cache = self.state / "image-cache"
        self.renderer = BrowserRenderer(self.state / "pdf-diagnostics.json", self.control, emit)

    def count(self, total, saved, reused, stage):
        self.emit(kind="counts", total=total, saved=saved, reused=reused,
                  pending=total-saved, stage=stage)

    def run(self):
        self.emit(kind="library", path=str(self.output))
        with self.session():
            return self._run()

    @contextmanager
    def session(self):
        """One lock/cache session shared by batch and sequential exports."""
        self.output.mkdir(parents=True, exist_ok=True)
        with library_lock(self.output, managed=True):
            migrate_pdf(self.output)
            yield self

    def target_for(self, source, year=None):
        relative = source.relative_to(self.xml_root)
        if relative.parts[0] == "fulltext_xml":
            relative = Path(*relative.parts[1:])
        if year and len(relative.parts) == 1:
            relative = Path(str(year)) / relative
        return self.output / relative.with_suffix(".pdf")

    def export_one(self, source, year=None):
        """Export exactly one XML. Caller owns session; failures preserve source."""
        self.control.checkpoint()
        target = self.target_for(source, year)
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        sidecar = existing_sidecar(source)
        if sidecar.exists() and sidecar.read_text(encoding="ascii").strip().lower() != digest:
            raise ValueError("XML 的 SHA-256 校验失败，请重新下载本篇 XML。")
        article = Article(data)
        if year is None and article.year.isdigit() and not self.cfg.start_year <= int(article.year) <= self.cfg.end_year:
            return {"status": "outside_years"}
        fingerprint = {"xml_sha256": digest, "layout": self.cfg.layout,
                       "renderer": RENDERER_VERSION, "doi": article.doi}
        meta_path = sidecar_path(target)
        if self.reusable(target, existing_sidecar(target), fingerprint):
            return {"status": "already_saved", "doi": article.doi, "pdf": str(target), "xml_sha256": digest}
        self.renderer.ensure_ready()
        self.emit(kind="phase", text=f"正在准备 PDF 图片 · {article.title}")
        cache = ImageCache(self.cache / digest[:24], self.key, self.cfg.network, self.control, self.emit)
        objects = image_objects(article.root)
        images, image_meta = {}, []
        try:
            for ref in article.required_images():
                self.control.checkpoint()
                path, info = cache.get(ref, objects.get(ref, []))
                images[ref] = path
                image_meta.append(info)
                if info["quality"] != "IMAGE-HIGH-RES":
                    self.emit(kind="log", text=f"{article.doi} · {ref} 高清不可用，已采用普通图。")
        finally:
            cache.key = ""
        document = article.render(images, self.cfg.layout)
        self.emit(kind="phase", text=f"正在排版与校验 PDF · {article.doi}")
        pdf = self.renderer.render(document, article.doi)
        pages = validate_pdf(pdf, article.doi)
        self.control.checkpoint()
        atomic_bytes(target, pdf)
        save_json(meta_path, {**fingerprint, "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
                             "pages": pages, "images": image_meta, "created_at": timestamp(),
                             "year": year or (int(article.year) if article.year.isdigit() else None),
                             "title": article.title})
        return {"status": "pdf_saved", "doi": article.doi, "pdf": str(target),
                "xml_sha256": digest, "pages": pages, "figures": len(images)}

    def _run(self):
        self.emit(kind="phase", text="正在扫描已下载 XML…")
        records = []
        seen, duplicates = set(), 0
        for source in xml_files(self.xml_root, self.control.checkpoint):
            self.control.checkpoint()
            # Existing libraries archive by year; restrict before reading large files.
            year = next((int(p) for p in reversed(source.relative_to(self.xml_root).parts[:-1]) if re.fullmatch(r"\d{4}", p)), None)
            if year is None and re.fullmatch(r"\d{4}", self.xml_root.name):
                year = int(self.xml_root.name)
            if year is not None and not self.cfg.start_year <= year <= self.cfg.end_year:
                continue
            try:
                info = read_xml(source)
                if info["doi"] in seen:
                    duplicates += 1
                    continue
                seen.add(info["doi"])
            except (OSError, ValueError):
                pass  # Keep damaged files in the batch so their errors are reported.
            records.append((source, year))
        if duplicates:
            self.emit(kind="log", text=f"检测到 {duplicates} 份重复 XML，每个 DOI 合成一次，原文件均保留。")
        total, saved, reused, pending = len(records), 0, 0, []
        self.count(total, saved, reused, "pdf")
        for index, (source, year) in enumerate(records, 1):
            self.control.checkpoint()
            relative = source.relative_to(self.xml_root)
            identity = str(relative)
            record = {"xml": identity, "checked_at": timestamp()}
            try:
                record.update(self.export_one(source, year))
                if record["status"] == "outside_years":
                    total -= 1
                    continue
                saved += 1
                if record["status"] == "already_saved":
                    reused += 1
                self.emit(kind="log", text=f"{index}/{len(records)} · {record['doi']} · {'校验通过，跳过' if record['status'] == 'already_saved' else 'PDF 已合成'}")
            except (ActionRequired, Cancelled):
                raise
            except Exception as error:
                message = str(error).replace(self.key, "[已隐藏]") if self.key else str(error)
                record.update(status="pending", reason=message)
                pending.append(record)
                self.emit(kind="log", text=f"{index}/{len(records)} · {identity} · 待处理：{message}")
            finally:
                self.count(total, saved, reused, "pdf")
                if "status" in record:
                    append_log(self.state / "pdf_log.jsonl", record)
                save_lines(self.state / "pdf_pending.jsonl", pending)
        stage = "pdf_finished_with_pending" if pending else "pdf_finished"
        result = {"checked_at": timestamp(), "total": total, "saved": saved,
                  "reused": reused, "pending": len(pending), "layout": self.cfg.layout}
        save_json(self.state / "pdf_summary.json", result)
        self.count(total, saved, reused, stage)
        return result

    @staticmethod
    def reusable(target, meta_path, expected):
        meta = load_json(meta_path, {})
        if not isinstance(meta, dict) or any(meta.get(k) != v for k, v in expected.items()):
            return False
        try:
            data = target.read_bytes()
            if hashlib.sha256(data).hexdigest() != meta.get("pdf_sha256"):
                return False
            return validate_pdf(data, expected["doi"]) == meta.get("pages")
        except Exception:
            return False
