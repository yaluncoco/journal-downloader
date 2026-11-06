"""Debounced, generation-safe library preview; no network and no writes."""
from pathlib import Path
import threading

from engine import Cancelled, Journal, library_path, local_journal, parse_journal_url, valid_issn, load_json
from article_filter import filter_records, OUTPUT_MODES, FILTER_MODES


class LibraryPreview:
    def _init_library_watch(self):
        self.scan_generation = 0
        self.scan_after = None
        self.scan_cancel = None
        if not self.scan_libraries:
            return
        for variable in (self.output, self.url, self.start_year, self.end_year, self.issn,
                         self.output_mode, self.title_filter, self.filter_mode):
            variable.trace_add("write", self._queue_library_scan)
        self._queue_library_scan()

    def _cancel_library_scan(self):
        self.scan_generation = getattr(self, "scan_generation", 0) + 1
        if getattr(self, "scan_after", None):
            self.after_cancel(self.scan_after)
            self.scan_after = None
        if getattr(self, "scan_cancel", None):
            self.scan_cancel.set()

    def _queue_library_scan(self, *_):
        self.scan_detect = not _ or _[0] == str(self.output)
        self._cancel_library_scan()
        if self.closing or (self.worker and self.worker.is_alive()):
            return
        self.library = None
        self.local_hint.set("正在读取保存位置…")
        self.pdf_progress.stop()
        self.pdf_progress.configure(mode="determinate", progress_color="#087F8C")
        self.pdf_progress.set(0)
        self.pdf_status.set("PDF · 正在读取当前目录…")
        self.scan_after = self.after(450, self._start_library_scan)

    def _start_library_scan(self):
        self.scan_after = None
        if self.worker and self.worker.is_alive():
            return
        try:
            if not self.output.get().strip():
                raise ValueError("请选择保存位置。")
            start, end = int(self.start_year.get()), int(self.end_year.get())
            if not 1800 <= start <= end <= 2100:
                raise ValueError("请填写有效的年份范围。")
            try:
                journal = local_journal(self.url.get())
            except ValueError:
                journal = None
            if not journal and valid_issn(self.issn.get()):
                slug, name = parse_journal_url(self.url.get())
                journal = Journal(name, slug, self.issn.get().upper())
            if not journal and not self.scan_detect:
                self.local_hint.set("请选择目录中的期刊；自定义期刊将在开始时识别。")
                self.library = None
                return
            root = Path(self.output.get().strip()).expanduser()
            query, filter_mode = self.title_filter.get(), FILTER_MODES[self.filter_mode.get()]
            output_mode = OUTPUT_MODES[self.output_mode.get()]
            filter_records([], query, filter_mode)
        except (OSError, ValueError) as error:
            self.local_hint.set(str(error))
            self.library = None
            self.pdf_status.set("PDF · 请检查保存位置与筛选设置")
            return
        generation = self.scan_generation
        detect = self.scan_detect
        cancel = self.scan_cancel = threading.Event()
        def work():
            from library_inventory import scan_existing, detect_journal
            from library_store import state_dir
            from pdf_inventory import scan_pdfs
            from pdf_export import RENDERER_VERSION
            def checkpoint():
                if cancel.is_set():
                    raise Cancelled()
            try:
                detected = detect_journal(root) if detect else None
                target = local_journal(detected["url"]) if detected else journal
                if target is None:
                    self.events.put({"kind": "library_preview", "generation": generation,
                                     "error": "请选择目录中的期刊；自定义期刊将在开始时识别。"})
                    return
                base = library_path(root, target or journal)
                inventory = scan_existing(base, start, end, checkpoint)
                pdfs = scan_pdfs(base, start, end, checkpoint)
                xml_dois, pdf_dois = set(inventory["saved"]), set(pdfs["saved"])
                compatible = {doi for doi in pdf_dois if doi not in xml_dois or
                    pdfs["saved"][doi].get("xml_sha256") == inventory["saved"][doi]["sha256"]}
                desired_pdfs = {doi for doi in compatible if pdfs["saved"][doi].get("layout") == "double"
                                and pdfs["saved"][doi].get("renderer") == RENDERER_VERSION}
                complete = xml_dois if output_mode == "xml" else desired_pdfs if output_mode == "pdf" else xml_dois & desired_pdfs
                cache = load_json(state_dir(base) / "indexes" / f"{start}-{end}.json", {})
                if not cache:
                    cache = load_json(base / "indexes" / f"{start}-{end}.json", {})
                total = None
                listed_saved = 0
                candidate_total = None
                if (isinstance(cache, dict) and cache.get("complete") is True and cache.get("issn") == (target or journal).issn
                        and cache.get("years") == [start, end] and isinstance(cache.get("records"), list)):
                    rows = cache["records"]
                    if all(isinstance(row, dict) and isinstance(row.get("doi"), str) for row in rows):
                        candidate_total = len(rows)
                        dois = {row["doi"].lower() for row in filter_records(rows, query, filter_mode)}
                        total = len(dois)
                        listed_saved = len(dois.intersection(complete))
                checkpoint()
                self.events.put({"kind": "library_preview", "generation": generation, "inventory": inventory,
                                 "total": total, "listed_saved": listed_saved,
                                 "local_complete": len(complete), "candidate_total": candidate_total,
                                 "pdf_saved": len(compatible), "pdf_total": len(xml_dois | pdf_dois | pdfs["invalid_dois"])
                                     + max(0, pdfs["invalid"] - len(pdfs["invalid_dois"])) + pdfs["unverified"],
                                 "pdf_invalid": pdfs["invalid"], "pdf_unverified": pdfs["unverified"],
                                 "detected_url": detected["url"] if detected else ""})
            except Cancelled:
                return
            except Exception:
                self.events.put({"kind": "library_preview", "generation": generation,
                                 "error": "无法读取该保存位置，请检查目录权限后重试。"})
        self.scan_worker = threading.Thread(target=work, daemon=True)
        self.scan_worker.start()

    def _library_preview_event(self, event):
        if event["generation"] != self.scan_generation or (self.worker and self.worker.is_alive()) or self.closing:
            return
        if event.get("error"):
            self.local_hint.set(event["error"])
            self.pdf_status.set("PDF · 当前目录无法读取")
            return
        try:
            selected = local_journal(self.url.get())
        except ValueError:
            selected = None
        if event.get("detected_url") and local_journal(event["detected_url"]) != selected:
            self.issn.set("")
            self.url.set(event["detected_url"])
            self._select_from_url()
            # The current scan already validated the detected library.
            self._cancel_library_scan()
        info = event["inventory"]
        count, bad = len(info["saved"]), info["invalid"]
        self.library = Path(info["base"])
        self.local_hint.set(f"已识别 {count} 篇完整 XML" + (f" · {bad} 个文件待检查" if bad else "") + " · 开始后自动续传")
        total = event["total"]
        complete = event.get("local_complete", count)
        self.saved_caption.set("本地完整 XML" if self.output_mode.get() == "仅 XML" else "本地完整输出")
        self.metrics["saved"].set(str(event["listed_saved"] if total is not None else complete))
        self.metrics["reused"].set(self.metrics["saved"].get())
        self.metrics["total"].set(str(total) if total is not None else "待获取")
        self.metrics["pending"].set(str(max(0, total - event["listed_saved"])) if total is not None else "待获取")
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(event["listed_saved"] / max(1, total) if total is not None else 0)
        self.percent.set(f"{event['listed_saved'] / total:.0%}" if total else "本地已读取")
        self.status.set("可继续下载" if count else "准备就绪")
        self.detail.set("开始后按输出方式校验并续传；缺失或损坏的文件会继续处理。" +
                        (f"题名筛选匹配 {total}/{event['candidate_total']} 条。" if self.title_filter.get().strip() and total is not None else ""))
        saved, expected = event.get("pdf_saved", 0), event.get("pdf_total", count)
        self.pdf_progress.stop()
        self.pdf_progress.configure(mode="determinate", progress_color="#24976C" if expected and saved == expected else "#087F8C")
        self.pdf_progress.set(saved / max(1, expected))
        note = " · 已全部生成" if expected and saved == expected else ""
        if event.get("pdf_unverified"):
            note += f" · {event['pdf_unverified']} 个无校验记录"
        if event.get("pdf_invalid"):
            note += f" · {event['pdf_invalid']} 个待修复"
        self.pdf_status.set(f"当前目录 · PDF {saved}/{expected}{note}" if expected else "当前目录 · 暂无 XML / PDF")
