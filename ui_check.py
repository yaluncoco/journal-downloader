"""Offline UI compatibility probe for both source and installed, windowed EXEs."""
from pathlib import Path
import sys
import time


def run(path):
    from engine import VERSION, save_json
    started = time.perf_counter()
    report = {"version": VERSION, "frozen": bool(getattr(sys, "frozen", False)), "ok": False}
    app = None
    errors = []
    try:
        from ui import App, ctk
        app = App(check_updates=False, remember_credentials=False, scan_libraries=False,
                  settings={}, visible=False)
        app.report_callback_exception = lambda kind, value, tb: errors.append(f"{kind.__name__}: {value}")
        report.update(startup_ms=round((time.perf_counter() - started) * 1000, 1),
                      tk=str(app.tk.call("info", "patchlevel")), python=sys.version.split()[0],
                      drawing=ctk.DrawEngine.preferred_drawing_method)

        def measure(action, duration):
            delays = []
            last = time.perf_counter()
            timer = None
            def heartbeat():
                nonlocal last, timer
                now = time.perf_counter()
                delays.append((now - last) * 1000)
                last = now
                timer = app.after(10, heartbeat)
            timer = app.after(10, heartbeat)
            action()
            end = time.perf_counter() + duration
            while time.perf_counter() < end:
                app.update()
                time.sleep(.002)
            if timer:
                app.after_cancel(timer)
            return {"max_gap_ms": round(max(delays, default=0), 1),
                    "p95_gap_ms": round(sorted(delays)[int(len(delays) * .95)] if delays else 0, 1)}

        report["first_layout"] = measure(lambda: None, .35)
        def open_catalog():
            app._catalog_dialog()
            app.catalog_window.attributes("-alpha", 0)
        report["catalog_open"] = measure(open_catalog, 1.5)
        report["search"] = measure(lambda: app.catalog_query.set("computer and"), .8)
        report["search_first"] = app.catalog_matches[0]["name"] if app.catalog_matches else ""
        report["catalog_clear"] = measure(lambda: app.catalog_query.set(""), .8)
        app.catalog_window.destroy()
        for i in range(2000):
            app.events.put({"kind": "log", "text": f"Offline UI check {i}"})
        report["log_burst"] = measure(lambda: None, 1)
        report["queued_events"] = app.events.qsize()
        report["resize"] = {}
        for scale in (1, 1.5, 2):
            dpi = ctk.ScalingTracker.window_dpi_scaling_dict[app]
            ctk.set_widget_scaling(scale / dpi)
            ctk.set_window_scaling(scale / dpi)
            measure(lambda: None, .2)
            step = [0]
            def resize():
                width = 800 + (step[0] if step[0] < 12 else 24 - step[0]) * 20
                app.geometry(f"{int(width / scale)}x{int(600 / scale)}")
                step[0] += 1
                if step[0] < 24:
                    app.after(16, resize)
            report["resize"][str(scale)] = measure(resize, 3)
            report["resize"][str(scale)]["steps"] = step[0]
        def glyphs(widget):
            count = 0
            if isinstance(widget, ctk.CTkCanvas):
                count = len(widget.find_withtag("ctk_aa_circle_font_element"))
            return count + sum(glyphs(child) for child in widget.winfo_children())
        report["private_shape_items"] = glyphs(app)
        report["ok"] = (not errors and report["private_shape_items"] == 0
                        and report["search_first"].startswith("Computers and")
                        and report["queued_events"] == 0
                        and all(item["steps"] == 24 for item in report["resize"].values()))
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        report["callback_errors"] = errors
        if app:
            for handle in app.tk.call("after", "info"):
                app.tk.call("after", "cancel", handle)
            app.destroy()
        save_json(Path(path), report)
