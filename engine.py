"""Parameterized, resumable Elsevier XML downloader. Only the standard library.

No credential is written to disk. Crossref supplies a dated candidate list;
Elsevier's authenticated Article Retrieval API supplies entitled full text.
"""
from __future__ import annotations

from contextlib import contextmanager
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

VERSION = "1.0.0"
AGENT = f"JournalXMLDownloader/{VERSION} (personal research)"
FULLTEXT = {"fulltext_structured", "fulltext_rawtext"}
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException)


@dataclass(frozen=True)
class Journal:
    name: str
    slug: str
    issn: str

    @property
    def url(self):
        return "https://www.sciencedirect.com/journal/" + self.slug


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


@dataclass
class Config:
    api_key: str = field(repr=False)
    url: str
    start_year: int
    end_year: int
    output: Path
    network: str = "direct"
    issn: str = ""
    refresh: bool = False
    retry_failed: bool = False
    interval: float = 3.0
    timeout: float = 30.0

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
def library_lock(base):
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


def date_value(item, field):
    parts = (item.get(field) or {}).get("date-parts", [[]])[0]
    return "-".join(str(n) if i == 0 else f"{n:02d}" for i, n in enumerate(parts))


def metadata_record(item, start, end):
    doi = str(item.get("DOI", "")).strip().lower()
    if not doi.startswith("10.") or "/" not in doi:
        return None
    field = next((f for f in ("published-print", "published", "published-online", "issued")
                  if date_value(item, f)), None)
    date = date_value(item, field) if field else ""
    if not date[:4].isdigit() or not start <= int(date[:4]) <= end:
        return None
    title = html.unescape((item.get("title") or [""])[0])
    links = item.get("link", [])
    pii = next((m.group(0) for link in links if
                (m := re.search(r"S\d{15}[\dX]", link.get("URL", "")))), None)
    return {"doi": doi, "title": title, "journal": item.get("container-title", []),
            "year": int(date[:4]), "date": date, "date_source": field,
            "published_online": date_value(item, "published-online"), "pii": pii,
            "volume": item.get("volume"), "issue": item.get("issue"),
            "possible_correction_or_editorial": bool(re.search(
                "corrigendum|erratum|editorial|retraction|withdrawn|expression of concern", title, re.I)),
            "crossref_type": item.get("type")}


def paths_for(base, row):
    doi = row["doi"]
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", doi)[:110] + "_" + hashlib.sha256(doi.encode()).hexdigest()[:8]
    return (base / "fulltext_xml" / str(row["year"]) / (name + ".xml"),
            base / "fulltext_txt" / str(row["year"]) / (name + ".txt"),
            base / "review_required" / (name + ".xml"))


def verify_saved(base, row):
    target, txt, _ = paths_for(base, row)
    # Date corrections must not force a second download of the same DOI.
    candidates = [target] + list((base / "fulltext_xml").glob("*/" + target.name))
    for path in dict.fromkeys(candidates):
        if not path.is_file():
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        checksum = path.with_suffix(".xml.sha256")
        if checksum.exists() and checksum.read_text(encoding="ascii", errors="replace").strip() != digest:
            continue
        result = inspect_xml(data, row["doi"])
        if result["status"] not in FULLTEXT:
            continue
        # Legacy XML gets a baseline only after parsing, DOI and body checks.
        if not checksum.exists():
            atomic_bytes(checksum, (digest + "\n").encode("ascii"))
        txt = base / "fulltext_txt" / path.parent.name / path.with_suffix(".txt").name
        if not txt.exists():
            atomic_bytes(txt, plain_text(data, row["doi"]).encode("utf-8"))
        return {**result, "file": str(path.relative_to(base)), "sha256": digest, "bytes": len(data)}
    return None


def library_path(root, journal):
    for filename in ("journal.json", "index_summary.json"):
        old = load_json(root / filename, {})
        if old.get("issn") == journal.issn:
            return root
    # ISSN is the stable identifier, independent of URL spelling or year range.
    return root / journal.issn


