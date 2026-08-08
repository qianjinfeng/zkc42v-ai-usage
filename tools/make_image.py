#!/usr/bin/env python3
"""Convert an image to ZKC42V (400x300 BWR) e-paper planes.

Outputs:
  black.bin / red.bin   raw 1bpp planes (400x300, MSB first, row-major)
  frame.bin             combined frame for the Swift `send` tool:
                        magic "ZKEPD1\n" + u16le w + u16le h + black + red

Color mapping (BWR):
  red-ish pixel   -> red plane = 1, black plane = 0
  dark pixel      -> black plane = 1, red plane = 0
  otherwise       -> white (both 0)

Polarity (confirmed from EPD-nRF5 SSD16xx driver):
  SSD16xx_Clear() fills RAM with 0xFF to clear to WHITE, so on this panel
  bit=1 -> white, bit=0 -> black (RAM1) / red (RAM2).  The plane bytes we
  emit below are already in this polarity (1=white, 0=ink).
"""

import argparse
import struct
from PIL import Image

W, H = 400, 300


def classify(px):
    """px: (r,g,b) -> 'R' | 'K' | 'W'"""
    r, g, b = px
    # red: strong red channel, weak green/blue
    if r > 120 and r > g * 1.6 and r > b * 1.6 and (g + b) < 300:
        return "R"
    # black: dark overall
    if (r + g + b) / 3 < 140:
        return "K"
    return "W"


def build_planes(img):
    # start from all-white planes (all bits 1), then punch out ink pixels
    black = bytearray(b"\xff" * ((W * H) // 8))
    red = bytearray(b"\xff" * ((W * H) // 8))
    for y in range(H):
        for x in range(W):
            cls = classify(img.getpixel((x, y)))
            bit = 7 - (x % 8)
            bi = (y * W + x) // 8
            if cls == "K":
                black[bi] &= ~(1 << bit)
            elif cls == "R":
                red[bi] &= ~(1 << bit)
    return bytes(black), bytes(red)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", default="frame.bin")
    ap.add_argument("--no-fit", action="store_true",
                    help="stretch to 400x300 instead of aspect-fit")
    args = ap.parse_args()

    img = Image.open(args.input).convert("RGB")
    if args.no_fit:
        img = img.resize((W, H), Image.LANCZOS)
    else:
        img.thumbnail((W, H), Image.LANCZOS)
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        canvas.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
        img = canvas

    black, red = build_planes(img)
    frame = b"ZKEPD1\n" + struct.pack("<HH", W, H) + black + red
    with open(args.out, "wb") as f:
        f.write(frame)
    print(f"wrote {args.out}: {W}x{H}, black {len(black)}B + red {len(red)}B")


if __name__ == "__main__":
    main()
