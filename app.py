"""Windows entry point. No console and no stored API credentials."""
from pathlib import Path
import sys


def main():
    if "--self-test" in sys.argv:
        from smoke_test import run
        index = sys.argv.index("--self-test")
        run(Path(sys.argv[index + 1]), online="--online" in sys.argv)
        return
    from ui import App
    App().mainloop()


if __name__ == "__main__":
    main()
