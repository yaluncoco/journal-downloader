"""Exercise real Tk geometry at several physical resolutions and DPI factors."""
import gc
import time
import unittest
from unittest.mock import patch
import customtkinter as ctk

from ui import App
from window_layout import fit_window


def settle(window, seconds=0.25):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        window.update()
        time.sleep(0.01)


class LayoutTests(unittest.TestCase):
    def setUp(self):
        with patch("ui.load_json", return_value={}):
            self.app = App(check_updates=False, remember_credentials=False, scan_libraries=False,
                           settings={}, visible=False)
        self.app.attributes("-alpha", 0)
        self.errors = []
        self.app.report_callback_exception = lambda *args: self.errors.append(args)
        settle(self.app)
        self.base_dpi = ctk.ScalingTracker.window_dpi_scaling_dict[self.app]

    def tearDown(self):
        for callback in self.app.tk.call("after", "info"):
            self.app.tk.call("after", "cancel", callback)
        self.app.destroy()
        ctk.set_widget_scaling(1)
        ctk.set_window_scaling(1)
        gc.collect()
        self.assertEqual(self.errors, [])

    def resize(self, physical_width, physical_height, scale):
        ctk.set_widget_scaling(scale / self.base_dpi)
        ctk.set_window_scaling(scale / self.base_dpi)
        self.app.minsize(300, 250)
        self.app.geometry(f"{int(physical_width / scale)}x{int(physical_height / scale)}")
        settle(self.app, 0.4)

    def assert_inside(self, child, parent, vertical=True):
        x = child.winfo_rootx() - parent.winfo_rootx()
        y = child.winfo_rooty() - parent.winfo_rooty()
        self.assertGreaterEqual(x, -2, str(child))
        self.assertLessEqual(x + child.winfo_width(), parent.winfo_width() + 2, str(child))
        if vertical:
            self.assertGreaterEqual(y, -2, str(child))
            self.assertLessEqual(y + child.winfo_height(), parent.winfo_height() + 2, str(child))

    def test_main_controls_remain_accessible_at_small_sizes_and_dpi_changes(self):
        for width, height, scale in ((1080, 790, 1), (1280, 720, 1.25), (1366, 768, 1.5),
                                     (1280, 720, 2), (800, 600, 1.5), (800, 600, 2), (1920, 1080, 2), (1080, 790, 1)):
            with self.subTest(width=width, height=height, scale=scale):
                self.resize(width, height, scale)
                stacked = self.app.winfo_width() / self.app.content._get_widget_scaling() < 920
                self.assertEqual(self.app.sidebar.grid_info()["column"], 0 if stacked else 1)
                self.assert_inside(self.app.footer, self.app)
                for button in self.app.action_buttons:
                    self.assert_inside(button, self.app)
                self.assert_inside(self.app.form, self.app.content, vertical=False)
                self.assert_inside(self.app.sidebar, self.app.content, vertical=False)
                self.assertTrue(self.app.content.winfo_ismapped())
                self.assertAlmostEqual(self.app.content.winfo_width(), self.app.content._parent_canvas.winfo_width(), delta=2)
                self.assert_inside(self.app.key_entry, self.app.form, vertical=False)
                if (width, height, scale) == (1080, 790, 1):
                    self.assert_inside(self.app.search_actions, self.app.content._parent_canvas)
                self.assertGreater(self.app.journal_label.cget("wraplength"), 0)
                years = self.app.form.grid_slaves(row=9)[0]
                for child in years.winfo_children():
                    if isinstance(child, ctk.CTkFrame):
                        self.assert_inside(child, self.app.form, vertical=False)
                self.assertGreater(self.app.content._parent_canvas.winfo_height(), 80)
                self.app.content._parent_canvas.yview_moveto(1)
                settle(self.app, 0.03)
                self.assertGreater(self.app.content._parent_canvas.yview()[1], 0.99)

    def test_dialogs_fit_work_area_and_catalog_results_wrap(self):
        self.resize(1000, 680, 1.5)
        for opener, name in ((self.app._pdf_dialog, "pdf_window"), (self.app._advanced, "advanced_window"),
                             (self.app._catalog_dialog, "catalog_window"), (self.app._search_dialog, "search_window")):
            with self.subTest(dialog=name), patch("window_layout.work_area", return_value=(0, 0, 1000, 680)):
                opener()
                window = getattr(self.app, name)
                window.attributes("-alpha", 0)
                settle(window, 0.5)
                self.assertLessEqual(window.winfo_width(), 980)
                self.assertLessEqual(window.winfo_height(), 680 - 50)
                if name == "catalog_window":
                    self.app.catalog_query.set("computer and")
                    settle(window, 0.4)
                    self.assertTrue(self.app.catalog_matches)
                    def check_frames(parent):
                        for child in parent.winfo_children():
                            if isinstance(child, ctk.CTkButton) and child.cget("text") == "选择":
                                self.assert_inside(child, window, vertical=False)
                            check_frames(child)
                    check_frames(window)
                window.destroy()

    def test_long_journal_name_wraps_to_allocated_width(self):
        self.resize(800, 600, 1.5)
        self.app.journal_label.configure(text="Computers and Electronics in Agriculture: " * 4)
        settle(self.app)
        self.assert_inside(self.app.journal_label, self.app.form, vertical=False)
        self.assertLessEqual(self.app.journal_label._label.winfo_reqwidth(),
                             self.app.form.winfo_width() - 40)

    def test_tiny_catalog_preserves_result_area_and_filter_access(self):
        self.resize(800, 600, 2)
        with patch("window_layout.work_area", return_value=(0, 0, 800, 600)):
            self.app._catalog_dialog()
        win = self.app.catalog_window
        win.attributes("-alpha", 0)
        settle(win, 0.5)
        self.assertGreater(self.app.catalog_results._parent_canvas.winfo_height(), 100)
        def buttons(parent):
            for child in parent.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    yield child
                yield from buttons(child)
        toggle = next(button for button in buttons(win) if button.cget("text") == "筛选")
        self.assert_inside(toggle, win)
        toggle.invoke()
        settle(win)
        self.assertEqual(toggle.cget("text"), "收起")
        toggle.invoke()
        settle(win)
        self.assertGreater(self.app.catalog_results._parent_canvas.winfo_height(), 100)
        win.destroy()


if __name__ == "__main__":
    unittest.main()
