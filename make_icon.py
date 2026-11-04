"""Optional icon regeneration; requires Pillow. The build uses the included ICO."""
from pathlib import Path
from PIL import Image, ImageDraw

im = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((16, 16, 1008, 1008), radius=238, fill="#087F8C")
d.rounded_rectangle((222, 274, 650, 838), radius=54, fill="#69BDC4")
d.rounded_rectangle((342, 190, 802, 762), radius=54, fill="#FAFFFF")
d.rounded_rectangle((430, 290, 704, 318), radius=14, fill="#B4D5DC")
d.rounded_rectangle((430, 362, 628, 390), radius=14, fill="#B4D5DC")
d.line((572, 467, 572, 638), fill="#087F8C", width=32)
d.line((496, 574, 572, 650, 648, 574), fill="#087F8C", width=32, joint="curve")
im = im.resize((256, 256), Image.Resampling.LANCZOS)
im.save(Path(__file__).with_name("app.png"))
im.save(Path(__file__).with_name("app.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
