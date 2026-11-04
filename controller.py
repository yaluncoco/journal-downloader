"""GUI controller: worker thread, events and credential-safe settings."""
from datetime import datetime
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog,messagebox
from engine import ActionRequired,Cancelled,Config,Control,Downloader,PRESETS,save_json
SETTINGS_DIR = Path(os.environ.get("LOCALAPPDATA",str(Path.home()))) / "JournalXMLDownloader"

class Controller:
    def _variables(self):
        self.key = tk.StringVar()
        self.show_key = tk.BooleanVar(value=False)
        self.preset = tk.StringVar()
        self.url = tk.StringVar(value=self.settings.get("url", PRESETS[0].url))
        self.start_year = tk.StringVar(value=str(self.settings.get("start_year", 2024)))
        self.end_year = tk.StringVar(value=str(self.settings.get("end_year", 2027)))
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

    def editable(self, widget, state="normal"):
        self.mutable.append((widget, state))
        return widget

    def _toggle_key(self):
        self.key_entry.configure(show="" if self.show_key.get() else "●")

    def _browse(self):
        folder = filedialog.askdirectory(title="选择保存文件夹（也可选择原有 RSE 下载目录）", initialdir=self.output.get() if Path(self.output.get()).exists() else str(Path.home()))
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
        save_json(SETTINGS_DIR / "settings.json", {
            "url": cfg.url, "start_year": cfg.start_year, "end_year": cfg.end_year,
            "output": str(cfg.output), "network": cfg.network, "issn": cfg.issn,
        })

    def _busy(self, busy):
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
            cfg = Config(api_key=self.key.get().strip(), url=self.url.get().strip(), start_year=start,
                         end_year=end, output=Path(self.output.get().strip()).expanduser().resolve(),
                         network="direct" if self.direct.get() else "system", issn=self.issn.get().strip().upper(),
                         refresh=self.refresh_index.get(), retry_failed=self.retry_failed.get())
            cfg.validate()
            if self.advanced_window and self.advanced_window.winfo_exists():
                self.advanced_window.destroy()
            self._save_settings(cfg)
        except (ValueError, OSError) as error:
            messagebox.showerror("请检查下载设置", str(error), parent=self)
            return
        self.library = None
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
        except (ValueError, OSError) as error:
            messagebox.showerror("请检查 PDF 设置", str(error), parent=self)
            return
        if getattr(self, "pdf_window", None) and self.pdf_window.winfo_exists():
            self.pdf_window.destroy()
        self.active_job = "pdf"
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
                self.progress.set(event["saved"] / max(1,event["total"]))
                self.percent.set(f"{event['saved'] / event['total']:.0%}" if event["total"] else "—")
                for name, variable in self.metrics.items():
                    variable.set(str(event[name]))
                self.stages = event["stage"]
            elif kind == "done":
                self.progress.stop()
                self._busy(False)
                outcome = event["outcome"]
                text = event["text"]
                if outcome == "complete":
                    if self.active_job == "pdf":
                        if self.stages == "pdf_finished_with_pending":
                            self.status.set("合成结束 · 部分待处理")
                            text = "部分 XML 或图片需要检查，详见 pdf_pending.jsonl；再次合成会重试并跳过完整 PDF。"
                        elif self.metrics["total"].get() == "0":
                            self.status.set("未找到 XML")
                            text = "所选文件夹和年份范围内未找到 XML，请选择已有文库或 fulltext_xml 文件夹。"
                        else:
                            self.status.set("PDF 合成完成")
                            text = "PDF 已保存并通过校验。点击「打开文件夹」查看；生成文件为 XML 重排阅读版。"
                    elif self.stages == "finished_with_pending":
                        self.status.set("已结束 · 部分条目待处理")
                        text = "未保存的条目见 pending.jsonl；可勾选「重新尝试」后再次开始。"
                    elif self.metrics["total"].get() == "0":
                        self.status.set("未找到记录")
                        text = "所选年份暂无已登记的论文。可调整年份，或勾选「更新论文列表」重试。"
                    else:
                        self.status.set("下载完成")
                        text = "所选列表中的条目均已保存并通过校验。"
                else:
                    self.status.set({"stopped": "已停止", "action_required": "需要处理", "error": "遇到错误"}[outcome])
                self.detail.set(text)
                self._log(text)
                if self.closing:
                    self.destroy()
                    return
        self.after(120, self._poll)

    def close(self):
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.stop()
            self.detail.set("正在安全结束任务并关闭窗口，请稍候…")
        else:
            self.key.set("")
            self.destroy()
