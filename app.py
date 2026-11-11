"""Windows entry point. No console; optional credentials use Windows DPAPI."""
from pathlib import Path
import sys


def main():
    browser = ""
    if "--pdf-browser" in sys.argv:
        browser = sys.argv[sys.argv.index("--pdf-browser") + 1]
    if "--ui-check" in sys.argv:
        from ui_check import run
        run(Path(sys.argv[sys.argv.index("--ui-check") + 1]))
        return
    if "--pdf-check" in sys.argv:
        from pdf_browser import diagnose
        from engine import ActionRequired
        index = sys.argv.index("--pdf-check")
        try:
            diagnose(Path(sys.argv[index + 1]), browser=browser)
        except ActionRequired:
            pass  # The machine-readable report already contains each attempt.
        return
    if "--self-test" in sys.argv:
        from smoke_test import run
        index = sys.argv.index("--self-test")
        try:
            run(Path(sys.argv[index + 1]), online="--online" in sys.argv, pdf="--pdf" in sys.argv)
        except RuntimeError:
            # The report contains the failure; avoid a blocking frozen-app dialog.
            raise SystemExit(1)
        return
    if "--pdf-export" in sys.argv:
        # Offline batch entry point for existing local XML and verified image caches.
        # Credentials are intentionally not accepted as command-line arguments.
        from pdf_export import PdfConfig, PdfExporter
        from engine import save_json
        from library_store import state_dir
        index = sys.argv.index("--pdf-export")
        source, output = Path(sys.argv[index + 1]), Path(sys.argv[index + 2])
        try:
            result = PdfExporter(PdfConfig(source=source, output=output,
                layout="single" if "--single-column" in sys.argv else "double", browser=browser,
                method="browser" if browser or "--browser-render" in sys.argv else "native")).run()
            save_json(state_dir(output) / "offline_export_result.json", {"ok": result["pending"] == 0, **result})
        except Exception as error:
            save_json(state_dir(output) / "offline_export_result.json", {"ok": False, "error": str(error)})
        return
    from ui import App
    from app_runtime import hold_app_mutex
    mutex = hold_app_mutex()
    App().mainloop()


if __name__ == "__main__":
    main()
