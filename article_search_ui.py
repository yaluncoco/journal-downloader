"""Compact article-search controls and a paged, metadata-only preview."""
import math
import webbrowser
from tkinter import messagebox

from article_filter import FILTER_MODES
from article_search import SCOPES, SORTS, validate_search, website_url
from engine import local_journal


class ArticleSearchUI:
    def _init_search(self):
        for variable in (self.search_scope, self.filter_mode, self.search_sort):
            variable.trace_add("write", self._search_changed)
        self._search_changed()

    def _search_changed(self, *_):
        local = SCOPES[self.search_scope.get()] == "local"
        if local and self.filter_mode.get() == "官网语法":
            self.filter_mode.set("任一词组")
        self.search_hint.set(("只筛选 Crossref 题名 · " + self.filter_mode.get()) if local else
                             self.filter_mode.get() + " · " + self.search_sort.get())
        if getattr(self, "search_window", None) and self.search_window.winfo_exists():
            self.search_mode_menu.configure(values=[name for name in FILTER_MODES if not local or name != "官网语法"])
            self.search_sort_menu.configure(state="disabled" if local else "normal")

    def _search_dialog(self):
        from ui import ctk, BG, MUTED, INK
        from window_layout import fit_window, scroll_body, wrap_to
        if self.worker and self.worker.is_alive():
            return
        if getattr(self, "search_window", None) and self.search_window.winfo_exists():
            self.search_window.focus()
            return
        win = self.search_window = ctk.CTkToplevel(self, fg_color=BG)
        win.title("论文检索设置")
        win.transient(self)
        fit_window(win, (620, 620))
        win.grid_columnconfigure(0, weight=1)
        body = scroll_body(win)
        self.label(body, "检索范围与语法", font=self.bold_font).grid(row=0, column=0, sticky="w", pady=(10, 14))
        def menu(row, label, variable, choices):
            self.label(body, label, font=self.small_font).grid(row=row, column=0, sticky="w", pady=(6, 4))
            widget = ctk.CTkOptionMenu(body, variable=variable, values=list(choices), height=36,
                font=self.body_font, dropdown_font=self.body_font, fg_color="#ECF1F5",
                button_color="#DDE8EE", text_color=INK)
            widget.grid(row=row+1, column=0, sticky="ew", pady=(0, 8))
            return widget
        menu(1, "检索范围", self.search_scope, SCOPES)
        self.search_mode_menu = menu(3, "输入方式", self.filter_mode, FILTER_MODES)
        self.search_sort_menu = menu(5, "API 结果顺序（达到下载数量后按此顺序续传）", self.search_sort, SORTS)
        text = ('官网语法：直接交给 ScienceDirect 解释。\n'
                '例：(forest OR crop) AND "deep learning" NOT urban\n'
                'AND / OR / NOT 必须大写；双引号表示词组，括号控制组合。\n\n'
                '任一 / 全部词组：输入 forest, deep learning，软件自动生成 OR / AND 检索式。\n\n'
                '全文检索覆盖文章内容（不含参考文献）；题名检索只查标题。搜索权限与全文下载权限可能不同。\n'
                '本地题名筛选不请求搜索 API，不检索摘要或正文。\n\n'
                '留空时获取所选期刊全部候选列表。输入时不发送请求；点击预览或开始下载才检索。'
                '相同条件复用缓存，勾选高级设置中的「更新论文列表」可刷新。')
        wrap_to(self.label(body, text, font=self.small_font, color=MUTED, justify="left"), padding=14).grid(row=7, column=0, sticky="ew", pady=(10, 12))
        self.secondary(win, "完成", win.destroy, width=100).grid(row=1, column=0, sticky="e", padx=24, pady=14)
        self._search_changed()

    def _open_search_website(self):
        try:
            journal = local_journal(self.url.get())
            if not journal:
                raise ValueError("请先从期刊目录选择期刊。")
            start, end = int(self.start_year.get()), int(self.end_year.get())
            if not 1800 <= start <= end <= 2100:
                raise ValueError("请填写有效的年份范围。")
            scope = SCOPES[self.search_scope.get()]
            mode, sort = FILTER_MODES[self.filter_mode.get()], SORTS[self.search_sort.get()]
            validate_search(self.title_filter.get(), mode, scope, sort)
            url = website_url(journal, start, end, self.title_filter.get(), mode,
                              "title" if scope == "local" else scope, sort)
            webbrowser.open(url)
        except ValueError as error:
            messagebox.showerror("请检查检索设置", str(error), parent=self)

    def _show_search_results(self, result):
        from ui import ctk, BG, MUTED, WHITE, INK
        from window_layout import fit_window, wrap_to
        if getattr(self, "search_results_window", None) and self.search_results_window.winfo_exists():
            self.search_results_window.destroy()
        win = self.search_results_window = ctk.CTkToplevel(self, fg_color=BG)
        win.title("论文检索预览")
        win.transient(self)
        fit_window(win, (800, 620))
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(2, weight=1)
        rows = result["records"]
        title = f"{result['journal']} · {result['years'][0]}–{result['years'][1]}"
        wrap_to(self.label(win, title, font=self.bold_font, justify="left"), padding=40).grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 7))
        source = "本地候选题名" if result["scope"] == "local" or not result["query"].strip() else "ScienceDirect API"
        hint = f"{source} · 命中 {result['candidate_total']} 条，可处理 {len(rows)} 条\n仅预览论文列表，尚未下载全文。回到主窗口可开始下载。"
        wrap_to(self.label(win, hint, font=self.small_font, color=MUTED, justify="left"), padding=40).grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        box = ctk.CTkTextbox(win, font=self.body_font, wrap="word", fg_color=WHITE, text_color=INK,
                             scrollbar_button_color="#D1DDE5", scrollbar_button_hover_color="#9BAEBE")
        box.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 10))
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 16))
        bar.grid_columnconfigure(1, weight=1)
        page, pages = [0], max(1, math.ceil(len(rows) / 25))
        label = self.label(bar, "", font=self.small_font, color=MUTED)
        label.grid(row=0, column=1)
        def show(delta=0):
            page[0] = min(pages - 1, max(0, page[0] + delta))
            start = page[0] * 25
            text = "\n\n".join(f"{i + 1}. {row['title']}\n{row['date']}  ·  {row['doi']}"
                                for i, row in enumerate(rows[start:start+25], start))
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text or "没有符合当前条件的可处理论文，请调整关键词或年份。")
            box.configure(state="disabled")
            label.configure(text=f"第 {page[0]+1}/{pages} 页")
            previous.configure(state="normal" if page[0] else "disabled")
            following.configure(state="normal" if page[0]+1 < pages else "disabled")
        previous = self.secondary(bar, "上一页", lambda: show(-1), width=85)
        previous.grid(row=0, column=0)
        following = self.secondary(bar, "下一页", lambda: show(1), width=85)
        following.grid(row=0, column=2)
        show()
        self.metrics["total"].set(str(len(rows)))
