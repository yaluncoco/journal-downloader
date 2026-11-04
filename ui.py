"""Rounded, DPI-aware light interface. Network work lives in engine.py."""
from pathlib import Path
import queue
import sys

import customtkinter as ctk
from PIL import Image

from controller import Controller, SETTINGS_DIR
from engine import PRESETS, VERSION, load_json, parse_journal_url

BG = "#F4F7FA"
WHITE = "#FFFFFF"
INK = "#182C3C"
MUTED = "#768798"
TEAL = "#087F8C"
TEAL_HOVER = "#066774"
PALE = "#EAF5F5"
LINE = "#E5ECF1"
FONT = "Microsoft YaHei UI"


class App(ctk.CTk, Controller):
    def __init__(self):
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        super().__init__(fg_color=BG)
        self.title("期刊全文下载器")
        self.assets = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        self.geometry("1080x790")
        self.minsize(960, 700)
        self.events = queue.Queue()
        self.worker = None
        self.control = None
        self.closing = False
        self.library = None
        self.mutable = []
        self.stages = ""
        self.advanced_window = None
        self.pdf_window = None
        self.settings = load_json(SETTINGS_DIR / "settings.json", {})
        if not isinstance(self.settings, dict):
            self.settings = {}
        self._variables()
        self.body_font = ctk.CTkFont(FONT, 14)
        self.small_font = ctk.CTkFont(FONT, 12)
        self.bold_font = ctk.CTkFont(FONT, 15, weight="bold")
        self._layout()
        self._select_from_url()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(120, self._poll)
        self.after(220, self._set_icon)
        self.after(30, self._fit_screen)

    def _set_icon(self):
        if (self.assets / "app.ico").exists():
            self.iconbitmap(str(self.assets / "app.ico"))

    def _fit_screen(self):
        scale = self._get_window_scaling()
        height = min(790, int(self.winfo_screenheight() / scale - 65))
        width = min(1080, int(self.winfo_screenwidth() / scale - 50))
        self.minsize(min(960, width), min(700, height))
        self.geometry(f"{width}x{height}")

    def label(self, parent, text="", *, font=None, color=INK, **kwargs):
        kwargs.setdefault("height", 20)
        return ctk.CTkLabel(parent, text=text, font=font or self.body_font, text_color=color,
                            anchor="w", **kwargs)

    def card(self, parent, **kwargs):
        return ctk.CTkFrame(parent, fg_color=WHITE, corner_radius=18, border_width=1,
                           border_color=LINE, **kwargs)

    def entry(self, parent, variable, **kwargs):
        return self.editable(ctk.CTkEntry(parent, textvariable=variable, height=42,
            corner_radius=10, border_width=1, border_color="#DCE5EC", fg_color="#FCFDFE",
            text_color=INK, font=self.body_font, **kwargs))

    def secondary(self, parent, text, command, **kwargs):
        return ctk.CTkButton(parent, text=text, command=command, height=40,
            corner_radius=10, fg_color="#ECF1F5", hover_color="#E0E8EE", text_color=INK,
            font=self.body_font, **kwargs)

    def _layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent", height=90)
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(22, 15))
        header.grid_columnconfigure(1, weight=1)
        if (self.assets / "app.png").exists():
            self.logo = ctk.CTkImage(Image.open(self.assets / "app.png"), size=(48, 48))
            ctk.CTkLabel(header, text="", image=self.logo, width=48).grid(row=0, column=0, rowspan=2, padx=(0, 14))
        self.label(header, "期刊全文下载器", font=ctk.CTkFont(FONT, 26, weight="bold")).grid(row=0, column=1, sticky="w")
        self.label(header, "把文献收集交给工具，把时间留给研究。", font=self.small_font, color=MUTED).grid(row=1, column=1, sticky="w", pady=(3, 0))
        ctk.CTkLabel(header, text="XML  +  PDF", text_color=TEAL, fg_color=PALE,
                    corner_radius=10, font=self.small_font, width=116, height=30).grid(row=0, column=2, rowspan=2)

        content = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0,
            scrollbar_fg_color=BG, scrollbar_button_color="#CEDAE3", scrollbar_button_hover_color="#9AAEBD")
        content._scrollbar.configure(width=8, corner_radius=4)
        content.grid(row=1, column=0, sticky="nsew", padx=(24, 19))
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0, minsize=290)

        form = self.card(content)
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 16), pady=(0, 14))
        form.grid_columnconfigure(0, weight=1)
        title = ctk.CTkFrame(form, fg_color="transparent")
        title.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 17))
        self.label(title, "下载设置", font=self.bold_font).pack(side="left")
        ctk.CTkButton(title, text="高级设置  ›", fg_color="transparent", hover_color=PALE,
            text_color=TEAL, font=self.small_font, width=80, height=28, command=self._advanced).pack(side="right")

        key_head = ctk.CTkFrame(form, fg_color="transparent")
        key_head.grid(row=1, column=0, sticky="ew", padx=24)
        self.label(key_head, "API Key", font=self.bold_font).pack(side="left")
        self.label(key_head, "仅本次运行保留", font=self.small_font, color=MUTED).pack(side="right")
        key_row = ctk.CTkFrame(form, fg_color="transparent")
        key_row.grid(row=2, column=0, sticky="ew", padx=24, pady=(7, 17))
        key_row.grid_columnconfigure(0, weight=1)
        self.key_entry = self.entry(key_row, self.key, show="●", placeholder_text="输入你自己的 Elsevier API Key")
        self.key_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.visibility = ctk.CTkCheckBox(key_row, text="显示", variable=self.show_key, command=self._toggle_key,
            width=58, checkbox_width=18, checkbox_height=18, corner_radius=5, border_width=1,
            border_color="#B5C5D1", fg_color=TEAL, hover_color=TEAL_HOVER, text_color=MUTED, font=self.small_font)
        self.visibility.grid(row=0, column=1)

        self.label(form, "选择期刊", font=self.bold_font).grid(row=3, column=0, sticky="w", padx=24)
        self.switch = self.editable(ctk.CTkSegmentedButton(form, values=["RSE", "ISPRS", "自定义期刊"],
            variable=self.preset, command=self._on_preset, height=38, corner_radius=10, border_width=4,
            fg_color="#F0F4F7", selected_color=TEAL, selected_hover_color=TEAL_HOVER,
            unselected_color="#F0F4F7", unselected_hover_color="#E0E9EF", text_color=INK,
            font=self.body_font, dynamic_resizing=False))
        self.switch.grid(row=4, column=0, sticky="ew", padx=24, pady=(8, 6))
        # Selected text needs a light foreground; the widget uses one color for all labels.
        self.switch.configure(selected_color="#D3EEEE", selected_hover_color="#BFE4E5")
        self.journal_label = self.label(form, "", font=self.small_font, color=TEAL, wraplength=540)
        self.journal_label.grid(row=5, column=0, sticky="w", padx=25, pady=(0, 10))

        self.url_entry = self.entry(form, self.url, placeholder_text="粘贴 ScienceDirect 期刊主页地址")
        self.url_entry.grid(row=6, column=0, sticky="ew", padx=24)
        self.url_entry.bind("<FocusOut>", lambda event: self._select_from_url())
        self.label(form, "支持 /journal/期刊名称 或含 pub= 的检索网址", font=self.small_font, color=MUTED).grid(row=7, column=0, sticky="w", padx=25, pady=(5, 17))

        line = ctk.CTkFrame(form, fg_color="transparent")
        line.grid(row=8, column=0, sticky="ew", padx=24)
        self.label(line, "发表年份", font=self.bold_font).pack(side="left")
        self.label(line, "包含起止年份", font=self.small_font, color=MUTED).pack(side="right")
        years = ctk.CTkFrame(form, fg_color="transparent")
        years.grid(row=9, column=0, sticky="ew", padx=24, pady=(7, 16))
        self.year_control(years, self.start_year).pack(side="left")
        self.label(years, "—", color=MUTED).pack(side="left", padx=16)
        self.year_control(years, self.end_year).pack(side="left")

        self.label(form, "保存位置", font=self.bold_font).grid(row=10, column=0, sticky="w", padx=24)
        folder = ctk.CTkFrame(form, fg_color="transparent")
        folder.grid(row=11, column=0, sticky="ew", padx=24, pady=(7, 22))
        folder.grid_columnconfigure(0, weight=1)
        self.entry(folder, self.output).grid(row=0, column=0, sticky="ew", padx=(9, 10))
        self.editable(self.secondary(folder, "选择", self._browse, width=62)).grid(row=0, column=1)

        right = ctk.CTkFrame(content, fg_color="transparent", width=290)
        right.grid(row=0, column=1, sticky="nsew", pady=(0, 14))
        right.grid_columnconfigure(0, weight=1)
        progress = self.card(right, width=290)
        progress.grid(row=0, column=0, sticky="ew")
        progress.grid_columnconfigure(0, weight=1)
        status_line = ctk.CTkFrame(progress, fg_color="transparent")
        status_line.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 13))
        self.label(status_line, "●", color=TEAL).pack(side="left", padx=(0, 7))
        self.label(status_line, "", textvariable=self.status, font=self.bold_font).pack(side="left")
        big = ctk.CTkFrame(progress, fg_color="transparent")
        big.grid(row=1, column=0, sticky="ew", padx=20)
        self.label(big, "", textvariable=self.metrics["saved"], font=ctk.CTkFont("Segoe UI", 42, weight="bold"), color=INK).pack(side="left")
        self.label(big, "", textvariable=self.saved_caption, font=self.small_font, color=MUTED).pack(side="left", padx=12, pady=(22, 0))
        self.progress = ctk.CTkProgressBar(progress, height=7, corner_radius=4, fg_color="#EAF0F4", progress_color=TEAL)
        self.progress.set(0)
        self.progress.grid(row=2, column=0, sticky="ew", padx=20, pady=(12, 7))
        self.label(progress, "", textvariable=self.percent, color=TEAL, font=self.small_font).grid(row=3, column=0, sticky="e", padx=20)
        stats = ctk.CTkFrame(progress, fg_color="transparent")
        stats.grid(row=4, column=0, sticky="ew", padx=20, pady=(4, 12))
        stats.grid_columnconfigure(1, weight=1)
        for i, (key, title) in enumerate((("total", "列表条目"), ("reused", "校验后跳过"), ("pending", "尚未保存"))):
            self.label(stats, title, font=self.small_font, color=MUTED).grid(row=i, column=0, sticky="w", pady=3)
            self.label(stats, "", textvariable=self.metrics[key], font=self.bold_font).grid(row=i, column=1, sticky="e", pady=3)
        ctk.CTkFrame(progress, height=1, fg_color=LINE).grid(row=5, column=0, sticky="ew", padx=20)
        self.label(progress, "", textvariable=self.detail, font=self.small_font, color=MUTED,
            wraplength=244, justify="left").grid(row=6, column=0, sticky="w", padx=20, pady=(12, 18))

        info = ctk.CTkFrame(right, fg_color=PALE, corner_radius=14)
        info.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.label(info, "连接校园网或学校 VPN", font=self.bold_font, color=TEAL).pack(anchor="w", padx=18, pady=(15, 5))
        self.label(info, "默认直连 Elsevier，自动校验与续传。\n临时断网会放缓重试，无需手动守候。", font=self.small_font,
            color="#5F858A", justify="left").pack(anchor="w", padx=18, pady=(0, 16))

        log = self.card(content)
        log.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        log.grid_columnconfigure(0, weight=1)
        self.label(log, "运行记录", font=self.bold_font).grid(row=0, column=0, sticky="w", padx=22, pady=(15, 5))
        self.logbox = ctk.CTkTextbox(log, height=105, fg_color=WHITE, text_color=MUTED,
            font=self.small_font, corner_radius=8, border_width=0, wrap="word",
            scrollbar_button_color="#D1DDE5", scrollbar_button_hover_color="#9BAEBE")
        self.logbox._y_scrollbar.configure(width=8, corner_radius=4)
        self.logbox.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))
        self.logbox.configure(state="disabled")
        self._log("准备就绪。先获取论文列表，再校验已保存文件，随后下载缺少的全文。")

        footer = ctk.CTkFrame(self, fg_color=WHITE, corner_radius=0, height=80)
        footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        footer.grid_columnconfigure(0, weight=1)
        self.label(footer, f"v{VERSION}   ·   XML + TXT + PDF\n高清图表 · 期刊式排版", font=self.small_font, color=MUTED,
            justify="left").grid(row=0, column=0, sticky="w", padx=30, pady=16)
        self.secondary(footer, "打开文件夹", self.open_folder, width=100).grid(row=0, column=1, padx=(0, 8))
        self.pdf_button = self.editable(self.secondary(footer, "合成 PDF", self._pdf_dialog, width=110))
        self.pdf_button.grid(row=0, column=2, padx=(0, 8))
        self.stop_button = self.secondary(footer, "停止", self.stop, width=70, state="disabled")
        self.stop_button.grid(row=0, column=3, padx=(0, 8))
        self.pause_button = self.secondary(footer, "暂停", self.pause, width=70, state="disabled")
        self.pause_button.grid(row=0, column=4, padx=(0, 10))
        self.start_button = ctk.CTkButton(footer, text="开始下载", command=self.start, height=44,
            width=130, corner_radius=12, fg_color=TEAL, hover_color=TEAL_HOVER, text_color=WHITE,
            font=self.bold_font)
        self.start_button.grid(row=0, column=5, padx=(0, 24), pady=16)

    def _pdf_dialog(self):
        if self.worker and self.worker.is_alive():
            return
        if self.pdf_window and self.pdf_window.winfo_exists():
            self.pdf_window.focus()
            return
        from tkinter import filedialog, StringVar
        win = ctk.CTkToplevel(self, fg_color=BG)
        self.pdf_window = win
        win.title("合成阅读版 PDF")
        win.geometry("610x480")
        win.resizable(False, False)
        win.transient(self)
        win.grid_columnconfigure(0, weight=1)
        self.label(win, "把 XML 变成可阅读的论文", font=ctk.CTkFont(FONT, 22, weight="bold")).grid(row=0, column=0, sticky="w", padx=28, pady=(26, 7))
        self.label(win, "补齐高清图片，保留正文、公式、表格与参考文献。", font=self.small_font, color=MUTED).grid(row=1, column=0, sticky="w", padx=28)
        card = self.card(win)
        card.grid(row=2, column=0, sticky="ew", padx=26, pady=20)
        card.grid_columnconfigure(0, weight=1)
        initial = self.library or Path(self.output.get())
        if initial.name == "fulltext_pdf":
            initial = initial.parent
        folder_var = StringVar(value=str(initial))
        layout_var = StringVar(value="期刊双栏")
        self.label(card, "已有 XML 文库", font=self.bold_font).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 8))
        folder_row = ctk.CTkFrame(card, fg_color="transparent")
        folder_row.grid(row=1, column=0, sticky="ew", padx=20)
        folder_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(folder_row, textvariable=folder_var, height=42, corner_radius=10,
                     border_width=1, border_color=LINE, font=self.body_font).grid(row=0, column=0, sticky="ew", padx=(0, 10))
        def browse():
            value = filedialog.askdirectory(parent=win, title="选择文库或 fulltext_xml 文件夹",
                                            initialdir=folder_var.get() if Path(folder_var.get()).exists() else str(Path.home()))
            if value:
                folder_var.set(value)
        self.secondary(folder_row, "选择", browse, width=65).grid(row=0, column=1)
        self.label(card, f"年份沿用主界面：{self.start_year.get()} — {self.end_year.get()}", font=self.small_font, color=MUTED).grid(row=2, column=0, sticky="w", padx=20, pady=(7, 17))
        ctk.CTkSegmentedButton(card, values=["期刊双栏", "舒适单栏"], variable=layout_var,
            height=40, corner_radius=10, border_width=4, fg_color="#F0F4F7",
            selected_color="#D3EEEE", selected_hover_color="#BFE4E5", unselected_color="#F0F4F7",
            unselected_hover_color="#E0E9EF", text_color=INK, font=self.body_font).grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 20))
        self.label(win, "使用主界面的 API Key 与网络设置；完整 PDF 会校验后跳过。\n输出到文库的 fulltext_pdf 文件夹。需要已安装 Edge 或 Chrome。\n生成的是 XML 重排阅读版，分页和细节可能与官方 PDF 不同。", font=self.small_font,
                   color=MUTED, justify="left", wraplength=550).grid(row=3, column=0, sticky="w", padx=28)
        ctk.CTkButton(win, text="开始合成", height=44, width=140, corner_radius=12,
            fg_color=TEAL, hover_color=TEAL_HOVER, font=self.bold_font,
            command=lambda: self.start_pdf(folder_var.get(), "double" if layout_var.get() == "期刊双栏" else "single")).grid(row=4, column=0, sticky="e", padx=28, pady=22)

    def year_control(self, parent, variable):
        frame = ctk.CTkFrame(parent, fg_color="#F7F9FB", corner_radius=10, border_width=1, border_color=LINE)
        for column, (label, step) in enumerate((("−", -1), ("+", 1))):
            button = ctk.CTkButton(frame, text=label, width=32, height=38, corner_radius=8,
                fg_color="transparent", hover_color="#E6EFF3", text_color=MUTED, font=ctk.CTkFont(FONT, 18),
                command=lambda v=variable, d=step: self._year_step(v, d))
            self.editable(button).grid(row=0, column=column * 2, padx=2, pady=2)
        self.editable(ctk.CTkEntry(frame, textvariable=variable, width=68, height=36, justify="center",
            fg_color="#F7F9FB", border_width=0, font=self.body_font, text_color=INK)).grid(row=0, column=1)
        return frame

    def _year_step(self, variable, delta):
        try:
            value = int(variable.get())
        except ValueError:
            value = 2024
        variable.set(str(max(1800, min(2100, value + delta))))

    def _select_from_url(self):
        try:
            slug, _ = parse_journal_url(self.url.get())
            journal = next((j for j in PRESETS if j.slug == slug), None)
        except ValueError:
            journal = None
        self.preset.set("RSE" if journal == PRESETS[0] else "ISPRS" if journal == PRESETS[1] else "自定义期刊")
        self.journal_label.configure(text=journal.name if journal else "粘贴期刊主页，程序会自动识别期刊。")

    def _on_preset(self, value):
        journal = PRESETS[0] if value == "RSE" else PRESETS[1] if value == "ISPRS" else None
        self.issn.set("")
        self.url.set(journal.url if journal else "")
        self._select_from_url()
        if not journal:
            self.url_entry.focus_set()

    def _advanced(self):
        if self.advanced_window and self.advanced_window.winfo_exists():
            self.advanced_window.focus()
            return
        win = ctk.CTkToplevel(self, fg_color=BG)
        self.advanced_window = win
        win.title("高级设置")
        win.geometry("540x380")
        win.resizable(False, False)
        win.transient(self)
        self.label(win, "高级设置", font=ctk.CTkFont(FONT, 22, weight="bold")).pack(anchor="w", padx=28, pady=(24, 12))
        for text, variable in (("直连 Elsevier（推荐，忽略系统 HTTP 代理）", self.direct),
                ("更新论文列表，获取新发表的记录", self.refresh_index),
                ("重新尝试之前无全文 / 失败 / 待检查的条目", self.retry_failed)):
            check = ctk.CTkCheckBox(win, text=text, variable=variable, font=self.body_font, text_color=INK,
                checkbox_width=20, checkbox_height=20, corner_radius=6, border_width=1, border_color="#A7BBC7",
                fg_color=TEAL, hover_color=TEAL_HOVER)
            check.pack(anchor="w", padx=28, pady=10)
            if self.worker and self.worker.is_alive():
                check.configure(state="disabled")
        self.label(win, "ISSN（无法自动识别期刊时填写）", font=self.small_font, color=MUTED).pack(anchor="w", padx=28, pady=(14, 6))
        entry = ctk.CTkEntry(win, textvariable=self.issn, height=40, width=200, corner_radius=10,
                fg_color=WHITE, border_width=1, border_color=LINE, font=self.body_font)
        entry.pack(anchor="w", padx=28)
        if self.worker and self.worker.is_alive():
            entry.configure(state="disabled")
        self.secondary(win, "完成", win.destroy, width=100).pack(anchor="e", padx=28, pady=20)
