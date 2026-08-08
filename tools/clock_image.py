#!/usr/bin/env python3
"""Generate a black-background white-text clock image (400x300) for ZKC42V."""
import datetime
from PIL import Image, ImageDraw, ImageFont

W, H = 400, 300
now = datetime.datetime.now()
img = Image.new("RGB", (W, H), (0, 0, 0))
d = ImageDraw.Draw(img)

def font(size):
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()

time_str = now.strftime("%H:%M")
date_str = now.strftime("%Y-%m-%d %a")

big = font(110)
small = font(32)

bb = d.textbbox((0, 0), time_str, font=big)
d.text(((W - (bb[2] - bb[0])) // 2, 70), time_str, fill=(255, 255, 255), font=big)
bb2 = d.textbbox((0, 0), date_str, font=small)
d.text(((W - (bb2[2] - bb2[0])) // 2, 215), date_str, fill=(255, 255, 255), font=small)
d.rectangle([2, 2, W - 3, H - 3], outline=(255, 255, 255), width=2)

img.save("/tmp/epaper-clock.png")
print("clock image saved: /tmp/epaper-clock.png")
