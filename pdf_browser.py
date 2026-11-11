"""Isolated browser printing with offline preflight and durable diagnostics."""
from contextlib import suppress
import ctypes
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

from app_runtime import clean_dll_search_path, independent_environment
from browser_detection import browsers, browser_name
from engine import ActionRequired, Cancelled, Control, VERSION, save_json, timestamp
from pdf_workspace import print_workspace

PROBE_DOI = "10.0000/journal-downloader.pdf-check"
PROBE = ('<!doctype html><html><meta charset="utf-8"><body>'
         '<h1>PDF environment check</h1><p>' + PROBE_DOI + '</p></body></html>')


def browser_version(path):
    if os.name != "nt":
        return None
    try:
        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, data):
            return None
        pointer, length = ctypes.c_void_p(), ctypes.c_uint()
        if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
            return None
        values = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        if values[0] != 0xFEEF04BD:
            return None
        return ".".join(str(n) for n in (values[2] >> 16, values[2] & 65535, values[3] >> 16, values[3] & 65535))
    except (OSError, ValueError):
        return None


class BrowserFailure(ValueError):
    def __init__(self, message, returncode=None):
        super().__init__(message)
        self.returncode = returncode


def render_pdf(document, browser, work, control):
    work = work.resolve()
    html_path, pdf_path = work / "article.html", work / "article.pdf"
    html_path.write_text(document, encoding="utf-8")
    command = [str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
        "--disable-component-update", "--no-pdf-header-footer",
        "--run-all-compositor-stages-before-draw", "--timeout=90000",
        "--host-resolver-rules=MAP * ~NOTFOUND",
        "--user-data-dir=" + str(work / "browser-profile"),
        "--print-to-pdf=" + str(pdf_path), html_path.as_uri()]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    environment = independent_environment()
    with (work / "browser.log").open("wb") as log:
        try:
            with clean_dll_search_path():
                process = subprocess.Popen(command, stdout=log, stderr=log, cwd=work,
                                           env=environment, creationflags=flags)
        except OSError as error:
            raise BrowserFailure(f"浏览器启动失败：{error}") from error
        deadline = time.monotonic() + 120
        try:
            while process.poll() is None:
                control.checkpoint()
                if time.monotonic() > deadline:
                    raise BrowserFailure("浏览器排版超过 120 秒。")
                control.stop.wait(.2)
            control.checkpoint()
            if not pdf_path.is_file():
                raise BrowserFailure(f"浏览器未输出 PDF（退出码 {process.returncode}）。", process.returncode)
            # A valid PDF can precede a browser crash during shutdown. The caller
            # verifies the complete PDF and DOI; exit status alone is not proof.
            return pdf_path.read_bytes()
        finally:
            if process.poll() is None:
                with suppress(OSError, subprocess.SubprocessError):
                    if os.name == "nt":
                        with clean_dll_search_path():
                            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=environment, creationflags=flags, timeout=15)
                    else:
                        process.kill()
                    process.wait(timeout=15)


