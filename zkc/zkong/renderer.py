"""Image and text rendering pipeline for Zkong BWR E-Paper displays."""

from __future__ import annotations

import io
import os
import datetime
from pathlib import Path
from typing import Optional, List, Tuple
from PIL import Image, ImageDraw, ImageFont

from .models import FitMode, DitherMode, DeviceModel, get_model_spec

FONTS_DIR = Path(__file__).parent.parent / "fonts"
FONT_BOLD_PATH = FONTS_DIR / "HarmonyOS_Sans_SC_Bold.ttf"
FONT_REGULAR_PATH = FONTS_DIR / "HarmonyOS_Sans_SC_Regular.ttf"
FONT_ROBOTO_PATH = FONTS_DIR / "Roboto.ttf"


def get_font(path: Path | str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load TrueType font or fallback to default."""
    try:
        p = str(path)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    except Exception:
        pass
    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def classify_pixel_bwr(px: Tuple[int, int, int], red_threshold: int = 120) -> str:
    """Classify RGB pixel into 'R' (Red), 'K' (Black/Dark), or 'W' (White/Light)."""
    r, g, b = px[:3]
    # Red detection: prominent red channel, low green and blue
    if r > red_threshold and r > (g * 1.5) and r > (b * 1.5) and (g + b) < 320:
        return "R"
    # Black detection: overall low luminance
    luminance = (r * 299 + g * 587 + b * 114) // 1000
    if luminance < 135:
        return "K"
    return "W"


def prepare_image(
    image: Image.Image,
    target_width: int,
    target_height: int,
    fit: FitMode = FitMode.ASPECT_FIT,
    bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Resize / pad an image to match the target e-paper dimensions."""
    img = image.convert("RGB")
    if fit == FitMode.STRETCH:
        return img.resize((target_width, target_height), Image.LANCZOS)
    elif fit == FitMode.CROP:
        # Scale to fill then crop center
        scale = max(target_width / img.width, target_height / img.height)
        new_w, new_h = int(img.width * scale), int(img.height * scale)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - target_width) // 2
        top = (new_h - target_height) // 2
        return resized.crop((left, top, left + target_width, top + target_height))
    else:  # ASPECT_FIT
        img.thumbnail((target_width, target_height), Image.LANCZOS)
        canvas = Image.new("RGB", (target_width, target_height), bg_color)
        paste_x = (target_width - img.width) // 2
        paste_y = (target_height - img.height) // 2
        canvas.paste(img, (paste_x, paste_y))
        return canvas


def image_to_bwr_planes(
    image: Image.Image,
    width: int,
    height: int,
    fit: FitMode = FitMode.ASPECT_FIT,
    dither: DitherMode = DitherMode.THRESHOLD,
    red_threshold: int = 120,
) -> Tuple[bytes, bytes]:
    """Convert an arbitrary RGB image to Black and Red 1bpp planes.
    
    Polarity on SSD16xx / Zkong GR5513:
      - 1 = White / Clear (no ink)
      - 0 = Black ink (Black plane) or Red ink (Red plane)
    """
    img = prepare_image(image, width, height, fit=fit)
    plane_bytes = (width * height + 7) // 8
    
    black_plane = bytearray(b"\xff" * plane_bytes)
    red_plane = bytearray(b"\xff" * plane_bytes)

    for y in range(height):
        for x in range(width):
            px = img.getpixel((x, y))
            cls = classify_pixel_bwr(px, red_threshold=red_threshold)
            bit = 7 - (x % 8)
            byte_idx = (y * width + x) // 8
            
            if cls == "K":
                black_plane[byte_idx] &= ~(1 << bit)
            elif cls == "R":
                red_plane[byte_idx] &= ~(1 << bit)

    return bytes(black_plane), bytes(red_plane)


