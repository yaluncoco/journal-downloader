"""Compact update dialog with a single, silent version check at startup."""
from pathlib import Path
import json
import os
import sys
import threading

import customtkinter as ctk

from controller import SETTINGS_DIR
from engine import Cancelled, Control, VERSION, library_lock
from updater import Updater, launch_install, prepare_install, version_tuple


class UpdateUI:
    def _set_update_available(self, release):
        self.update_available = version_tuple(release.tag) > version_tuple(VERSION)
        if self.update_available:
            self.update_badge.place(relx=1, x=-2, y=2, anchor="ne")
            self.update_badge.lift()
        else:
            self.update_badge.place_forget()

    def _cancel_update_notice(self):
        self.update_notice_generation = getattr(self, "update_notice_generation", 0) + 1
        control = getattr(self, "update_notice_control", None)
        if control:
            control.stop.set()

    def _start_update_notice(self):
        if self.closing or getattr(self, "update_notice_started", False):
            return
        self.update_notice_started = True
        self._cancel_update_notice()
        self.update_notice_control = Control()
        self.update_notice_worker = threading.Thread(target=self._update_notice_work,
            args=(self.update_notice_control, self.update_notice_generation), daemon=True)
        self.update_notice_worker.start()

    def _update_notice_work(self, control, generation):
        try:
            release = Updater(control).latest()
            control.checkpoint()
        except Exception:
            # An unavailable GitHub connection must not disturb a download.
            return
        self.events.put({"kind": "update_notice", "release": release, "generation": generation})

    def _update_notice_event(self, event):
        if not self.closing and event.get("generation") == getattr(self, "update_notice_generation", None):
            self._set_update_available(event["release"])

    def _update_dialog(self):
        if self.worker and self.worker.is_alive():
            return
        if getattr(self, "update_window", None) and self.update_window.winfo_exists():
            self.update_window.focus()
            return
        win = ctk.CTkToplevel(self, fg_color="#F4F7FA")
        self.update_window = win
        win.title("软件更新")
        win.geometry("450x270")
        win.resizable(False, False)
        win.transient(self)
        win.grid_columnconfigure(0, weight=1)
        self.label(win, "软件更新", font=ctk.CTkFont("Microsoft YaHei UI", 22, weight="bold")).grid(row=0, column=0, sticky="w", padx=26, pady=(24, 6))
        self.label(win, f"当前版本 v{VERSION}", font=self.small_font, color="#768798").grid(row=1, column=0, sticky="w", padx=26)
        self.update_message = ctk.StringVar(value="正在检查 GitHub 正式版本…")
        self.label(win, "", textvariable=self.update_message, font=self.body_font,
                   wraplength=390, justify="left", height=65).grid(row=2, column=0, sticky="ew", padx=26, pady=(16, 10))
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.grid(row=3, column=0, sticky="e", padx=26, pady=16)
        self.secondary(row, "关闭", self._close_update, width=80).pack(side="left", padx=(0, 10))
        self.update_action = ctk.CTkButton(row, text="检查中…", state="disabled", height=40,
            width=135, corner_radius=10, font=self.body_font, fg_color="#087F8C", hover_color="#066774")
        self.update_action.pack(side="left")
        win.protocol("WM_DELETE_WINDOW", self._close_update)
        win.after(100, lambda: win.grab_set() if win.winfo_exists() else None)
        self._run_update("check")

    def _close_update(self):
        self.update_cancel_close = True
        if self.worker and self.worker.is_alive():
            self.control.stop.set()
            self.update_message.set("正在取消，请稍候…")
            self.update_action.configure(state="disabled")
        elif self.update_window.winfo_exists():
            self.update_window.destroy()

    def _run_update(self, operation, release=None):
        if operation == "check":
            self.update_notice_started = True
            self._cancel_update_notice()
        self.control = Control()
        self.update_cancel_close = False
        self.update_request = getattr(self, "update_request", 0) + 1
        self._busy(True)
        self.pause_button.configure(state="disabled")
        self.update_action.configure(state="disabled", text="检查中…" if operation == "check" else "下载中…")
        self.status.set("检查更新" if operation == "check" else "下载更新")
        self.worker = threading.Thread(target=self._update_work, args=(operation, release, self.control, self.update_request), daemon=False)
        self.worker.start()

    def _update_work(self, operation, release, control, request):
        event = {"kind": "update_result", "operation": operation, "request": request}
        try:
            client = Updater(control, progress=lambda text: self.events.put({"kind": "update_progress", "text": text, "request": request}))
            if operation == "check":
                event["release"] = client.latest()
            else:
                directory = SETTINGS_DIR / "updates" / release.tag
                directory.mkdir(parents=True, exist_ok=True)
                with library_lock(directory):
                    downloaded, digest = client.download(release, directory)
                    control.checkpoint()
                    event["downloaded"] = str(downloaded)
                    if getattr(sys, "frozen", False):
                        event["install"], result = prepare_install(downloaded, digest)
                control.checkpoint()
        except Cancelled:
            event["error"] = "已取消更新。"
        except Exception as error:
            event["error"] = f"更新未完成：{error}"
        self.events.put(event)

    def _update_event(self, event):
        if event.get("request") != getattr(self, "update_request", None):
            return
        if event["kind"] == "update_progress":
            self.update_message.set(event["text"])
            return
        self._busy(False)
        if self.closing:
            self.key.set("")
            self.destroy()
            return True
        if getattr(self, "update_cancel_close", False):
            if self.update_window.winfo_exists():
                self.update_window.destroy()
            self.status.set("已取消更新")
            return
        if "error" in event:
            self.update_message.set(event["error"])
            self.status.set("更新未完成")
            self.update_action.configure(state="normal", text="重新检查", command=lambda: self._run_update("check"))
            self._log(event["error"])
        elif event["operation"] == "check":
            release = event["release"]
            self._set_update_available(release)
            if self.update_available:
                suffix = "更新后会自动重新打开软件。" if getattr(sys, "frozen", False) else "下载后可直接运行新的 EXE。"
                self.update_message.set(f"发现新版本 {release.tag}。\n{suffix}")
                self.update_action.configure(state="normal", text="下载并更新" if getattr(sys, "frozen", False) else "下载新版",
                    command=lambda: self._run_update("download", release))
                self.status.set("发现新版本")
            else:
                self.update_message.set("当前已是最新版本。")
                self.update_action.configure(state="normal", text="完成", command=self._close_update)
                self.status.set("已是最新版本")
        elif "install" in event:
            try:
                launch_install(event["install"])
            except OSError as error:
                self.update_message.set(f"无法启动更新：{error}")
                self.update_action.configure(state="normal", text="关闭", command=self._close_update)
                return
            self._persist_api_key()
            self._credentials_closed = True
            self.key.set("")
            self._cancel_update_notice()
            self.destroy()
            return True
        else:
            folder = Path(event["downloaded"]).parent
            self.update_message.set("新版 EXE 已下载并通过校验。\n源码运行模式请手动打开新程序。")
            self.update_action.configure(state="normal", text="打开文件夹", command=lambda: os.startfile(str(folder)))
            self.status.set("新版已下载")

    def _report_update_result(self):
        try:
            records = list((SETTINGS_DIR / "updates").glob("*/install-result.json"))
            if not records:
                return
            newest = max(records, key=lambda path: path.stat().st_mtime)
            result = json.loads(newest.read_text(encoding="utf-8-sig"))
            if result.get("ok") is False:
                self.status.set("上次更新未完成")
                self.detail.set("已保留原版本。可点击右上角向上箭头重试更新。")
                self._log("上次更新未完成：" + str(result.get("error", "")))
        except (OSError, ValueError, AttributeError):
            pass
