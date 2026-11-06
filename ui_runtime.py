"""Windowed runtime and font-independent Canvas drawing for the light UI."""
import os
import sys


def prepare_ui():
    # PyInstaller --windowed has no standard streams. CTk's font-failure
    # fallback writes to stderr during import, before a Tk window even exists.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    import customtkinter as ctk
    # Native Canvas polygons also draw arrows/checkmarks; no private glyph font
    # is required on machines that restrict non-system fonts or font loading.
    ctk.DrawEngine.preferred_drawing_method = "polygon_shapes"
    if sys.platform == "win32":
        # This application always uses a light theme. Keep the OS title bar:
        # CTk's optional recoloring withdraws/remaps windows and calls update().
        ctk.CTk._deactivate_windows_window_header_manipulation = True
        ctk.CTkToplevel._deactivate_windows_window_header_manipulation = True
    return ctk


def defer_canvas_paints(parent):
    """Avoid nested global idle flushing in CTk 6.0 scrollbar/menu drawing.

    These two widgets flush ALL pending Tk geometry/paint work from inside
    _draw(), causing reentrant resize cascades. Canvas items already repaint
    in the normal event loop. Limit the adapter to our own widget instances;
    application/root update_idletasks and other library behavior stay intact.
    Recheck this adapter when upgrading the pinned CustomTkinter dependency.
    """
    import customtkinter as ctk
    if isinstance(parent, (ctk.CTkScrollbar, ctk.CTkOptionMenu)):
        parent._canvas.update_idletasks = lambda: None
    for child in parent.winfo_children():
        defer_canvas_paints(child)