def bwr_planes_to_preview_image(black_plane: bytes, red_plane: bytes, width: int, height: int) -> Image.Image:
    """Reconstruct an RGB preview image from BWR planes."""
    preview = Image.new("RGB", (width, height), (255, 255, 255))
    pixels = preview.load()
    
    for y in range(height):
        for x in range(width):
            bit = 7 - (x % 8)
            byte_idx = (y * width + x) // 8
            
            is_black = (black_plane[byte_idx] & (1 << bit)) == 0
            is_red = (red_plane[byte_idx] & (1 << bit)) == 0
            
            if is_red:
                pixels[x, y] = (220, 20, 20)  # Red ink
            elif is_black:
                pixels[x, y] = (15, 15, 15)    # Black ink
            else:
                pixels[x, y] = (255, 255, 255) # White background
                
    return preview


def render_text_card(
    title: str,
    subtitle: Optional[str] = None,
    body_lines: Optional[List[str]] = None,
    footer: Optional[str] = None,
    badge: Optional[str] = None,
    badge_color: str = "red",
    model: str = "ZKC42V",
    invert: bool = False,
) -> Image.Image:
    """Render structured text card styled for ZKC42V (400x300) or ZKC21V (250x122)."""
    spec = get_model_spec(model)
    w, h = spec.width, spec.height
    is_small = (w <= 250 or h <= 150)

    bg_color = (20, 20, 20) if invert else (255, 255, 255)
    fg_color = (255, 255, 255) if invert else (0, 0, 0)
    red_color = (230, 20, 20)
    gray_color = (160, 160, 160) if invert else (80, 80, 80)

    im = Image.new("RGB", (w, h), bg_color)
    draw = ImageDraw.Draw(im)

    # Scale font sizes based on screen resolution
    title_size = 18 if is_small else 26
    subtitle_size = 12 if is_small else 16
    body_size = 12 if is_small else 16
    footer_size = 10 if is_small else 13
    badge_size = 11 if is_small else 14

    font_title = get_font(FONT_BOLD_PATH, title_size)
    font_sub = get_font(FONT_REGULAR_PATH, subtitle_size)
    font_body = get_font(FONT_REGULAR_PATH, body_size)
    font_footer = get_font(FONT_REGULAR_PATH, footer_size)
    font_badge = get_font(FONT_BOLD_PATH, badge_size)

    margin_x = 8 if is_small else 16
    margin_y = 8 if is_small else 16
    cur_y = margin_y

    # Header section: Title + optional badge
    draw.text((margin_x, cur_y), title, fill=fg_color, font=font_title)
    
    if badge:
        b_color = red_color if badge_color.lower() == "red" else fg_color
        # Measure badge text width
        try:
            bbox = draw.textbbox((0, 0), badge, font=font_badge)
            bw = bbox[2] - bbox[0] + (8 if is_small else 12)
            bh = bbox[3] - bbox[1] + (4 if is_small else 6)
        except Exception:
            bw, bh = (40, 16) if is_small else (60, 22)
            
        bx = w - margin_x - bw
        by = cur_y
        draw.rectangle([bx, by, bx + bw, by + bh], fill=b_color)
        draw.text((bx + (4 if is_small else 6), by + 1), badge, fill=(255, 255, 255), font=font_badge)

    cur_y += title_size + (4 if is_small else 8)

    if subtitle:
        draw.text((margin_x, cur_y), subtitle, fill=gray_color, font=font_sub)
        cur_y += subtitle_size + (4 if is_small else 6)

    # Divider line
    draw.line([(margin_x, cur_y), (w - margin_x, cur_y)], fill=red_color if not invert else fg_color, width=2)
    cur_y += 6 if is_small else 12

    # Body lines
    if body_lines:
        line_gap = 2 if is_small else 6
        for line in body_lines:
            if cur_y + body_size > h - (margin_y + (footer_size if footer else 0)):
                break
            # Highlight bullet points or keywords starting with * or [R] in red
            if line.startswith("[R]") or line.startswith("!"):
                draw.text((margin_x, cur_y), line.lstrip("[R]! "), fill=red_color, font=font_body)
            else:
                draw.text((margin_x, cur_y), line, fill=fg_color, font=font_body)
            cur_y += body_size + line_gap

    # Footer
    if footer:
        draw.text((margin_x, h - margin_y - footer_size), footer, fill=gray_color, font=font_footer)

    return im