class Downloader:
    def __init__(self, config, emit=lambda **event: None, control=None):
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
        self.failures = {}
        self.latest = {}
        self.new_count = 0
        self.reused = 0
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
                    raise ActionRequired(f"论文列表服务返回 HTTP {error.code}，请稍后重试。") from None
                seconds = retry_seconds(error.headers.get("Retry-After"))
                error.close()
            except NETWORK_ERRORS as error:
                if isinstance(getattr(error, "reason", error), ssl.SSLCertVerificationError):
                    raise ActionRequired("论文列表服务的证书校验失败，请检查网络和系统时间。") from None
                seconds = None
            except (ValueError, KeyError):
                seconds = None
            attempts += 1
            self.control.wait(max(seconds or 0, min(300, 2 ** min(attempts, 8))), "获取论文列表失败，自动重试")

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
        for journal in PRESETS:
            if slug == journal.slug or norm == normalize_title(journal.name):
                if self.config.issn and self.config.issn != journal.issn:
                    raise ActionRequired("填写的 ISSN 与所选期刊不一致，请清空高级设置里的 ISSN。")
                return journal
        self.emit(kind="phase", text="正在识别期刊…")
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

    def collect(self):
        cfg = self.config
        path = self.base / "indexes" / f"{cfg.start_year}-{cfg.end_year}.json"
        cached = load_json(path, {})
        if (not cfg.refresh and cached.get("complete") is True and cached.get("issn") == self.journal.issn
                and cached.get("years") == [cfg.start_year, cfg.end_year]):
            records = cached.get("records")
            if isinstance(records, list) and all(isinstance(r, dict) and isinstance(r.get("doi"), str)
                    and isinstance(r.get("year"), int) and cfg.start_year <= r["year"] <= cfg.end_year for r in records):
                if len({r["doi"] for r in records}) == len(records):
                    self.log(f"使用已保存的论文列表：{len(records)} 条（{cached.get('created_at', '')}）。")
                    return records
        # Reuse the verified CLI's finished index without requiring a new network query.
        legacy = load_json(self.base / "index_summary.json", {})
        bounds = legacy.get("requested_years", [])
        if (not cfg.refresh and legacy.get("issn") == self.journal.issn and len(bounds) == 2
                and bounds[0] <= cfg.start_year <= cfg.end_year <= bounds[1]
                and legacy.get("queries") and all(q.get("exhausted") for q in legacy["queries"])):
            try:
                old_records = [json.loads(line) for line in (self.base / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
                if (len(old_records) == legacy.get("records") and
                        len({r["doi"] for r in old_records}) == len(old_records)):
                    records = [r for r in old_records if cfg.start_year <= r["year"] <= cfg.end_year]
                    save_json(path, {"complete": True, "created_at": legacy.get("checked_at"),
                                    "issn": self.journal.issn, "years": [cfg.start_year, cfg.end_year],
                                    "source": "verified CLI index", "records": records})
                    self.log(f"已复用原命令行文库的完整列表：{len(records)} 条。")
                    return records
            except (OSError, ValueError, KeyError, TypeError):
                self.log("原有列表不完整，将重新获取论文列表。")
        raw, queries = {}, []
        for mode in ("print-pub-date", "pub-date"):
            cursor, seen, cursors = "*", set(), set()
            while True:
                self.control.checkpoint()
                self.emit(kind="phase", text=f"正在获取论文列表… 已发现 {len(raw)} 条记录")
                params = {"filter": f"from-{mode}:{cfg.start_year}-01-01,until-{mode}:{cfg.end_year}-12-31",
                          "rows": 500, "cursor": cursor,
                          "select": "DOI,title,container-title,published-print,published,published-online,issued,volume,issue,type,link"}
                data = self.public_json(f"https://api.crossref.org/journals/{self.journal.issn}/works?" +
                                        urllib.parse.urlencode(params))["message"]
                items = data["items"]
                if not items:
                    queries.append({"mode": mode, "total": data["total-results"], "retrieved": len(seen), "exhausted": True})
                    break
                before = len(seen)
                for item in items:
                    doi = item["DOI"].lower()
                    seen.add(doi)
                    raw[doi] = item
                next_cursor = data.get("next-cursor")
                if len(seen) == before or not next_cursor:
                    raise ActionRequired("论文列表分页没有继续前进，尚未生成完整列表；请稍后重新开始。")
                # Crossref may return the same opaque cursor; progress is measured by DOIs.
                cursors.add(cursor)
                cursor = next_cursor
                self.log(f"论文列表：{len(seen)} / {data['total-results']}（{mode}）")
                self.control.wait(1)
        records = [record for item in raw.values() if (record := metadata_record(item, cfg.start_year, cfg.end_year))]
        records.sort(key=lambda r: (r["date"], r["doi"]))
        save_json(path, {"complete": True, "created_at": timestamp(), "issn": self.journal.issn,
                         "years": [cfg.start_year, cfg.end_year], "queries": queries, "records": records})
        self.log(f"论文列表已建立：{len(records)} 条。包含勘误、编委会等已登记条目。")
        return records

    def persist_pause(self, deadline, reason):
        save_json(self.base / "server_pause.json", {"deadline": deadline, "reason": reason})

    def server_wait(self, seconds, reason):
        self.persist_pause(time.time() + seconds, reason)
        self.control.wait(seconds, reason)
        save_json(self.base / "server_pause.json", {"deadline": 0})

    def fetch(self, doi):
        url = "https://api.elsevier.com/content/article/doi/" + urllib.parse.quote(doi, safe="/") + "?view=FULL"
        for attempt in range(6):
            self.control.checkpoint()
            headers = {"X-ELS-APIKey": self.key, "Accept": "text/xml", "User-Agent": AGENT,
                       "X-ELS-ReqId": str(uuid.uuid4()), "Accept-Encoding": "identity"}
            info = {"attempts": attempt + 1}
            try:
                with self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.config.timeout) as response:
                    data = self.read_response(response)
                    info.update(http=response.status, remaining=response.headers.get("X-RateLimit-Remaining"),
                                quota_reset_epoch=response.headers.get("X-RateLimit-Reset"), bytes=len(data))
                if self.key.encode() in data:
                    raise ActionRequired("API 响应中含有凭据，已停止且未保存该响应。")
                return data, info
            except urllib.error.HTTPError as error:
                code, response_headers = error.code, error.headers
                try:
                    message = error.read(3000).decode("utf-8", "replace")
                except NETWORK_ERRORS:
                    message = ""
                finally:
                    error.close()
                info.update(http=code, message=self.scrub(message)[:800])
                if code in (401, 403):
                    return None, {**info, "status": "authentication_error", "action_required":
                        f"HTTP {code}：API Key 或机构权限未被认可。请确认校园网/VPN，并保持「直连 Elsevier」；TUN 全局代理仍可能影响出口。修复后再开始，已有文件会保留。"}
                quota = (response_headers.get("X-RateLimit-Remaining") == "0" or
                         "QUOTA" in (message + response_headers.get("X-ELS-Status", "")).upper())
                if code == 429 and quota:
                    deadline = quota_deadline(response_headers.get("X-RateLimit-Reset"))
                    if not deadline:
                        return None, {**info, "status": "quota_exhausted", "action_required": "API 配额已用完，服务器未给出有效恢复时间。请配额恢复后再继续。"}
                    return None, {**info, "status": "quota_wait", "wait_until": deadline}
                required = retry_seconds(response_headers.get("Retry-After"))
                if code not in (408, 429, 500, 502, 503, 504):
                    return None, {**info, "status": "http_error"}
                info["status"] = "network_error" if code == 408 else "server_error"
                if code == 429 or required:
                    return None, {**info, "status": "server_wait", "wait_until": time.time() + max(60 if code == 429 else 2, required or 0)}
            except NETWORK_ERRORS as error:
                reason = getattr(error, "reason", error)
                if isinstance(reason, ssl.SSLCertVerificationError):
                    return None, {**info, "status": "certificate_error", "action_required": "HTTPS 证书校验失败，请检查系统时间或校园网证书配置。"}
                info.update(status="network_error", message=self.scrub(str(reason))[:600])
            if attempt == 5:
                return None, info
            self.control.wait(min(60, 2 ** (attempt + 1)), f"连接暂时失败，重试 {attempt + 1}/5")

    def report(self, stage):
        pending = [{**row, "last_status": self.failures.get(row["doi"], self.latest.get(row["doi"], {})).get("status", "not_attempted")}
                   for row in self.records if row["doi"] not in self.saved]
        summary = {"checked_at": timestamp(), "stage": stage, "journal": self.journal.name,
                   "issn": self.journal.issn, "years": [self.config.start_year, self.config.end_year],
                   "total": len(self.records), "saved": len(self.saved), "reused": self.reused,
                   "downloaded_this_run": self.new_count, "pending": len(pending),
                   "failed_or_review": len(self.failures), "source": "Crossref candidate list; Elsevier XML"}
        save_json(self.base / "download_summary.json", summary)
        save_lines(self.base / "pending.jsonl", pending)
        self.emit(kind="counts", **summary)

    def run(self):
        self.journal = self.resolve()
        self.base = library_path(Path(self.config.output), self.journal)
        self.base.mkdir(parents=True, exist_ok=True)
        self.emit(kind="library", path=str(self.base), journal=self.journal.name, issn=self.journal.issn)
        with library_lock(self.base):
            save_json(self.base / "journal.json", {"name": self.journal.name, "issn": self.journal.issn, "url": self.journal.url})
            self.log(f"期刊：{self.journal.name} · ISSN {self.journal.issn}")
            self.log("Elsevier 网络：" + ("直连（不使用系统 HTTP 代理）" if self.config.network == "direct" else "系统代理"))
            self.records = self.collect()
            save_lines(self.base / "manifest.jsonl", self.records)
            self.latest = latest_log(self.base / "download_log.jsonl")
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
            found = verify_saved(self.base, row)
            if found:
                self.saved[row["doi"]] = found
        self.reused = len(self.saved)
        self.log(f"完整性校验结束：{self.reused} / {total} 已保存并通过检查。")
        self.report("downloading")
        pending = []
        permanent = {"no_fulltext", "needs_review_identity", "needs_review_unstructured", "doi_mismatch", "http_error", "api_error", "invalid_xml"}
        for row in self.records:
            doi = row["doi"]
            if doi in self.saved:
                continue
            if not self.config.retry_failed and self.latest.get(doi, {}).get("status") in permanent:
                self.failures[doi] = self.latest[doi]
            else:
                pending.append(row)
        if not pending:
            self.report("finished_with_pending" if len(self.saved) < total else "complete")
            return
        pause = load_json(self.base / "server_pause.json", {})
        deadline = quota_deadline(pause.get("deadline"))
        if deadline:
            self.control.wait(deadline - time.time(), pause.get("reason", "等待服务器恢复"))
        pass_number = 0
        while pending:
            pass_number += 1
            deferred, consecutive = [], 0
            for index, row in enumerate(pending):
                self.control.checkpoint()
                doi = row["doi"]
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
                        self.saved[doi] = result.copy()
                        self.failures.pop(doi, None)
                        self.new_count += 1
                    elif result["status"] != "api_error":
                        atomic_bytes(review, data)
                        result["file"] = str(review.relative_to(self.base))
                status = result["status"]
                if status not in FULLTEXT:
                    self.failures[doi] = result
                append_log(self.base / "download_log.jsonl", self.scrub(result))
                self.latest[doi] = result
                label = "全文已保存" if status in FULLTEXT else {
                    "no_fulltext": "仅返回元数据，未计为全文", "invalid_xml": "XML 不完整，留待检查",
                    "network_error": "网络故障，稍后自动重试", "server_error": "服务器故障，稍后自动重试",
                    "authentication_error": "需要处理权限", "http_error": "该条目无法下载",
                }.get(status, "需要检查")
                self.log(f"{doi} → {label}（HTTP {result.get('http', '—')}）")
                self.report("downloading")
                if result.get("action_required"):
                    raise ActionRequired(result["action_required"])
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
