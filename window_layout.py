"""Logical-pixel layout helpers for small screens and Windows DPI scaling."""
import ctypes
import sys
import tkinter as tk


def work_area(window):
    """Return the nearest monitor's usable pixel rectangle, excluding the taskbar."""
    if sys.platform == "win32":
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = user32.MonitorFromWindow(window.winfo_id(), 2)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.work
            return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()


def fit_window(window, preferred, minimum=(420, 340)):
    """CTk geometry uses logical sizes but physical screen positions."""
    left, top, right, bottom = work_area(window.master or window)
    scale = window._get_window_scaling()
    # Leave space for the native caption, borders, and taskbar on every DPI.
    available = (max(240, int((right - left - 24) / scale)),
                 max(220, int((bottom - top) / scale) - 52))
    width, height = (min(p, a) for p, a in zip(preferred, available))
    window.minsize(min(minimum[0], width), min(minimum[1], height))
    x = left + max(8, int((right - left - width * scale) / 2))
    y = top + max(8, int((bottom - top - (height + 36) * scale) / 2))
    window.geometry(f"{width}x{height}+{x}+{y}")


def wrap_to(label, parent=None, padding=48, maximum=None):
    """Wrap from allocated width, not from the previous text's requested width."""
    parent = parent or label.master
    previous = [None]

    def resize(event=None):
        if event is not None and event.widget != parent:
            return
        width = max(80, int(parent.winfo_width() / label._get_widget_scaling()) - padding)
        if maximum:
            width = min(width, maximum)
        if width != previous[0]:
            previous[0] = width
            label.configure(wraplength=width)

    # CTkFrame.bind forwards to its canvas; measure the actual frame allocation.
    tk.Misc.bind(parent, "<Configure>", resize, add="+")
    return label


def scroll_body(window, *, row=0, padx=20, pady=12):
    import customtkinter as ctk
    body = ctk.CTkScrollableFrame(window, fg_color="transparent", corner_radius=0,
        scrollbar_button_color="#CEDAE3", scrollbar_button_hover_color="#9AAEBD")
    body._scrollbar.configure(width=7, corner_radius=4)
    body.grid(row=row, column=0, sticky="nsew", padx=padx, pady=pady)
    body.grid_columnconfigure(0, weight=1)
    window.grid_columnconfigure(0, weight=1)
    window.grid_rowconfigure(row, weight=1)
    return body