def render_price_tag(
    product_name: str,
    price: str,
    original_price: Optional[str] = None,
    unit: Optional[str] = "件",
    spec: Optional[str] = None,
    origin: Optional[str] = None,
    barcode: Optional[str] = None,
    promo_badge: Optional[str] = None,
    qr_data: Optional[str] = None,
    model: str = "ZKC42V",
) -> Image.Image:
    """Render standard retail electronic shelf label (ESL) layout."""
    spec_model = get_model_spec(model)
    w, h = spec_model.width, spec_model.height
    is_small = (w <= 250 or h <= 150)

    im = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(im)

    red = (220, 20, 20)
    black = (0, 0, 0)
    gray = (100, 100, 100)

    # Clean price string
    clean_price = price.replace("￥", "").replace("¥", "").strip()

    if is_small:
        # Layout for ZKC21V (250x122)
        font_name = get_font(FONT_BOLD_PATH, 16)
        font_price = get_font(FONT_BOLD_PATH, 34)
        font_curr = get_font(FONT_BOLD_PATH, 16)
        font_unit = get_font(FONT_REGULAR_PATH, 12)
        font_sub = get_font(FONT_REGULAR_PATH, 11)
        font_promo = get_font(FONT_BOLD_PATH, 11)

        # Header bar or promo
        if promo_badge:
            draw.rectangle([6, 6, 46, 22], fill=red)
            draw.text((10, 7), promo_badge, fill=(255, 255, 255), font=font_promo)
            draw.text((52, 6), product_name[:12], fill=black, font=font_name)
        else:
            draw.text((8, 6), product_name[:16], fill=black, font=font_name)

        draw.line([(6, 26), (w - 6, 26)], fill=black, width=1)

        # Price section
        draw.text((8, 38), "￥", fill=red, font=font_curr)
        draw.text((24, 28), clean_price, fill=red, font=font_price)
        
        # Unit & Spec
        unit_text = f"/{unit}" if unit else ""
        draw.text((130, 42), unit_text, fill=black, font=font_unit)
        if spec:
            draw.text((130, 58), f"规: {spec}", fill=gray, font=font_sub)

        # Bottom section: origin / barcode / orig price
        draw.line([(6, 88), (w - 6, 88)], fill=gray, width=1)
        if original_price:
            orig_str = f"原价:￥{original_price}"
            draw.text((8, 96), orig_str, fill=gray, font=font_sub)
            # Strikethrough
            try:
                bb = draw.textbbox((8, 96), orig_str, font=font_sub)
                draw.line([(bb[0], (bb[1]+bb[3])//2), (bb[2], (bb[1]+bb[3])//2)], fill=gray, width=1)
            except Exception:
                pass
        elif origin:
            draw.text((8, 96), origin[:14], fill=gray, font=font_sub)
            
        if barcode:
            draw.text((w - 90, 96), barcode[:12], fill=black, font=font_sub)

    else:
        # Layout for ZKC42V (400x300)
        font_name = get_font(FONT_BOLD_PATH, 26)
        font_price = get_font(FONT_BOLD_PATH, 68)
        font_curr = get_font(FONT_BOLD_PATH, 32)
        font_orig = get_font(FONT_REGULAR_PATH, 20)
        font_unit = get_font(FONT_REGULAR_PATH, 20)
        font_sub = get_font(FONT_REGULAR_PATH, 16)
        font_promo = get_font(FONT_BOLD_PATH, 18)

        # Top Banner / Title
        top_y = 16
        if promo_badge:
            draw.rectangle([16, top_y, 80, top_y + 32], fill=red)
            draw.text((24, top_y + 4), promo_badge, fill=(255, 255, 255), font=font_promo)
            draw.text((92, top_y), product_name, fill=black, font=font_name)
        else:
            draw.text((16, top_y), product_name, fill=black, font=font_name)

        draw.line([(16, 60), (w - 16, 60)], fill=red, width=3)

        # Middle Left: Large Red Price
        draw.text((16, 95), "￥", fill=red, font=font_curr)
        draw.text((52, 72), clean_price, fill=red, font=font_price)
        
        # Unit
        if unit:
            draw.text((270, 105), f"/{unit}", fill=black, font=font_unit)

        # Original Price
        if original_price:
            orig_text = f"原价: ￥{original_price}"
            draw.text((20, 165), orig_text, fill=gray, font=font_orig)
            try:
                bb = draw.textbbox((20, 165), orig_text, font=font_orig)
                draw.line([(bb[0], (bb[1]+bb[3])//2), (bb[2], (bb[1]+bb[3])//2)], fill=gray, width=2)
            except Exception:
                pass

        # Meta attributes (spec, origin)
        meta_y = 205
        if spec:
            draw.text((20, meta_y), f"规格: {spec}", fill=black, font=font_sub)
            meta_y += 24
        if origin:
            draw.text((20, meta_y), f"产地: {origin}", fill=gray, font=font_sub)
            meta_y += 24

        # Footer Barcode / Info
        draw.line([(16, 255), (w - 16, 255)], fill=black, width=1)
        if barcode:
            draw.text((20, 268), f"条码: {barcode}", fill=black, font=font_sub)
        draw.text((w - 110, 268), "Zkong ESL", fill=gray, font=font_sub)

    return im


def render_clock_face(
    title: str = "EPD CLOCK",
    show_seconds: bool = False,
    show_date: bool = True,
    invert: bool = True,
    model: str = "ZKC42V",
) -> Image.Image:
    """Render modern high-contrast digital clock screen."""
    spec = get_model_spec(model)
    w, h = spec.width, spec.height
    is_small = (w <= 250 or h <= 150)

    bg_color = (15, 15, 15) if invert else (255, 255, 255)
    fg_color = (255, 255, 255) if invert else (15, 15, 15)
    red_color = (230, 30, 30)

    im = Image.new("RGB", (w, h), bg_color)
    draw = ImageDraw.Draw(im)

    now = datetime.datetime.now()
    time_str = now.strftime("%H:%M:%S" if show_seconds else "%H:%M")
    date_str = now.strftime("%Y-%m-%d  %A")

    if is_small:
        font_time = get_font(FONT_ROBOTO_PATH, 36)
        font_sub = get_font(FONT_REGULAR_PATH, 12)
        font_title = get_font(FONT_BOLD_PATH, 11)

        draw.text((8, 6), title, fill=red_color, font=font_title)
        draw.line([(8, 22), (w - 8, 22)], fill=fg_color, width=1)
        draw.text((w // 2 - 50, 32), time_str, fill=fg_color, font=font_time)
        if show_date:
            draw.text((8, 98), now.strftime("%Y-%m-%d"), fill=red_color if invert else red_color, font=font_sub)
    else:
        font_time = get_font(FONT_ROBOTO_PATH, 82)
        font_sub = get_font(FONT_REGULAR_PATH, 20)
        font_title = get_font(FONT_BOLD_PATH, 18)

        # Header
        draw.text((20, 16), title, fill=red_color, font=font_title)
        draw.line([(20, 46), (w - 20, 46)], fill=fg_color, width=2)
        
        # Center Clock
        draw.text((w // 2 - 130, 80), time_str, fill=fg_color, font=font_time)
        
        # Date & info
        if show_date:
            draw.line([(20, 210), (w - 20, 210)], fill=fg_color, width=1)
            draw.text((24, 230), date_str, fill=red_color, font=font_sub)
            draw.text((24, 260), "ZKC42V 4.2\" BWR E-Paper", fill=fg_color, font=font_sub)

    return im
