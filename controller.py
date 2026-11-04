"""GUI controller: worker thread, events and credential-safe settings."""
from datetime import datetime
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog,messagebox
from engine import (ActionRequired,Cancelled,Config,Control,Downloader,PRESETS,save_json,
                    local_journal, library_path, load_json, Journal, parse_journal_url, valid_issn)
from credential_store import load_key, save_key, forget_key
SETTINGS_DIR = Path(os.environ.get("LOCALAPPDATA",str(Path.home()))) / "JournalXMLDownloader"

class Controller:
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

    def _init_library_watch(self):
        self.scan_generation = 0
        self.scan_after = None
        self.scan_cancel = None
        if not self.scan_libraries:
            return
        for variable in (self.output, self.url, self.start_year, self.end_year, self.issn):
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
        except (OSError, ValueError) as error:
            self.local_hint.set(str(error))
            self.library = None
            return
        generation = self.scan_generation
        detect = self.scan_detect
        cancel = self.scan_cancel = threading.Event()
        def work():
            from library_inventory import scan_existing, detect_journal
            from library_store import state_dir
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
                cache = load_json(state_dir(base) / "indexes" / f"{start}-{end}.json", {})
                if not cache:
                    cache = load_json(base / "indexes" / f"{start}-{end}.json", {})
                total = None
                listed_saved = 0
                if (isinstance(cache, dict) and cache.get("complete") is True and cache.get("issn") == (target or journal).issn
                        and cache.get("years") == [start, end] and isinstance(cache.get("records"), list)):
                    rows = cache["records"]
                    if all(isinstance(row, dict) and isinstance(row.get("doi"), str) for row in rows):
                        dois = {row["doi"].lower() for row in rows}
                        total = len(dois)
                        listed_saved = len(dois.intersection(inventory["saved"]))
                checkpoint()
                self.events.put({"kind": "library_preview", "generation": generation, "inventory": inventory,
                                 "total": total, "listed_saved": listed_saved,
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
        self.saved_caption.set("本地完整 XML")
        self.metrics["saved"].set(str(count))
        self.metrics["reused"].set(str(count))
        self.metrics["total"].set(str(total) if total is not None else "待获取")
        self.metrics["pending"].set(str(max(0, total - event["listed_saved"])) if total is not None else "待获取")
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(event["listed_saved"] / max(1, total) if total is not None else 0)
        self.percent.set(f"{event['listed_saved'] / total:.0%}" if total else "本地已读取")
        self.status.set("可继续下载" if count else "准备就绪")
        self.detail.set("开始后复用已有列表并校验 XML，完整论文会跳过；缺失或损坏的论文继续下载。")

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
            allowed = {"url", "start_year", "end_year", "output", "network", "issn", "max_downloads", "remember_key"}
            save_json(SETTINGS_DIR / "settings.json", {k: v for k, v in self.settings.items() if k in allowed})
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

    def _browse(self):
        folder = filedialog.askdirectory(title="选择保存位置或已有 XML 文库", initialdir=self.output.get() if Path(self.output.get()).exists() else str(Path.home()))
        if folder:
            self.output.set(folder)

    def _log(self, text):
        key = self.key.get().strip()
        if key:
            text = text.replace(key, "[已隐藏]")
        self.logbox.configure(state="normal")
        self.logbox.insert("end", datetime.now().strftime("%H:%M:%S") + "  " + text + "\n")
        if int(self.logbox.index("end-1c").split(".")[0]) > 800:
            self.logbox.delete("1.0", "200.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _save_settings(self, cfg):
        # Explicit allowlist: never serialize Config or any credential wholesale.
        self.settings = {
            "url": cfg.url, "start_year": cfg.start_year, "end_year": cfg.end_year,
            "output": str(cfg.output), "network": cfg.network, "issn": cfg.issn,
            "max_downloads": cfg.max_downloads,
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

    def start(self):
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
                         refresh=self.refresh_index.get(), retry_failed=self.retry_failed.get(), max_downloads=maximum)
            cfg.validate()
            if self.advanced_window and self.advanced_window.winfo_exists():
                self.advanced_window.destroy()
            self._save_settings(cfg)
            self._persist_api_key()
        except (ValueError, OSError) as error:
            messagebox.showerror("请检查下载设置", str(error), parent=self)
            return
        self.library = None
        self._cancel_library_scan()
        if getattr(self, "catalog_window", None) and self.catalog_window.winfo_exists():
            self.catalog_window.destroy()
        self.active_job = "xml"
        self.saved_caption.set("已保存全文")
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
        self._log(f"开始任务：{start}–{end} 年。")
        self._log(f"本次最多成功下载 {maximum} 篇；已保存的完整文件不计入。" if maximum else "下载数量不限。")
        if end > datetime.now().year:
            self._log("所选范围包含未来年份，只能获取目前已登记、已发布且有权限的内容。")
        self.worker = threading.Thread(target=self._work, args=(cfg, self.control), daemon=False)
        self.worker.start()

    def _work(self, cfg, control):
        runner = None
        outcome = "complete"
        text = "本次任务结束。"
        try:
            runner = Downloader(cfg, emit=lambda **event: self.events.put(event), control=control)
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
            cfg.api_key = ""

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
                            layout=layout)
            cfg.validate()
            self._persist_api_key()
        except (ValueError, OSError) as error:
            messagebox.showerror("请检查 PDF 设置", str(error), parent=self)
            return
        if getattr(self, "pdf_window", None) and self.pdf_window.winfo_exists():
            self.pdf_window.destroy()
        self.active_job = "pdf"
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
        self._log(f"开始合成 PDF：{cfg.start_year}–{cfg.end_year} 年，{'双栏' if layout == 'double' else '单栏'}排版。")
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
        for _ in range(200):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event["kind"]
            if kind == "log":
                self._log(event["text"])
            elif kind == "update_notice":
                self._update_notice_event(event)
            elif kind == "library_preview":
                self._library_preview_event(event)
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
                if self.active_job == "pdf":
                    self.pdf_progress.stop()
                    self.pdf_progress.configure(mode="determinate")
                    self.pdf_progress.set(event["saved"] / max(1, event["total"]))
                    self.pdf_status.set(f"PDF · 已完成 {event['saved']}/{event['total']} 篇")
            elif kind == "done":
                self.progress.stop()
                self._busy(False)
                outcome = event["outcome"]
                text = event["text"]
                if outcome == "complete":
                    if self.active_job == "pdf":
                        if self.stages == "pdf_finished_with_pending":
                            self.status.set("合成结束 · 部分待处理")
                            text = "部分 XML 或图片需要检查，请查看运行记录；详细记录保存在本机应用数据中，再次合成可重试。"
                        elif self.metrics["total"].get() == "0":
                            self.status.set("未找到 XML")
                            text = "所选文件夹和年份范围内未找到 XML，请选择已有文库或 fulltext_xml 文件夹。"
                        else:
                            self.status.set("PDF 合成完成")
                            text = "PDF 已保存并通过校验。点击「打开文件夹」查看；生成文件为 XML 重排阅读版。"
                    elif self.stages == "limit_reached":
                        self.status.set("已达到本次数量")
                        text = f"本次成功下载 {self.downloaded_this_run} 篇。再次开始会跳过完整文件，继续下载剩余论文。"
                    elif self.stages == "finished_with_pending":
                        self.status.set("已结束 · 部分条目待处理")
                        text = "未保存条目已记录到本机应用数据；可勾选「重新尝试」后再次开始。"
                    elif self.metrics["total"].get() == "0":
                        self.status.set("未找到记录")
                        text = "所选年份暂无已登记的论文。可调整年份，或勾选「更新论文列表」重试。"
                    else:
                        self.status.set("下载完成")
                        text = "所选列表中的条目均已保存并通过校验。"
                else:
                    self.status.set({"stopped": "已停止", "action_required": "需要处理", "error": "遇到错误"}[outcome])
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
                self.detail.set(text)
                self._log(text)
                if self.closing:
                    self.destroy()
                    return
        self.after(120, self._poll)

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
