"""Compact update dialog with a single, silent version check at startup."""
from pathlib import Path
import json
import os
import sys
import threading

import customtkinter as ctk

from controller import SETTINGS_DIR
from engine import Cancelled, Control, VERSION, library_lock, save_json
from updater import Updater, launch_install, prepare_install, version_tuple, installation_status
from window_layout import fit_window, wrap_to, scroll_body


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
        win.transient(self)
        fit_window(win, (490, 320), (380, 260))
        win.grid_columnconfigure(0, weight=1)
        body = scroll_body(win)
        self.label(body, "软件更新", font=ctk.CTkFont("Microsoft YaHei UI", 22, weight="bold")).grid(row=0, column=0, sticky="w", padx=6, pady=(12, 6))
        self.label(body, f"当前版本 v{VERSION}", font=self.small_font, color="#768798").grid(row=1, column=0, sticky="w", padx=6)
        self.update_message = ctk.StringVar(value="正在检查 GitHub 正式版本…")
        wrap_to(self.label(body, "", textvariable=self.update_message, font=self.body_font,
                   justify="left", height=65), padding=12).grid(row=2, column=0, sticky="ew", padx=6, pady=(16, 10))
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.grid(row=1, column=0, sticky="e", padx=26, pady=16)
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
                        event["install"], event["record"] = prepare_install(downloaded, digest, release.tag)
                control.checkpoint()
        except Cancelled:
            event["error"] = "已取消更新。"
        except Exception as error:
            event["error"] = f"更新未完成：{error}"
            try:
                save_json(SETTINGS_DIR / "updates" / "last-error.json", {
                    "version": VERSION, "operation": operation, "error": str(error)})
            except OSError:
                pass
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
                suffix = "下载后将关闭当前窗口并打开安装向导。完成安装后可启动新版，论文和设置会保留。" if getattr(sys, "frozen", False) else "下载完成后请运行安装包。"
                self.update_message.set(f"发现新版本 {release.tag}。\n{suffix}")
                self.update_action.configure(state="normal", text="下载并安装" if getattr(sys, "frozen", False) else "下载安装包",
                    command=lambda: self._run_update("download", release))
                self.status.set("发现新版本")
            else:
                self.update_message.set("当前已是最新版本。")
                self.update_action.configure(state="normal", text="完成", command=self._close_update)
                self.status.set("已是最新版本")
        elif "install" in event:
            try:
                launch_install(event["install"], event["record"])
            except (OSError, ValueError) as error:
                self.update_message.set(f"无法启动更新：{error}")
                self.update_action.configure(state="normal", text="关闭", command=self._close_update)
                return
            self._persist_api_key()
            self._credentials_closed = True
            self.key.set("")
            self._cancel_update_notice()
            self._cancel_library_scan()
            self.destroy()
            return True
        else:
            folder = Path(event["downloaded"]).parent
            self.update_message.set("安装包已下载并通过校验。\n请打开文件夹并运行安装包。")
            self.update_action.configure(state="normal", text="打开文件夹", command=lambda: os.startfile(str(folder)))
            self.status.set("新版已下载")

    def _report_update_result(self):
        if not getattr(sys, "frozen", False) or not self.use_key_store:
            return
        try:
            state, result = installation_status(SETTINGS_DIR / "updates")
            if state == "complete":
                self.status.set(f"已更新至 v{VERSION}")
                self._log(f"新版 v{VERSION} 已成功启动。")
            elif state == "pending":
                self.detail.set("仍在运行旧版本；如上次取消了安装，可点击更新箭头重新安装。")
                self._log("安装尚未确认完成；安装日志：" + result.get("log", ""))
        except (OSError, ValueError, AttributeError):
            pass
