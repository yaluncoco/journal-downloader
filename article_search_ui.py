"""Compact article-search controls and a paged, metadata-only preview."""
import math
import webbrowser
from tkinter import messagebox

from article_filter import FILTER_MODES
from article_search import SCOPES, SORTS, validate_search, website_url, scope_description
from engine import local_journal


class ArticleSearchUI:
    def _init_search(self):
        for variable in (self.search_scope, self.filter_mode, self.search_sort):
            variable.trace_add("write", self._search_changed)
        self._search_changed()

    def _search_changed(self, *_):
        scope = SCOPES[self.search_scope.get()]
        self.search_hint.set(scope_description(scope) + "\n" + self.filter_mode.get() +
                             " · " + self.search_sort.get())

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
        fit_window(win, (560, 480))
        win.grid_columnconfigure(0, weight=1)
        body = scroll_body(win)
        self.label(body, "关键词与排序", font=self.bold_font).grid(row=0, column=0, sticky="w", pady=(10, 14))
        def menu(row, label, variable, choices):
            self.label(body, label, font=self.small_font).grid(row=row, column=0, sticky="w", pady=(6, 4))
            widget = ctk.CTkOptionMenu(body, variable=variable, values=list(choices), height=36,
                font=self.body_font, dropdown_font=self.body_font, fg_color="#ECF1F5",
                button_color="#DDE8EE", text_color=INK)
            widget.grid(row=row+1, column=0, sticky="ew", pady=(0, 8))
            return widget
        menu(1, "关键词输入方式", self.filter_mode, FILTER_MODES)
        menu(3, "关键词检索的结果顺序", self.search_sort, SORTS)
        text = ('官网语法使用大写 AND / OR / NOT。\n'
                '例：(forest OR crop) AND "deep learning" NOT urban\n'
                '任一 / 全部词组用逗号分隔，例如 forest, deep learning。\n\n'
                '留空时获取整个期刊目录，最新发表优先。\n'
                '预览或开始时自动更新；刚预览过的完整列表可直接用于下载。')
        wrap_to(self.label(body, text, font=self.small_font, color=MUTED, justify="left"), padding=14).grid(row=5, column=0, sticky="ew", pady=(10, 12))
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
            url = website_url(journal, start, end, self.title_filter.get(), mode, scope, sort)
            webbrowser.open(url)
        except ValueError as error:
            messagebox.showerror("请检查检索设置", str(error), parent=self)

    def _show_search_results(self, result):
        self.search_snapshot = result.get('snapshot')
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
        source = "ScienceDirect 检索" if result['query'].strip() else "Elsevier 官方期刊目录"
        hint = f"{source} · 已自动更新\n命中 {result['candidate_total']} 条，可处理 {len(rows)} 条 · 关键词：{result['query'] or '无'}"
        if result['scope'] != 'fulltext' and result['query'].strip():
            hint += "\n当前只查题名。TLS 等术语可能出现在正文，可切换全文重新预览。"
        else:
            hint += "\n仅预览列表，尚未下载全文；缩写不会自动扩展为全称。"
        if result.get('search_notice'):
            hint += '\n' + result['search_notice']
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
        if result['scope'] != 'fulltext' and result['query'].strip():
            def fulltext():
                self.search_scope.set(next(name for name, code in SCOPES.items() if code == 'fulltext'))
                self.start(preview=True)
            self.secondary(win, "改用全文重新预览", fulltext, width=175).grid(row=4, column=0, sticky="e", padx=20, pady=(0, 14))
        self.secondary(win, "开始下载", self.start, width=110).grid(row=5, column=0, sticky="e", padx=20, pady=(0, 14))
        self.metrics["total"].set(str(len(rows)))
