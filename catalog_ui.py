"""Paged local journal search; no network call while typing."""
import tkinter as tk
import customtkinter as ctk

from journal_catalog import catalog, search, status_label
from journal_metadata import CATEGORY_OPTIONS, ZONE_OPTIONS, reference_label, subject_label


class CatalogUI:
    def _catalog_dialog(self):
        if self.worker and self.worker.is_alive():
            return
        if getattr(self, "catalog_window", None) and self.catalog_window.winfo_exists():
            self.catalog_window.focus()
            return
        from ui import BG, INK, MUTED, TEAL, PALE, FONT
        win = self.catalog_window = ctk.CTkToplevel(self, fg_color=BG)
        win.title("检索 Elsevier 期刊")
        win.geometry("900x700")
        win.minsize(790, 560)
        win.transient(self)
        if (self.assets / "app.ico").exists():
            win.after(200, lambda: win.iconbitmap(str(self.assets / "app.ico")))
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(5, weight=1)
        self.label(win, "查找期刊", font=ctk.CTkFont(FONT, 22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=24, pady=(22, 5))
        data = catalog()
        self.label(win, f"官方清单 · {data['count']:,} 个刊名 · {data['retrieved_at'][:10]} 更新 · 离线搜索",
                   font=self.small_font, color=MUTED).grid(row=1, column=0, sticky="w", padx=24)
        query = self.catalog_query = tk.StringVar()
        scope = self.catalog_scope = tk.StringVar(value="全部")
        category = self.catalog_category = tk.StringVar(value="全部学科")
        zone = self.catalog_zone = tk.StringVar(value="全部分区")
        top = self.catalog_top = tk.BooleanVar(value=False)
        self.catalog_page = 0
        self.catalog_matches = []
        search_row = ctk.CTkFrame(win, fg_color="transparent")
        search_row.grid(row=2, column=0, sticky="ew", padx=24, pady=(16, 10))
        search_row.grid_columnconfigure(1, weight=1)
        self.label(search_row, "期刊名 / ISSN", font=self.small_font, color=MUTED).grid(row=0, column=0, padx=(0, 12))
        entry = ctk.CTkEntry(search_row, textvariable=query, placeholder_text="输入英文期刊名、关键词或 ISSN",
                            height=42, corner_radius=10, font=self.body_font)
        entry.grid(row=0, column=1, sticky="ew")
        options = ctk.CTkFrame(win, fg_color="transparent")
        options.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 10))
        ctk.CTkSegmentedButton(options, values=["全部", "在刊", "历史刊名"], variable=scope,
            command=lambda _: refresh(), selected_color="#D3EEEE", selected_hover_color="#BFE4E5",
            fg_color="#F0F4F7", unselected_color="#F0F4F7", unselected_hover_color="#E0E9EF",
            text_color=INK, font=self.small_font, height=32).pack(side="left")
        result_text = tk.StringVar()
        self.label(options, textvariable=result_text, font=self.small_font, color=MUTED).pack(side="right")
        filters = ctk.CTkFrame(win, fg_color="transparent")
        filters.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.label(filters, "学科", font=self.small_font, color=MUTED).pack(side="left", padx=(0, 8))
        ctk.CTkOptionMenu(filters, values=CATEGORY_OPTIONS, variable=category, width=208, height=32,
            fg_color="#EAF0F4", button_color="#DCE6ED", text_color=INK, button_hover_color="#D3EEEE",
            font=self.small_font, dropdown_font=self.small_font, command=lambda _: refresh()).pack(side="left")
        self.label(filters, "2025 中科院参考", font=self.small_font, color=MUTED).pack(side="left", padx=(16, 8))
        ctk.CTkOptionMenu(filters, values=ZONE_OPTIONS, variable=zone, width=108, height=32,
            fg_color="#EAF0F4", button_color="#DCE6ED", text_color=INK, button_hover_color="#D3EEEE",
            font=self.small_font, dropdown_font=self.small_font, command=lambda _: refresh()).pack(side="left")
        ctk.CTkCheckBox(filters, text="Top", variable=top, command=lambda: refresh(), width=65,
            checkbox_width=17, checkbox_height=17, corner_radius=5, border_width=1,
            fg_color=TEAL, font=self.small_font, text_color=INK).pack(side="left", padx=16)
        results = ctk.CTkScrollableFrame(win, fg_color="#FFFFFF", corner_radius=12,
            scrollbar_button_color="#CCD9E2", scrollbar_button_hover_color="#A1B5C3")
        results._scrollbar.configure(width=7, corner_radius=4)
        results.grid(row=5, column=0, sticky="nsew", padx=24)
        results.grid_columnconfigure(0, weight=1)
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ew", padx=24, pady=16)
        self.label(footer, "交叉类别依据官方标签；分区为公开参考。",
            font=self.small_font, color=MUTED).pack(side="left")
        ctk.CTkButton(footer, text="来源与说明", width=88, height=26, fg_color="transparent",
            text_color=TEAL, hover_color=PALE, font=self.small_font, command=self._catalog_sources).pack(side="left", padx=4)
        page_text = tk.StringVar()
        def move(step):
            self.catalog_page += step
            render()
        previous = self.secondary(footer, "‹", lambda: move(-1), width=30)
        following = self.secondary(footer, "›", lambda: move(1), width=30)
        following.pack(side="right")
        self.label(footer, textvariable=page_text, font=self.small_font, color=MUTED).pack(side="right", padx=8)
        previous.pack(side="right")
        timer = [None]
        def choose(item):
            if self.worker and self.worker.is_alive():
                return
            self.issn.set("")
            self.url.set(item["url"])
            self._select_from_url()
            win.destroy()
        def render():
            for child in results.winfo_children():
                child.destroy()
            count = len(self.catalog_matches)
            pages = max(1, (count + 29) // 30)
            self.catalog_page = min(max(0, self.catalog_page), pages - 1)
            result_text.set(f"找到 {count:,} 个期刊")
            page_text.set(f"{self.catalog_page + 1} / {pages}")
            previous.configure(state="normal" if self.catalog_page else "disabled")
            following.configure(state="normal" if self.catalog_page + 1 < pages else "disabled")
            for row, item in enumerate(self.catalog_matches[self.catalog_page * 30:(self.catalog_page + 1) * 30]):
                block = ctk.CTkFrame(results, fg_color="#F5F8FA", corner_radius=10)
                block.grid(row=row, column=0, sticky="ew", padx=4, pady=4)
                block.grid_columnconfigure(0, weight=1)
                title = self.label(block, item["name"], font=self.body_font, wraplength=550, justify="left")
                title.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 3))
                detail = self.label(block, f"{item['issn'] or 'ISSN 待公布'}  ·  {status_label(item['status'])}  ·  {subject_label(item)}",
                    font=self.small_font, color=MUTED, wraplength=550, justify="left")
                detail.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 3))
                self.label(block, reference_label(item), font=self.small_font, color=TEAL,
                    wraplength=550, justify="left").grid(row=2, column=0, sticky="w", padx=12, pady=(0, 10))
                ctk.CTkButton(block, text="选择", command=lambda value=item: choose(value), width=56,
                    height=30, fg_color=PALE, hover_color="#D3EEEE", text_color=TEAL,
                    font=self.small_font).grid(row=0, column=1, rowspan=3, padx=12)
            if not count:
                self.label(results, "未找到匹配期刊，试试缩短关键词或输入 ISSN。", color=MUTED).grid(padx=15, pady=30)
            results._parent_canvas.yview_moveto(0)
        def refresh(*_):
            if timer[0]:
                win.after_cancel(timer[0])
            def apply():
                timer[0] = None
                self.catalog_page = 0
                self.catalog_matches = search(query.get(), scope.get(), category.get(), zone.get(), top.get())
                render()
            timer[0] = win.after(180, apply)
        query.trace_add("write", refresh)
        def destroy(event):
            if event.widget == win and timer[0]:
                win.after_cancel(timer[0])
                timer[0] = None
        win.bind("<Destroy>", destroy)
        entry.bind("<Escape>", lambda _: win.destroy())
        entry.focus_set()
        refresh()

    def _catalog_sources(self):
        from tkinter import messagebox
        messagebox.showinfo("期刊数据来源", "学科：Elsevier 官方 ScienceDirect 三级分类表，2026-11-06 获取。\n"
            "农学 × 计算机：同时具有农学与生物科学、计算机标签。\n林学 × 计算机：同时具有 Forestry、Computer Science 标签。分类不保证投稿范围。\n\n"
            "2025 中科院分区：hitfyd/ShowJCR 公开参考表，按 ISSN 精确匹配，固定版本 4687a8f。"
            "未经官方逐刊核验；大类分区、Top、收录索引与排名均来自该参考表，不等同于 JCR 分区。"
            "缺失条目显示未收录，不代表未被 SCI 收录。\n\n"
            "来源链接及数据版本见安装包 DATA_SOURCES.md；正式评价请核对机构订阅的分区表。",
            parent=self.catalog_window)
