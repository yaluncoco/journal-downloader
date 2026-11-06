"""Windows entry point. No console; optional credentials use Windows DPAPI."""
from pathlib import Path
import sys


def main():
    if "--pdf-check" in sys.argv:
        from pdf_browser import diagnose
        from engine import ActionRequired
        index = sys.argv.index("--pdf-check")
        try:
            diagnose(Path(sys.argv[index + 1]))
        except ActionRequired:
            pass  # The machine-readable report already contains each attempt.
        return
    if "--self-test" in sys.argv:
        from smoke_test import run
        index = sys.argv.index("--self-test")
        run(Path(sys.argv[index + 1]), online="--online" in sys.argv, pdf="--pdf" in sys.argv)
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
                layout="single" if "--single-column" in sys.argv else "double")).run()
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
