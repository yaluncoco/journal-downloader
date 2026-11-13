"""Parameterized, resumable Elsevier XML downloader. Only the standard library.

No credential is written to paper folders or logs. Elsevier supplies journal contents,
keyword search results and entitled full text. Crossref is used only to resolve
custom journals absent from the bundled catalogue.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import http.client
import json
import math
import os
from pathlib import Path
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from xml_utils import atomic_bytes, inspect_xml, plain_text, save_fulltext
from library_store import state_dir, sidecar_path, existing_sidecar, migrate_library

VERSION = "1.9.0"
AGENT = f"JournalXMLDownloader/{VERSION} (personal research)"
FULLTEXT = {"fulltext_structured", "fulltext_rawtext"}
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException)


@dataclass(frozen=True)
class Journal:
    name: str
    slug: str
    issn: str
    link: str = ""

    @property
    def url(self):
        return self.link or "https://www.sciencedirect.com/journal/" + self.slug


PRESETS = (
    Journal("Remote Sensing of Environment", "remote-sensing-of-environment", "0034-4257"),
    Journal("ISPRS Journal of Photogrammetry and Remote Sensing",
            "isprs-journal-of-photogrammetry-and-remote-sensing", "0924-2716"),
)


def normalize_title(text):
    return re.sub(r"[^a-z0-9]", "", html.unescape(text).lower().replace("&", "and"))


def valid_issn(value):
    value = value.upper().strip()
    if not re.fullmatch(r"\d{4}-\d{3}[\dX]", value):
        return False
    digits = value.replace("-", "")
    return (sum(int(digits[i]) * (8 - i) for i in range(7)) +
            (10 if digits[-1] == "X" else int(digits[-1]))) % 11 == 0


def parse_journal_url(value):
    value = value.strip()
    if value.startswith(("www.sciencedirect.com/", "sciencedirect.com/")):
        value = "https://" + value
    url = urllib.parse.urlsplit(value)
    if (url.scheme not in ("http", "https") or url.hostname not in
            ("www.sciencedirect.com", "sciencedirect.com") or url.username or url.password
            or url.port not in (None, 80, 443)):
        raise ValueError("请粘贴 ScienceDirect 的期刊主页地址（不是单篇论文地址）。")
    parts = url.path.strip("/").split("/")
    if len(parts) >= 3 and parts[:2] == ["science", "journal"] and re.fullmatch(r"\d{7}[\dXx]", parts[2]):
        return parts[2].upper(), parts[2].upper()
    if len(parts) >= 2 and parts[0] == "journal" and re.fullmatch(r"[a-z0-9-]+", parts[1]):
        slug = parts[1]
        return slug, slug.replace("-", " ")
    if url.path.rstrip("/") == "/search":
        titles = urllib.parse.parse_qs(url.query).get("pub", [])
        if len(titles) == 1 and titles[0].strip():
            title = titles[0].strip()
            slug = re.sub(r"[^a-z0-9]+", "-", html.unescape(title).lower().replace("&", "and")).strip("-")
            return slug, title
    raise ValueError("支持 /journal/期刊名称 地址，或含 pub=期刊名称 的检索地址。")


def local_journal(value):
    """Resolve catalogue/preset journals offline, without spending API requests."""
    from journal_catalog import lookup
    slug, title = parse_journal_url(value)
    for journal in PRESETS:
        if slug in (journal.slug, journal.issn.replace("-", "")) or normalize_title(title) == normalize_title(journal.name):
            return journal
    item = lookup(slug, title)
    if item and item["issn"]:
        return Journal(item["name"], slug, item["issn"], item["url"])
    return None


@dataclass
class Config:
    api_key: str = field(repr=False)
    url: str
    start_year: int
    end_year: int
    output: Path
    network: str = "direct"
    issn: str = ""
    retry_failed: bool = False
    interval: float = 3.0
    timeout: float = 30.0
    max_downloads: int = 0
    output_mode: str = "xml"
    title_filter: str = ""
    filter_mode: str = "native"
    pdf_browser: str = ""
    search_scope: str = "fulltext"
    search_sort: str = "relevance"
    pdf_method: str = "auto"

    def validate(self):
        parse_journal_url(self.url)
        if not self.api_key.strip() or any(ord(c) > 126 or ord(c) < 33 for c in self.api_key.strip()):
            raise ValueError("请填写有效的 API Key，不能包含空格或换行。")
        if not 1800 <= self.start_year <= self.end_year <= 2100:
            raise ValueError("年份须在 1800–2100 之间，起始年份不能晚于结束年份。")
        if self.network not in ("direct", "system"):
            raise ValueError("网络模式无效。")
        if self.issn and not valid_issn(self.issn):
            raise ValueError("ISSN 格式或校验位不正确，示例：0034-4257。")
        if not str(self.output).strip():
            raise ValueError("请选择保存文件夹。")
        if type(self.max_downloads) is not int or self.max_downloads < 0:
            raise ValueError("下载数量必须是非负整数，0 表示不限。")
        if self.output_mode not in ("xml", "xml_pdf", "pdf"):
            raise ValueError("输出方式无效。")
        if self.pdf_method not in ('auto', 'native', 'browser'):
            raise ValueError("PDF 生成方式无效。")
        from article_search import validate_search
        validate_search(self.title_filter, self.filter_mode, self.search_scope, self.search_sort)


class Cancelled(Exception):
    pass


class ActionRequired(Exception):
    pass


class Control:
    def __init__(self, emit=lambda **event: None):
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.emit = emit

    def checkpoint(self):
        if self.stop.is_set():
            raise Cancelled()
        was_paused = self.paused.is_set()
        if was_paused:
            self.emit(kind="phase", text="已暂停，点击「继续」恢复。")
        while self.paused.is_set():
            if self.stop.wait(0.15):
                raise Cancelled()
        if was_paused:
            self.emit(kind="phase", text="已继续，正在处理…")
        if self.stop.is_set():
            raise Cancelled()

    def wait(self, seconds, reason=""):
        deadline = time.monotonic() + max(0, seconds)
        next_notice = 0
        while True:
            self.checkpoint()
            left = deadline - time.monotonic()
            if left <= 0:
                return
            if reason and time.monotonic() >= next_notice:
                self.emit(kind="phase", text=f"{reason}，{math.ceil(left)} 秒后继续…")
                next_notice = time.monotonic() + 1
            if self.stop.wait(min(left, 0.2)):
                raise Cancelled()


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def save_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def save_lines(path, values):
    atomic_bytes(path, "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values).encode("utf-8"))


def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def latest_log(path):
    latest = {}
    if path.exists():
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                    if item.get("doi"):
                        latest[item["doi"].lower()] = item
                except (ValueError, AttributeError):
                    continue
    return latest


def append_log(path, value):
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell():
            handle.seek(-1, 2)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))


@contextmanager
def _directory_lock(base):
    handle = (base / ".xml_download.lock").open("a+b")
    acquired = False
    try:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise ActionRequired("这个文件夹已有下载任务运行，请先暂停并停止另一个任务。") from None
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def library_lock(base, *, managed=False):
    base = Path(base)
    if not managed:
        with _directory_lock(base):
            yield
        return
    state = state_dir(base)
    state.mkdir(parents=True, exist_ok=True)
    legacy = base / ".xml_download.lock"
    used_legacy = started = False
    try:
        with ExitStack() as stack:
            if legacy.exists():
                stack.enter_context(_directory_lock(base))
                used_legacy = True
            stack.enter_context(_directory_lock(state))
            started = True
            yield
    finally:
        if used_legacy and started:
            try:
                if legacy.read_bytes() == b"0":
                    legacy.unlink()
            except OSError:
                pass


class ElsevierRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if (url.scheme != "https" or url.hostname != "api.elsevier.com" or
                url.port not in (None, 443) or url.username or url.password):
            raise ActionRequired("API 要求跳转到其他服务器，已停止以保护 API Key。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def elsevier_opener(network="direct"):
    handlers = [ElsevierRedirect()]
    if network == "direct":
        handlers.insert(0, urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def retry_seconds(value):
    if not value:
        return None
    try:
        number = float(value)
        return max(0, number) if math.isfinite(number) else None
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, date.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return None


def quota_deadline(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > time.time() else None
    except (ValueError, TypeError):
        return None


def paths_for(base, row):
    doi = row["doi"]
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", doi)[:110] + "_" + hashlib.sha256(doi.encode()).hexdigest()[:8]
    return (base / "fulltext_xml" / str(row["year"]) / (name + ".xml"),
            base / "fulltext_txt" / str(row["year"]) / (name + ".txt"),
            state_dir(base) / "review_required" / (name + ".xml"))


def verify_saved(base, row):
    target, txt, _ = paths_for(base, row)
    # Date corrections must not force a second download of the same DOI.
    candidates = [target] + list((base / "fulltext_xml").glob("*/" + target.name))
    for path in dict.fromkeys(candidates):
        if not path.is_file():
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        checksum = existing_sidecar(path)
        if checksum.exists() and checksum.read_text(encoding="ascii", errors="replace").strip() != digest:
            continue
        result = inspect_xml(data, row["doi"])
        if result["status"] not in FULLTEXT:
            continue
        # Legacy XML gets a baseline only after parsing, DOI and body checks.
        if not checksum.exists():
            atomic_bytes(sidecar_path(path), (digest + "\n").encode("ascii"))
        return {**result, "file": str(path.relative_to(base)), "sha256": digest, "bytes": len(data)}
    return None


def library_path(root, journal):
    from library_inventory import resolve_library
    return resolve_library(root, journal.issn, journal.name)


class Downloader:
    @property
    def state(self):
        return state_dir(self.base)

    def __init__(self, config, emit=lambda **event: None, control=None, search_snapshot=None):
        config.validate()
        self.config = config
        self.key = config.api_key.strip()
        self.callback = emit
        self.control = control or Control()
        self.control.emit = self.emit
        self.base = None
        self.journal = None
        self.records = []
        self.saved = {}
        self.xml_saved = {}
        self.output_processor = None
        self.candidate_total = 0
        self.search_info = {}
        self.search_snapshot = search_snapshot
        self.failures = {}
        self.latest = {}
        self.new_count = 0
        self.reused = 0
        self.download_target = 0
        self.opener = elsevier_opener(config.network)

    def scrub(self, value):
        if isinstance(value, str):
            return value.replace(self.key, "[已隐藏]") if self.key else value
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        return value

    def emit(self, **event):
        self.callback(**self.scrub(event))

    def log(self, text):
        self.emit(kind="log", text=text)

    def public_json(self, url):
        attempts = 0
        while True:
            self.control.checkpoint()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                    return json.loads(self.read_response(response))
            except urllib.error.HTTPError as error:
                if error.code not in (408, 429, 500, 502, 503, 504):
                    raise ActionRequired(f"期刊识别服务返回 HTTP {error.code}，请稍后重试。") from None
                seconds = retry_seconds(error.headers.get("Retry-After"))
                error.close()
            except NETWORK_ERRORS as error:
                if isinstance(getattr(error, "reason", error), ssl.SSLCertVerificationError):
                    raise ActionRequired("期刊识别服务的证书校验失败，请检查网络和系统时间。") from None
                seconds = None
            except (ValueError, KeyError):
                seconds = None
            attempts += 1
            self.control.wait(max(seconds or 0, min(300, 2 ** min(attempts, 8))), "识别期刊失败，自动重试")

    def read_response(self, response):
        chunks, size = [], 0
        while True:
            self.control.checkpoint()
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 128 * 1024 * 1024:
                raise ActionRequired("单个响应超过 128 MB，已停止，请检查返回内容。")
        expected = response.headers.get("Content-Length", "")
        if expected.isdigit() and int(expected) != size:
            raise http.client.IncompleteRead(b"", max(0, int(expected) - size))
        return b"".join(chunks)

    def resolve(self):
        slug, title = parse_journal_url(self.config.url)
        norm = normalize_title(title)
        journal = local_journal(self.config.url)
        if journal:
            if self.config.issn and self.config.issn != journal.issn:
                raise ActionRequired("填写的 ISSN 与所选期刊不一致，请清空高级设置里的 ISSN。")
            return journal
        self.emit(kind="phase", text="正在识别期刊…")
        if re.fullmatch(r"\d{7}[\dX]", slug):
            issn = slug[:4] + "-" + slug[4:]
            if not valid_issn(issn) or (self.config.issn and self.config.issn != issn):
                raise ActionRequired("期刊地址中的 ISSN 无效或与高级设置不一致。")
            item = self.public_json("https://api.crossref.org/journals/" + issn)["message"]
            return Journal(item["title"], slug, issn, self.config.url)
        if self.config.issn:
            item = self.public_json("https://api.crossref.org/journals/" + self.config.issn)["message"]
            if normalize_title(item.get("title", "")) != norm:
                raise ActionRequired("ISSN 对应的期刊名称与地址不匹配，请核对期刊主页和 ISSN。")
            return Journal(item["title"], slug, self.config.issn)
        data = self.public_json("https://api.crossref.org/journals?" + urllib.parse.urlencode({"query": title, "rows": 20}))
        matches = [item for item in data["message"]["items"] if normalize_title(item.get("title", "")) == norm]
        if len(matches) != 1 or not matches[0].get("ISSN"):
            raise ActionRequired("无法唯一识别这个期刊。请在「高级设置」补填期刊 ISSN 后重试。")
        item = matches[0]
        issn = next((x["value"] for x in item.get("issn-type", []) if x.get("type") == "print"), item["ISSN"][0])
        return Journal(item["title"], slug, issn)

    def persist_pause(self, deadline, reason):
        save_json(self.state / "server_pause.json", {"deadline": deadline, "reason": reason})

    def server_wait(self, seconds, reason):
        self.persist_pause(time.time() + seconds, reason)
        self.control.wait(seconds, reason)
        save_json(self.state / "server_pause.json", {"deadline": 0})

    def fetch(self, doi):
        from article_transfer import fetch_article
        return fetch_article(self, doi)

    def report(self, stage):
        pending = [{**row, "last_status": self.failures.get(row["doi"], self.latest.get(row["doi"], {})).get("status", "not_attempted")}
                   for row in self.records if row["doi"] not in self.saved]
        summary = {"checked_at": timestamp(), "stage": stage, "journal": self.journal.name,
                   "issn": self.journal.issn, "years": [self.config.start_year, self.config.end_year],
                   "total": len(self.records), "saved": len(self.saved), "reused": self.reused,
                   "downloaded_this_run": self.new_count, "pending": len(pending),
                   "max_downloads": self.config.max_downloads, "download_target": self.download_target,
                   "failed_or_review": len(self.failures), "source": "Elsevier Article Metadata / ScienceDirect Search; Elsevier XML"}
        summary.update(output_mode=self.config.output_mode, candidate_total=self.candidate_total,
                       excluded=sum(self.search_info.get('excluded', {}).values()) if self.search_info else max(0, self.candidate_total - len(self.records)),
                       search_complete=self.search_info.get('complete', True),
                       search_missing=self.search_info.get('missing', 0),
                       search_notice=self.search_info.get('notice', ''),
                       title_filter=self.config.title_filter, filter_mode=self.config.filter_mode,
                       search_scope=self.config.search_scope, search_sort=self.config.search_sort)
        save_json(self.state / "download_summary.json", summary)
        save_lines(self.state / "pending.jsonl", pending)
        self.emit(kind="counts", **summary)
        if self.output_processor:
            self.emit(kind="pdf_counts", total=len(self.records), saved=len(self.saved),
                      pending=len(pending), stage=stage)

    def initialize_library(self):
        self.journal = self.resolve()
        self.base = library_path(Path(self.config.output), self.journal)
        self.base.mkdir(parents=True, exist_ok=True)
        self.emit(kind="library", path=str(self.base), journal=self.journal.name, issn=self.journal.issn)

    def select_records(self):
        from article_search import collect_search
        result = collect_search(self)
        self.records = result["records"]
        self.candidate_total = result["results_found"]
        self.search_info = result
        self.emit(kind='search_notice', text=result.get('notice', ''))
        if not self.config.title_filter.strip() and result['complete']:
            save_lines(self.state / "manifest.jsonl", self.records)

    def preview(self):
        self.initialize_library()
        with library_lock(self.base, managed=True):
            migrate_library(self.base)
            self.select_records()
            return {"records": self.records, "candidate_total": self.candidate_total,
                    "snapshot": {"result": self.search_info, "state": str(self.state), "checked_at": time.monotonic()},
                    "search_complete": self.search_info.get('complete', True),
                    "search_notice": self.search_info.get('notice', ''),
                    "scope": self.config.search_scope, "query": self.config.title_filter,
                    "journal": self.journal.name, "years": [self.config.start_year, self.config.end_year]}

    def run(self):
        self.initialize_library()
        with library_lock(self.base, managed=True), ExitStack() as stack:
            migrate_library(self.base)
            save_json(self.state / "journal.json", {"name": self.journal.name, "issn": self.journal.issn, "url": self.journal.url})
            self.log(f"期刊：{self.journal.name} · ISSN {self.journal.issn}")
            self.log("Elsevier 网络：" + ("直连（不使用系统 HTTP 代理）" if self.config.network == "direct" else "系统代理"))
            from library_inventory import scan_existing
            self.emit(kind="phase", text="正在读取保存位置中已有的 XML…")
            self.inventory = scan_existing(self.base, checkpoint=self.control.checkpoint)
            self.log(f"本地 XML 校验：{len(self.inventory['saved'])} 篇完整，{self.inventory['invalid']} 个文件未通过检查。")
            self.select_records()
            if self.config.output_mode != "xml":
                from download_output import DownloadOutput
                self.output_processor = DownloadOutput(self.config, self.base, self.emit, self.control)
                stack.enter_context(self.output_processor.session())
                self.emit(kind="phase", text="正在校验已有 PDF…")
                self.output_processor.scan()
            self.latest = latest_log(self.state / "download_log.jsonl")
            try:
                self.download()
            except Cancelled:
                self.report("stopped")
                raise
            except (ActionRequired, OSError):
                self.report("action_required")
                raise

    def download(self):
        total = len(self.records)
        for index, row in enumerate(self.records):
            self.control.checkpoint()
            if index % 10 == 0:
                self.emit(kind="phase", text=f"正在校验已有文件… {index}/{total}")
            found = None
            existing = getattr(self, "inventory", {}).get("saved", {}).get(row["doi"])
            if existing:
                from library_inventory import read_xml
                try:
                    # Check again at use time: the list query may have taken a while.
                    checked = read_xml(Path(existing["path"]))
                    if checked["doi"] == row["doi"]:
                        found = {**checked, "file": existing["file"]}
                        checksum = sidecar_path(Path(existing["path"]))
                        if not existing_sidecar(Path(existing["path"])).exists():
                            atomic_bytes(checksum, (checked["sha256"] + "\n").encode("ascii"))
                except (OSError, ValueError):
                    pass
            if not found:
                found = verify_saved(self.base, row)
            if found:
                self.xml_saved[row["doi"]] = found
            if self.output_processor:
                complete = self.output_processor.reusable(row, found)
                if complete:
                    self.saved[row["doi"]] = complete
            elif found:
                self.saved[row["doi"]] = found
        self.reused = len(self.saved)
        self.log(f"完整性校验结束：{self.reused} / {total} 已保存并通过检查。")
        pending = []
        permanent = {"no_fulltext", "needs_review_identity", "needs_review_unstructured", "doi_mismatch", "http_error", "api_error", "invalid_xml"}
        for row in self.records:
            doi = row["doi"]
            if doi in self.saved:
                continue
            if doi not in self.xml_saved and not self.config.retry_failed and self.latest.get(doi, {}).get("status") in permanent:
                self.failures[doi] = self.latest[doi]
            else:
                pending.append(row)
        self.download_target = min(self.config.max_downloads, len(pending)) if self.config.max_downloads else 0
        self.report("downloading")
        if not pending:
            self.report("finished_with_pending" if len(self.saved) < total else "complete")
            return
        if self.output_processor and self.config.pdf_method == 'browser':
            # Test printing before spending API quota on a PDF output task.
            self.output_processor.exporter.renderer.ensure_ready()
        pause = load_json(self.state / "server_pause.json", {})
        deadline = quota_deadline(pause.get("deadline"))
        network_started = False
        pass_number = 0
        while pending:
            pass_number += 1
            deferred, consecutive = [], 0
            for index, row in enumerate(pending):
                self.control.checkpoint()
                doi = row["doi"]
                existing = self.xml_saved.get(doi)
                data, info = None, dict(existing or {})
                if not existing:
                    if not network_started and deadline:
                        self.control.wait(max(0, deadline - time.time()), pause.get("reason", "等待服务器恢复"))
                    network_started = True
                    self.emit(kind="phase", text=f"正在下载 · {doi}")
                    while True:
                        data, info = self.fetch(doi)
                        if not info.get("wait_until"):
                            break
                        self.server_wait(max(0, info["wait_until"] - time.time()), "服务器限流 / 配额等待")
                result = {"doi": doi, "year": row["year"], "time": timestamp(), **info}
                target, txt, review = paths_for(self.base, row)
                if data is not None:
                    result.update(inspect_xml(data, doi))
                    code = result.get("api_code", "").upper()
                    if "AUTHENTICATION" in code or "AUTHORIZATION" in code:
                        result.update(status="authentication_error", action_required="API 未认可凭据或机构权限，请检查 API Key 与校园网/VPN。")
                    elif "QUOTA" in code:
                        result.update(status="quota_exhausted", action_required="API 配额已用完，请配额恢复后继续。")
                    elif result["status"] in FULLTEXT:
                        result["sha256"] = save_fulltext(target, txt, data, doi)
                        result["file"] = str(target.relative_to(self.base))
                        self.xml_saved[doi] = result.copy()
                    elif result["status"] != "api_error":
                        atomic_bytes(review, data)
                        result["file"] = str(review.relative_to(self.state))
                if result["status"] in FULLTEXT:
                    if self.output_processor:
                        result.update(self.output_processor.process(row, result))
                    if result["status"] in FULLTEXT or result["status"] == "pdf_saved":
                        self.saved[doi] = result.copy()
                        self.failures.pop(doi, None)
                        self.new_count += 1
                status = result["status"]
                if status not in FULLTEXT and status != "pdf_saved":
                    self.failures[doi] = result
                append_log(self.state / "download_log.jsonl", self.scrub(result))
                self.latest[doi] = result
                label = "全文已保存" if status in FULLTEXT else {
                    "pdf_saved": "PDF 已完成", "pdf_pending": "PDF 待处理，XML 已保留",
                    "no_fulltext": "仅返回元数据，未计为全文", "invalid_xml": "XML 不完整，留待检查",
                    "network_error": "网络故障，稍后自动重试", "server_error": "服务器故障，稍后自动重试",
                    "authentication_error": "需要处理权限", "http_error": "该条目无法下载",
                }.get(status, "需要检查")
                self.log(f"{doi} → {label}（HTTP {result.get('http', '—')}）")
                self.report("downloading")
                if result.get("action_required"):
                    raise ActionRequired(result["action_required"])
                if self.config.max_downloads and self.new_count >= self.config.max_downloads:
                    # Finish before any interval, deferred retry or quota wait.
                    # Keep a known quota reset so the next run still respects it.
                    if result.get("remaining") == "0":
                        deadline = quota_deadline(result.get("quota_reset_epoch"))
                        if deadline:
                            self.persist_pause(deadline, "API 配额用完，等待恢复")
                    self.log(f"已达到本次数量：成功下载 {self.new_count} 篇，任务结束。")
                    self.report("limit_reached")
                    return
                if result.get("remaining") == "0" and (index + 1 < len(pending) or deferred):
                    deadline = quota_deadline(result.get("quota_reset_epoch"))
                    if not deadline:
                        raise ActionRequired("本次响应后 API 配额归零；已保存成功文件。请配额恢复后继续。")
                    self.server_wait(deadline - time.time(), "API 配额用完，等待恢复")
                if status in ("network_error", "server_error"):
                    deferred.append(row)
                    consecutive += 1
                    if consecutive >= 3:
                        self.control.wait(min(600, 60 * 2 ** min(pass_number - 1, 4)), "连续连接失败，等待网络恢复")
                        consecutive = 0
                else:
                    consecutive = 0
                if index + 1 < len(pending):
                    self.control.wait(self.config.interval)
            pending = deferred
            if pending:
                self.control.wait(min(600, 60 * 2 ** min(pass_number - 1, 4)), f"剩余 {len(pending)} 条网络失败，稍后重试")
        self.report("finished_with_pending" if len(self.saved) < total else "complete")
