"""GUI controller: worker thread, events and credential-safe settings."""
from datetime import datetime
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog,messagebox
from engine import ActionRequired, Cancelled, Config, Control, Downloader, PRESETS, save_json
from credential_store import load_key, save_key, forget_key
SETTINGS_DIR = Path(os.environ.get("LOCALAPPDATA",str(Path.home()))) / "JournalXMLDownloader"
SETTING_FIELDS = {"url", "start_year", "end_year", "output", "network", "issn", "max_downloads",
                  "remember_key", "output_mode", "title_filter", "filter_mode", "pdf_browser", "search_scope", "search_sort", "pdf_method"}

from library_preview import LibraryPreview
from article_filter import OUTPUT_MODES, FILTER_MODES
from article_search import SCOPES, SORTS
from pdf_official import METHODS


class Controller(LibraryPreview):
    def _variables(self):
        self.remember_key = tk.BooleanVar(value=self.settings.get("remember_key", True))
        remembered = load_key(SETTINGS_DIR) if self.use_key_store and self.remember_key.get() else ""
        self.key = tk.StringVar(value=remembered)
        self.show_key = tk.BooleanVar(value=False)
        self.preset = tk.StringVar()
        self.url = tk.StringVar(value=self.settings.get("url", PRESETS[0].url))
        self.start_year = tk.StringVar(value=str(self.settings.get("start_year", 2024)))
        self.end_year = tk.StringVar(value=str(self.settings.get("end_year", 2027)))
        self.max_downloads = tk.StringVar(value=str(self.settings.get("max_downloads", 0)))
        self.output_mode = tk.StringVar(value=next((label for label, mode in OUTPUT_MODES.items() if mode == self.settings.get("output_mode")), "仅 XML"))
        self.title_filter = tk.StringVar(value=self.settings.get("title_filter", ""))
        legacy_filter = bool(self.settings.get("title_filter")) and "search_scope" not in self.settings
        scope = self.settings.get("search_scope", "local" if legacy_filter else "fulltext")
        self.search_scope = tk.StringVar(value=next((label for label, code in SCOPES.items() if code == scope), next(iter(SCOPES))))
        self.search_sort = tk.StringVar(value=next((label for label, code in SORTS.items() if code == self.settings.get("search_sort")), "相关度优先"))
        self.filter_mode = tk.StringVar(value=next((label for label, mode in FILTER_MODES.items() if mode == self.settings.get("filter_mode")), "任一词组" if scope == "local" else "官网语法"))
        self.search_hint = tk.StringVar()
        self.output = tk.StringVar(value=self.settings.get("output", str(Path.home() / "Documents" / "期刊全文下载")))
        self.direct = tk.BooleanVar(value=self.settings.get("network", "direct") != "system")
        self.issn = tk.StringVar(value=self.settings.get("issn", ""))
        self.refresh_index = tk.BooleanVar(value=False)
        self.retry_failed = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="准备就绪")
        self.detail = tk.StringVar(value="填入 API Key，选择期刊与年份，然后开始下载。")
        self.metrics = {name: tk.StringVar(value="—") for name in ("total", "saved", "reused", "pending")}
        self.percent = tk.StringVar(value="0%")
        self.saved_caption = tk.StringVar(value="已保存全文")
        self.active_job = "xml"
        self.local_hint = tk.StringVar(value="正在读取保存位置…")
        self.pdf_status = tk.StringVar(value="PDF · 尚未合成")
        self.pdf_browser = tk.StringVar(value=self.settings.get("pdf_browser", ""))
        self.pdf_browser_caption = tk.StringVar(value=self._browser_caption())
        self.pdf_method = tk.StringVar(value=next((label for label, code in METHODS.items()
            if code == self.settings.get('pdf_method')), next(iter(METHODS))))

    def _browser_caption(self):
        from browser_detection import browser_name
        path = self.pdf_browser.get()
        return f"{browser_name(path)} · {path}" if path else "自动检测（优先 Microsoft Edge）"

    def _set_pdf_browser(self, value):
        if self.worker and self.worker.is_alive():
            return
        self.pdf_browser.set(value)
        self.pdf_browser_caption.set(self._browser_caption())
        self.settings["pdf_browser"] = value
        try:
            save_json(SETTINGS_DIR / "settings.json", {k: v for k, v in self.settings.items() if k in SETTING_FIELDS})
        except OSError:
            self._log("浏览器选择仅在本次运行有效，设置文件未保存。")

    def _choose_pdf_browser(self, parent):
        if self.worker and self.worker.is_alive():
            return
        from browser_detection import selected_browser
        value = filedialog.askopenfilename(parent=parent, title="选择 Edge 或 Chrome 程序",
            filetypes=[("浏览器程序（msedge.exe / chrome.exe）", "*.exe")])
        if not value:
            return
        try:
            path = selected_browser(value)
        except (OSError, ValueError) as error:
            messagebox.showerror("请选择浏览器程序", str(error), parent=parent)
            return
        self._set_pdf_browser(str(path))

    def editable(self, widget, state="normal"):
        self.mutable.append((widget, state))
        return widget

    def _toggle_key(self):
        self.key_entry.configure(show="" if self.show_key.get() else "●")

    def _persist_api_key(self, event=None):
        if not self.use_key_store or getattr(self, "_credentials_closed", False):
            return
        try:
            self._remember_form()
            if self.remember_key.get():
                save_key(SETTINGS_DIR, self.key.get())
            else:
                forget_key(SETTINGS_DIR)
            self.settings["remember_key"] = self.remember_key.get()
            save_json(SETTINGS_DIR / "settings.json", {k: v for k, v in self.settings.items() if k in SETTING_FIELDS})
        except (OSError, ValueError):
            self._log("Key 本机记忆未成功，当前仍可使用；请检查 Key 格式或本机文件权限。")

    def _remember_form(self):
        # Remember valid form selections even when the user only browsed a library.
        for name, variable in (("url", self.url), ("output", self.output), ("issn", self.issn)):
            if variable.get().strip() or name == "issn":
                self.settings[name] = variable.get().strip()
        for name, variable in (("start_year", self.start_year), ("end_year", self.end_year), ("max_downloads", self.max_downloads)):
            try:
                value = int(variable.get())
                if (0 <= value if name == "max_downloads" else 1800 <= value <= 2100):
                    self.settings[name] = value
            except ValueError:
                pass
        self.settings["network"] = "direct" if self.direct.get() else "system"
        self.settings.update(output_mode=OUTPUT_MODES[self.output_mode.get()], title_filter=self.title_filter.get().strip(),
                             filter_mode=FILTER_MODES[self.filter_mode.get()], pdf_browser=self.pdf_browser.get(),
                             search_scope=SCOPES[self.search_scope.get()], search_sort=SORTS[self.search_sort.get()])
        self.settings['pdf_method'] = METHODS[self.pdf_method.get()]

    def _browse(self):
        folder = filedialog.askdirectory(title="选择保存位置或已有 XML 文库", initialdir=self.output.get() if Path(self.output.get()).exists() else str(Path.home()))
        if folder:
            self.output.set(folder)

    def _log(self, text):
        self._log_batch([text])

    def _log_batch(self, messages):
        if not messages:
            return
        key = self.key.get().strip()
        stamp = datetime.now().strftime("%H:%M:%S")
        text = "".join(stamp + "  " + (message.replace(key, "[已隐藏]") if key else message) + "\n"
                       for message in messages)
        self.logbox.configure(state="normal")
        self.logbox.insert("end", text)
        lines = int(self.logbox.index("end-1c").split(".")[0])
        if lines > 800:
            self.logbox.delete("1.0", f"{lines - 600}.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _save_settings(self, cfg):
        # Explicit allowlist: never serialize Config or any credential wholesale.
        self.settings = {
            "url": cfg.url, "start_year": cfg.start_year, "end_year": cfg.end_year,
            "output": str(cfg.output), "network": cfg.network, "issn": cfg.issn,
            "max_downloads": cfg.max_downloads,
            "output_mode": cfg.output_mode, "title_filter": cfg.title_filter, "filter_mode": cfg.filter_mode,
            "pdf_browser": cfg.pdf_browser,
            "pdf_method": cfg.pdf_method,
            "search_scope": cfg.search_scope, "search_sort": cfg.search_sort,
            "remember_key": self.remember_key.get(),
        }
        save_json(SETTINGS_DIR / "settings.json", self.settings)

    def _busy(self, busy):
        if busy:
            self._cancel_library_scan()
        for widget, normal_state in self.mutable:
            widget.configure(state="disabled" if busy else normal_state)
        self.start_button.configure(state="disabled" if busy else "normal")
        self.pause_button.configure(state="normal" if busy else "disabled", text="暂停")
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.update_button.configure(state="disabled" if busy else "normal")

    def start(self, preview=False):
        if self.worker and self.worker.is_alive():
            return
        try:
            if not self.output.get().strip():
                raise ValueError("请选择保存文件夹。")
            try:
                start, end = int(self.start_year.get()), int(self.end_year.get())
            except ValueError:
                raise ValueError("起止年份必须填写整数。") from None
            try:
                maximum = int(self.max_downloads.get().strip())
            except ValueError:
                raise ValueError("下载数量必须是非负整数，0 表示不限。") from None
            cfg = Config(api_key=self.key.get().strip(), url=self.url.get().strip(), start_year=start,
                         end_year=end, output=Path(self.output.get().strip()).expanduser().resolve(),
                         network="direct" if self.direct.get() else "system", issn=self.issn.get().strip().upper(),
                         refresh=self.refresh_index.get(), retry_failed=self.retry_failed.get(), max_downloads=maximum,
                         output_mode=OUTPUT_MODES[self.output_mode.get()], title_filter=self.title_filter.get().strip(),
                         filter_mode=FILTER_MODES[self.filter_mode.get()], pdf_browser=self.pdf_browser.get(),
                         search_scope=SCOPES[self.search_scope.get()], search_sort=SORTS[self.search_sort.get()],
                         pdf_method=METHODS[self.pdf_method.get()])
            cfg.validate()
            if self.advanced_window and self.advanced_window.winfo_exists():
                self.advanced_window.destroy()
            for name in ("search_window", "search_results_window"):
                win = getattr(self, name, None)
                if win and win.winfo_exists():
                    win.destroy()
            self._save_settings(cfg)
            self._persist_api_key()
        except (ValueError, OSError) as error:
            messagebox.showerror("请检查下载设置", str(error), parent=self)
            return
        self.library = None
        self._cancel_library_scan()
        if getattr(self, "catalog_window", None) and self.catalog_window.winfo_exists():
            self.catalog_window.destroy()
        self.active_job = "search" if preview else "xml"
        self.search_notice = ''
        self.pipeline_pdf = not preview and cfg.output_mode != "xml"
        self.saved_caption.set("完整输出" if self.pipeline_pdf else "已保存全文")
        if self.pipeline_pdf:
            self.pipeline_counts = {}
            self.pdf_status.set("PDF · 下载后逐篇合成")
            self.pdf_progress.configure(mode="determinate", progress_color="#087F8C")
            self.pdf_progress.set(0)
        self.stages = ""
        self.control = Control()
        self._busy(True)
        for var in self.metrics.values():
            var.set("—")
        self.progress.configure(mode="indeterminate")
        self.progress.set(0)
        self.progress.start()
        self.percent.set("准备中")
        self.status.set("正在准备")
        self.detail.set("正在识别期刊并建立论文列表…")
        self._log(f"{'预览检索' if preview else '开始任务'}：{start}–{end} 年。")
        if preview:
            self._log("只获取论文列表，不下载全文；相同条件可复用检索缓存。")
        else:
            self._log(f"输出方式：{self.output_mode.get()}。" + ("每篇 PDF 校验完成后，再处理下一篇。" if self.pipeline_pdf else ""))
            if self.pipeline_pdf:
                self._log("PDF 方式：" + self.pdf_method.get() + ("。不会自动启动浏览器。" if cfg.pdf_method != 'browser' else "。已选择浏览器排版。"))
            self._log(f"本次最多成功下载 {maximum} 篇；已保存的完整文件不计入。" if maximum else "下载数量不限。")
        if end > datetime.now().year:
            self._log("所选范围包含未来年份，只能获取目前已登记、已发布且有权限的内容。")
        self.worker = threading.Thread(target=self._work, args=(cfg, self.control, preview), daemon=False)
        self.worker.start()

    def _work(self, cfg, control, preview=False):
        runner = None
        outcome = "complete"
        text = "本次任务结束。"
        try:
            runner = Downloader(cfg, emit=lambda **event: self.events.put(event), control=control)
            if preview:
                result = runner.preview()
                self.events.put({"kind": "search_results", **result})
                text = f"检索完成：可处理 {len(result['records'])} 条。核对预览后可开始下载。"
            else:
                runner.run()
        except Cancelled:
            outcome, text = "stopped", "已停止。再次开始会校验已有文件并继续下载。"
        except ActionRequired as error:
            outcome, text = "action_required", str(error)
        except Exception as error:
            outcome, text = "error", f"{type(error).__name__}：{error}。请检查保存位置、网络后重试。"
        finally:
            text = text.replace(cfg.api_key, "[已隐藏]") if cfg.api_key else text
            self.events.put({"kind": "done", "outcome": outcome, "text": text})
            if runner:
                runner.key = ""
                if runner.output_processor:
                    runner.output_processor.exporter.key = ""
                    runner.output_processor.exporter.cfg.api_key = ""
            cfg.api_key = ""

    def check_pdf_environment(self):
        if self.worker and self.worker.is_alive():
            return
        report = filedialog.asksaveasfilename(parent=self.pdf_window, title="保存 PDF 环境诊断",
            initialfile="PDF环境诊断.json", defaultextension=".json", filetypes=[("诊断记录", "*.json")])
        if not report:
            return
        self.pdf_window.destroy()
        self._cancel_library_scan()
        self.active_job, self.pipeline_pdf, self.stages = "diagnostic", False, ""
        self.control = Control()
        self._busy(True)
        self.status.set("正在检测 PDF 环境")
        self._log("离线检测 PDF 环境，不读取论文或 API Key，不消耗 API 配额。")
        browser = self.pdf_browser.get()  # Read Tk state only on the UI thread.
        def work():
            outcome, text = "complete", f"PDF 环境检测通过。诊断文件：{report}"
            try:
                from pdf_browser import diagnose
                diagnose(report, self.control, lambda **event: self.events.put(event), browser=browser)
            except Cancelled:
                outcome, text = "stopped", "已停止 PDF 环境检测。"
            except ActionRequired as error:
                outcome, text = "action_required", str(error)
            except Exception as error:
                outcome, text = "error", f"诊断未完成：{error}"
            self.events.put({"kind": "done", "outcome": outcome, "text": text})
        self.worker = threading.Thread(target=work, daemon=False)
        self.worker.start()

    def start_pdf(self, source, layout="double"):
        if self.worker and self.worker.is_alive():
            return
        from pdf_export import PdfConfig, PdfExporter
        try:
            if not source.strip():
                raise ValueError("请选择含已下载 XML 的文库文件夹。")
            cfg = PdfConfig(source=Path(source).expanduser().resolve(), api_key=self.key.get().strip(),
                            network="direct" if self.direct.get() else "system",
                            start_year=int(self.start_year.get()), end_year=int(self.end_year.get()),
                            layout=layout, browser=self.pdf_browser.get(), method=METHODS[self.pdf_method.get()])
            cfg.validate()
            self._persist_api_key()
        except (ValueError, OSError) as error:
            messagebox.showerror("请检查 PDF 设置", str(error), parent=self)
            return
        if getattr(self, "pdf_window", None) and self.pdf_window.winfo_exists():
            self.pdf_window.destroy()
        from library_inventory import normalize_root
        self.output.set(str(normalize_root(cfg.source)))
        self.active_job = "pdf"
        self.pipeline_pdf = False
        self._cancel_library_scan()
        if getattr(self, "catalog_window", None) and self.catalog_window.winfo_exists():
            self.catalog_window.destroy()
        self.pdf_status.set("PDF · 正在读取 XML…")
        self.pdf_progress.configure(progress_color="#087F8C", mode="indeterminate")
        self.pdf_progress.set(0)
        self.pdf_progress.start()
        self.saved_caption.set("已合成 PDF")
        self.stages = ""
        self.control = Control()
        self._busy(True)
        for variable in self.metrics.values():
            variable.set("—")
        self.progress.configure(mode="indeterminate")
        self.progress.set(0)
        self.progress.start()
        self.percent.set("准备中")
        self.status.set("准备合成 PDF")
        self.detail.set("扫描现有 XML，校验 PDF 与图片缓存…")
        self._log(f"开始生成 PDF：{cfg.start_year}–{cfg.end_year} 年，{self.pdf_method.get()}。")
        self.worker = threading.Thread(target=self._pdf_work, args=(cfg, PdfExporter), daemon=False)
        self.worker.start()

    def _pdf_work(self, cfg, factory):
        runner = None
        outcome, text = "complete", "PDF 合成结束。"
        try:
            runner = factory(cfg, emit=lambda **event: self.events.put(event), control=self.control)
            runner.run()
        except Cancelled:
            outcome, text = "stopped", "已停止合成。再次开始会校验 PDF 并复用已缓存图片。"
        except ActionRequired as error:
            outcome, text = "action_required", str(error)
        except Exception as error:
            outcome, text = "error", f"{type(error).__name__}：{error}"
        finally:
            if cfg.api_key:
                text = text.replace(cfg.api_key, "[已隐藏]")
            self.events.put({"kind": "done", "outcome": outcome, "text": text})
            if runner:
                runner.key = ""
            cfg.api_key = ""

    def pause(self):
        if not self.control:
            return
        if self.control.paused.is_set():
            self.control.paused.clear()
            self.pause_button.configure(text="暂停")
            self.status.set("继续运行")
        else:
            self.control.paused.set()
            self.pause_button.configure(text="继续")
            self.status.set("正在暂停")
            self.detail.set("正在等待当前网络操作返回；已有文件已保存。")

    def stop(self):
        if self.control:
            self.control.stop.set()
            self.pause_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.status.set("正在停止")
            self.detail.set("等待当前网络操作返回后安全停止，通常不超过 30 秒。")

    def open_folder(self):
        folder = self.library or Path(self.output.get()).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except OSError as error:
            messagebox.showerror("无法打开文件夹", str(error), parent=self)

    def _poll(self):
        if getattr(self, "_poll_after", None):
            self.after_cancel(self._poll_after)
            self._poll_after = None
        # Bound time, not just event count: hundreds of Tk redraws can otherwise
        # monopolize the event loop on slower CPUs. Preserve event order.
        deadline = time.perf_counter() + .008
        logs = []
        for _ in range(200):
            if time.perf_counter() >= deadline:
                break
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event["kind"]
            if kind == "log":
                logs.append(event["text"])
                continue
            self._log_batch(logs)
            logs.clear()
            if kind == "update_notice":
                self._update_notice_event(event)
            elif kind == "library_preview":
                self._library_preview_event(event)
            elif kind == "search_results":
                if not self.closing:
                    self._show_search_results(event)
            elif kind == 'search_notice':
                self.search_notice = event['text']
            elif kind == "library_scan_progress":
                if (event["generation"] == self.scan_generation and not self.closing
                        and not (self.worker and self.worker.is_alive())):
                    self.local_hint.set(f"正在校验当前目录 · 已检查 {event['checked']} 个文件…")
            elif kind in ("update_result", "update_progress"):
                if self._update_event(event):
                    return
            elif kind == "library":
                self.library = Path(event["path"])
                self._log("保存目录：" + event["path"])
            elif kind == "phase":
                if not self.control or not self.control.stop.is_set():
                    self.detail.set(event["text"])
                    self.status.set("已暂停" if event["text"].startswith("已暂停") else
                                    "等待恢复" if "秒后继续" in event["text"] else "正在处理")
            elif kind == "counts":
                self.progress.stop()
                self.progress.configure(mode="determinate")
                target = event.get("download_target", 0)
                if event.get("max_downloads") and target:
                    current = event["downloaded_this_run"]
                    self.progress.set(min(1, current / target))
                    self.percent.set(f"{current / target:.0%} · 本次 {current}/{target} 篇")
                else:
                    self.progress.set(event["saved"] / max(1,event["total"]))
                    self.percent.set(f"{event['saved'] / event['total']:.0%}" if event["total"] else "—")
                self.downloaded_this_run = event.get("downloaded_this_run", 0)
                for name, variable in self.metrics.items():
                    variable.set(str(event[name]))
                self.stages = event["stage"]
                if event.get("title_filter"):
                    label = "本地题名筛选" if event.get("search_scope", "local") == "local" else "ScienceDirect 检索"
                    self.local_hint.set(f"{label}：{event['total']}/{event['candidate_total']} 条 · {self.output_mode.get()}")
                if self.active_job == "pdf":
                    self.pdf_progress.stop()
                    self.pdf_progress.configure(mode="determinate")
                    self.pdf_progress.set(event["saved"] / max(1, event["total"]))
                    self.pdf_status.set(f"PDF · 已完成 {event['saved']}/{event['total']} 篇")
            elif kind == "pdf_counts":
                self.pdf_progress.stop()
                self.pdf_progress.configure(mode="determinate", progress_color="#087F8C")
                self.pdf_progress.set(event["saved"] / max(1, event["total"]))
                self.pdf_status.set(f"本次范围 · PDF {event['saved']}/{event['total']} 篇")
                self.pipeline_counts = event
            elif kind == "done":
                self.progress.stop()
                self._busy(False)
                outcome = event["outcome"]
                text = event["text"]
                if outcome == "complete":
                    if self.active_job == "search":
                        self.status.set("检索完成")
                        self.progress.configure(mode="determinate")
                        self.progress.set(0)
                        self.percent.set("待下载")
                    elif self.active_job == "diagnostic":
                        self.status.set("PDF 环境检测通过")
                    elif self.active_job == "pdf":
                        if self.stages == "pdf_finished_with_pending":
                            self.status.set("合成结束 · 部分待处理")
                            text = "部分 XML 或图片需要检查，请查看运行记录；详细记录保存在本机应用数据中，再次合成可重试。"
                        elif self.metrics["total"].get() == "0":
                            self.status.set("未找到 XML")
                            text = "所选文件夹和年份范围内未找到 XML，请选择已有文库或 fulltext_xml 文件夹。"
                        else:
                            self.status.set("PDF 合成完成")
                            text = "PDF 已保存并通过校验。自动方式优先保存官方原版，其余为 XML 阅读版；点击「打开文件夹」查看。"
                    elif self.stages == "limit_reached":
                        self.status.set("已达到本次数量")
                        text = f"本次成功下载 {self.downloaded_this_run} 篇。再次开始会跳过完整文件，继续下载剩余论文。"
                    elif self.stages == "finished_with_pending":
                        self.status.set("已结束 · 部分条目待处理")
                        text = "未保存条目已记录到本机应用数据；可勾选「重新尝试」后再次开始。"
                    elif self.metrics["total"].get() == "0":
                        self.status.set("未找到记录")
                        text = "当前期刊、年份与检索条件没有可处理的记录。可调整条件，或勾选「更新论文列表」重试。"
                    else:
                        self.status.set("下载完成")
                        text = "所选列表中的条目均已保存并通过校验。"
                else:
                    self.status.set({"stopped": "已停止", "action_required": "需要处理", "error": "遇到错误"}[outcome])
                if self.active_job in ('search', 'xml') and getattr(self, 'search_notice', ''):
                    text += '\n' + self.search_notice
                    if outcome == 'complete':
                        self.status.set('已结束 · 检索列表待补齐')
                if self.active_job == "pdf":
                    self.pdf_progress.stop()
                    if outcome == "complete" and self.stages == "pdf_finished" and self.metrics["total"].get() != "0":
                        self.pdf_progress.configure(progress_color="#24976C")
                        self.pdf_progress.set(1)
                        self.pdf_status.set(f"PDF 已生成完成 · {self.metrics['saved'].get()} 篇")
                    elif outcome == "complete" and self.metrics["total"].get() == "0":
                        self.pdf_progress.set(0)
                        self.pdf_status.set("PDF · 未找到可合成的 XML")
                    elif outcome == "complete":
                        self.pdf_progress.configure(progress_color="#C58A30")
                        self.pdf_status.set(f"PDF · {self.metrics['saved'].get()} 篇完成，{self.metrics['pending'].get()} 篇待处理")
                    else:
                        self.pdf_progress.configure(progress_color="#C58A30")
                        self.pdf_status.set("PDF · " + self.status.get())
                elif getattr(self, "pipeline_pdf", False):
                    counts = getattr(self, "pipeline_counts", {})
                    saved, total = counts.get("saved", 0), counts.get("total", 0)
                    if outcome == "complete" and saved == total and total:
                        self.pdf_progress.configure(progress_color="#24976C")
                        self.pdf_status.set(f"PDF 已生成完成 · {saved} 篇")
                    elif outcome == "complete" and self.stages == "limit_reached":
                        self.pdf_status.set(f"本次 PDF 已生成完成 · 新增 {self.downloaded_this_run} 篇")
                    else:
                        self.pdf_progress.configure(progress_color="#C58A30")
                        self.pdf_status.set(f"PDF · 完成 {saved}/{total} 篇 · {self.status.get()}")
                self.detail.set(text)
                self._log(text)
                if self.closing:
                    self.destroy()
                    return
        self._log_batch(logs)
        self._poll_after = self.after(16 if not self.events.empty() else 120, self._poll)

    def close(self):
        self._cancel_library_scan()
        self._persist_api_key()
        self._credentials_closed = True
        self._cancel_update_notice()
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.stop()
            self.detail.set("正在安全结束任务并关闭窗口，请稍候…")
        else:
            self.key.set("")
            self.destroy()
