"""Regenerate the Windows ICO from app.png; requires Pillow.

app.svg is the original user-selected Alpine artwork. If the artwork changes,
render that SVG to app.png (at least 256 x 256) before running this script.
The prebuilt PNG and ICO are included, so normal builds need no SVG renderer.
"""
from pathlib import Path

from PIL import Image


def main():
    base = Path(__file__).resolve().parent
    with Image.open(base / "app.png") as source:
        if source.width != source.height or source.width < 256:
            raise ValueError("app.png must be square and at least 256 x 256 pixels")
        icon = source.convert("RGBA")
    icon.save(base / "app.ico", sizes=[
        (16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256),
    ])


if __name__ == "__main__":
    main()