class BrowserRenderer:
    def __init__(self, report, control=None, emit=lambda **event: None, browser=""):
        self.report = Path(report)
        self.control = control or Control()
        self.emit = emit
        self.selection = browser
        self.selected = None
        self.candidates = None
        self.attempts = []
        self.versions = {}

    def _save(self, ok, error=None):
        for browser in self.candidates or []:
            if str(browser) not in self.versions:
                self.versions[str(browser)] = browser_version(browser)
        save_json(self.report, {"version": VERSION, "checked_at": timestamp(), "ok": ok,
            "platform": platform.platform(), "frozen": bool(getattr(sys, "frozen", False)),
            "application": sys.executable, "browser_versions": self.versions,
            "browser_selection": self.selection or "auto", "error": error,
            "selected_browser": str(self.selected) if self.selected else None,
            "candidates": [str(path) for path in self.candidates or []], "attempts": self.attempts[-20:]})

    def _attempt(self, document, doi, browser, phase):
        from pdf_export import validate_pdf
        self.control.checkpoint()
        # Use the OS local temp directory, independent of the paper library and
        # read-only installation folder; no profile belonging to the user is used.
        record = {"browser": str(browser), "phase": phase, "time": timestamp()}
        try:
            with print_workspace() as work:
                record["work_directory"] = str(work)
                try:
                    data = render_pdf(document, browser, work, self.control)
                    record["pages"] = validate_pdf(data, doi)
                except Exception:
                    with suppress(OSError):
                        with (work / "browser.log").open("rb") as log:
                            log.seek(max(0, log.seek(0, 2) - 8000))
                            stderr = log.read().decode("utf-8", errors="replace")
                        record["stderr"] = re.sub(r"(?i)(apikey|api_key|token|authorization)([=: ]+)[^\s&]+",
                                                   r"\1\2[hidden]", stderr)
                    raise
                record["ok"] = True
                return data
        except Cancelled:
            raise
        except Exception as error:
            record.update(ok=False, error=str(error), returncode=getattr(error, "returncode", None))
            if isinstance(record["returncode"], int):
                record["returncode_hex"] = f"0x{record['returncode'] & 0xFFFFFFFF:08X}"
            raise BrowserFailure(record["error"], record["returncode"]) from error
        finally:
            if "ok" in record:
                self.attempts.append(record)
                self._save(record["ok"])

    def ensure_ready(self, force=False):
        if self.selected is not None and not force:
            return
        self.emit(kind="phase", text="正在检测 PDF 环境（离线，不使用 API 配额）…")
        if self.candidates is None:
            try:
                self.candidates = browsers(self.selection)
            except (OSError, ValueError) as error:
                self._save(False, str(error))
                raise ActionRequired(str(error) + f" 诊断文件：{self.report}") from error
        preferred = self.selected
        self.selected = None
        choices = list(dict.fromkeys(([preferred] if preferred else []) + self.candidates))
        for browser in choices:
            try:
                self._attempt(PROBE, PROBE_DOI, browser, "environment-check")
            except BrowserFailure:
                self.emit(kind="log", text=f"{browser_name(browser)} 的 PDF 检测未通过。")
                continue
            self.selected = browser
            self._save(True)
            version = self.versions.get(str(browser)) or "版本未知"
            self.emit(kind="log", text=f"PDF 环境检测通过：{browser_name(browser)} · {version}。")
            self.emit(kind="log", text=f"PDF 浏览器位置：{browser}")
            return
        reason = "未找到 Edge / Chrome。" if not choices else "已安装浏览器均未通过离线 PDF 检测。"
        if self.selection and choices:
            reason = f"指定的 {browser_name(choices[0])} 未通过离线 PDF 检测。"
        self._save(False, reason)
        raise ActionRequired(reason + "可在「合成 PDF」中重新选择浏览器；也可选择「仅 XML」继续下载。"
                             + f"现有 XML 已保留。诊断文件：{self.report}")

    def render(self, document, doi):
        self.ensure_ready()
        choices = list(dict.fromkeys([self.selected] + self.candidates))
        for browser in choices:
            try:
                data = self._attempt(document, doi, browser, "article")
            except BrowserFailure:
                continue
            if browser != self.selected:
                self.emit(kind="log", text=f"已切换到 {browser_name(browser)}，PDF 合成成功。")
            self.selected = browser
            self._save(True)
            return data
        # Distinguish a bad article from a broken system before repeating failures
        # across an entire library. Only a working probe permits the batch to go on.
        self.ensure_ready(force=True)
        self._save(False)  # A successful probe does not make the failed article complete.
        raise ValueError(f"本篇 PDF 未通过校验，浏览器环境正常，XML 已保留。诊断文件：{self.report}")


def diagnose(report, control=None, emit=lambda **event: None, browser=""):
    renderer = BrowserRenderer(report, control, emit, browser)
    renderer.ensure_ready()
    return renderer.report
