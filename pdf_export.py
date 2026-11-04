"""Resumable XML + figure objects to a locally rendered reading PDF."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from pypdf import PdfReader

from engine import (ActionRequired, Cancelled, Control, append_log, atomic_bytes,
                    library_lock, load_json, save_json, save_lines, timestamp)
from pdf_assets import ImageCache, image_objects
from pdf_document import Article

RENDERER_VERSION = "1.1.0-layout3"


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


def find_browser():
    candidates = []
    for environment in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        folder = os.environ.get(environment)
        if folder:
            candidates.extend(Path(folder) / suffix for suffix in (
                "Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"))
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser"):
        binary = shutil.which(name)
        if binary:
            candidates.append(Path(binary))
    browser = next((p for p in candidates if p.is_file()), None)
    if browser is None:
        raise ActionRequired("合成 PDF 需要已安装的 Microsoft Edge 或 Google Chrome。安装后可再次运行，无需 Python。")
    return browser


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


def render_pdf(document, browser, work, control):
    html_path, pdf_path = work / "article.html", work / "article.pdf"
    html_path.write_text(document, encoding="utf-8")
    profile = work / "browser-profile"
    command = [str(browser), "--headless", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
               "--disable-component-update", "--no-pdf-header-footer",
               "--run-all-compositor-stages-before-draw", "--timeout=90000",
               "--host-resolver-rules=MAP * ~NOTFOUND",
               "--user-data-dir=" + str(profile), "--print-to-pdf=" + str(pdf_path),
               html_path.resolve().as_uri()]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (work / "browser.log").open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, creationflags=flags)
        deadline = time.monotonic() + 120
        try:
            while process.poll() is None:
                control.checkpoint()
                if time.monotonic() > deadline:
                    raise ValueError("本篇排版超时，已留待重试。")
                control.stop.wait(0.2)
            if process.returncode or not pdf_path.is_file():
                raise ValueError("浏览器没有生成有效 PDF，请检查 Edge/Chrome 安装。")
            return pdf_path.read_bytes()
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=flags, timeout=15)
                else:
                    process.kill()
                process.wait(timeout=15)


class PdfExporter:
    def __init__(self, cfg, emit=lambda **event: None, control=None):
        cfg.validate()
        self.cfg, self.key, self.emit = cfg, cfg.api_key, emit
        self.control = control or Control()
        self.control.emit = emit
        source = cfg.source.resolve()
        self.xml_root = source / "fulltext_xml" if (source / "fulltext_xml").is_dir() else source
        library = self.xml_root.parent if self.xml_root.name == "fulltext_xml" else source
        if source.parent.name == "fulltext_xml":
            library = source.parent.parent
        self.output = (cfg.output or library / "fulltext_pdf").resolve()
        self.cache = self.output / ".image-cache"
        self.browser = None

    def count(self, total, saved, reused, stage):
        self.emit(kind="counts", total=total, saved=saved, reused=reused,
                  pending=total-saved, stage=stage)

    def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self.emit(kind="library", path=str(self.output))
        with library_lock(self.output):
            return self._run()

    def _run(self):
        self.emit(kind="phase", text="正在扫描已下载 XML…")
        records = []
        for source in sorted(self.xml_root.rglob("*.xml")):
            self.control.checkpoint()
            # Existing libraries archive by year; restrict before reading large files.
            year = next((int(p) for p in reversed(source.relative_to(self.xml_root).parts[:-1]) if re.fullmatch(r"\d{4}", p)), None)
            if year is None and re.fullmatch(r"\d{4}", self.xml_root.name):
                year = int(self.xml_root.name)
            if year is not None and not self.cfg.start_year <= year <= self.cfg.end_year:
                continue
            records.append((source, year))
        total, saved, reused, pending = len(records), 0, 0, []
        self.count(total, saved, reused, "pdf")
        for index, (source, year) in enumerate(records, 1):
            self.control.checkpoint()
            relative = source.relative_to(self.xml_root)
            target = self.output / relative.with_suffix(".pdf")
            if year and len(relative.parts) == 1:
                target = self.output / str(year) / relative.with_suffix(".pdf")
            identity = str(relative)
            record = {"xml": identity, "checked_at": timestamp()}
            try:
                data = source.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                sidecar = source.with_suffix(".xml.sha256")
                if sidecar.exists() and sidecar.read_text(encoding="ascii").strip().lower() != digest:
                    raise ValueError("XML 的 SHA-256 校验失败，请重新下载本篇 XML。")
                article = Article(data)
                if year is None and article.year.isdigit() and not self.cfg.start_year <= int(article.year) <= self.cfg.end_year:
                    total -= 1
                    continue
                record["doi"] = article.doi
                meta_path = target.with_suffix(".pdf.json")
                fingerprint = {"xml_sha256": digest, "layout": self.cfg.layout,
                               "renderer": RENDERER_VERSION, "doi": article.doi}
                if self.reusable(target, meta_path, fingerprint):
                    saved += 1
                    reused += 1
                    record["status"] = "already_saved"
                else:
                    if self.browser is None:
                        self.browser = find_browser()
                    self.emit(kind="phase", text=f"{index}/{len(records)} · {article.title}")
                    cache = ImageCache(self.cache / digest[:24], self.key, self.cfg.network, self.control, self.emit)
                    objects = image_objects(article.root)
                    images, image_meta = {}, []
                    try:
                        for ref in article.required_images():
                            path, info = cache.get(ref, objects.get(ref, []))
                            images[ref] = path
                            image_meta.append(info)
                            if info["quality"] != "IMAGE-HIGH-RES":
                                self.emit(kind="log", text=f"{article.doi} · {ref} 高清不可用，已采用普通图。")
                    finally:
                        cache.key = ""
                    document = article.render(images, self.cfg.layout)
                    self.emit(kind="phase", text=f"正在排版与校验 PDF · {article.doi}")
                    temp_base = self.output / ".work"
                    temp_base.mkdir(exist_ok=True)
                    with tempfile.TemporaryDirectory(prefix="pdf-", dir=temp_base, ignore_cleanup_errors=True) as tmp:
                        pdf = render_pdf(document, self.browser, Path(tmp), self.control)
                        pages = validate_pdf(pdf, article.doi)
                    self.control.checkpoint()
                    atomic_bytes(target, pdf)
                    save_json(meta_path, {**fingerprint, "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
                                         "pages": pages, "images": image_meta, "created_at": timestamp()})
                    record.update(status="pdf_saved", pages=pages, figures=len(images))
                    saved += 1
                self.emit(kind="log", text=f"{index}/{len(records)} · {article.doi} · {'校验通过，跳过' if record['status'] == 'already_saved' else 'PDF 已合成'}")
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
                    append_log(self.output / "pdf_log.jsonl", record)
                save_lines(self.output / "pdf_pending.jsonl", pending)
        stage = "pdf_finished_with_pending" if pending else "pdf_finished"
        result = {"checked_at": timestamp(), "total": total, "saved": saved,
                  "reused": reused, "pending": len(pending), "layout": self.cfg.layout}
        save_json(self.output / "pdf_summary.json", result)
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
