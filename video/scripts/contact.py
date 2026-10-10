"""Tile QA stills into contact sheets: python contact.py <dir> <out-prefix> [cols]"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

src, out = Path(sys.argv[1]), sys.argv[2]
cols = int(sys.argv[3]) if len(sys.argv) > 3 else 3
files = sorted(src.glob("*.png"))
first = Image.open(files[0])
tw = 640 if first.width > first.height else 360
th = round(first.height * tw / first.width)
per = cols * (3 if first.width > first.height else 2)
for page in range(0, len(files), per):
    chunk = files[page : page + per]
    rows = -(-len(chunk) // cols)
    sheet = Image.new("RGB", (cols * tw, rows * (th + 28)), "white")
    d = ImageDraw.Draw(sheet)
    for i, f in enumerate(chunk):
        im = Image.open(f).convert("RGB").resize((tw, th))
        x, y = (i % cols) * tw, (i // cols) * (th + 28)
        sheet.paste(im, (x, y + 28))
        d.text((x + 6, y + 6), f.stem, fill="black")
    sheet.save(f"{out}-{page // per:02d}.png")
    print(f"{out}-{page // per:02d}.png")
