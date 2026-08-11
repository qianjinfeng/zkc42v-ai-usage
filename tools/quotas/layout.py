"""400×300 BWR (black/white/red) high-contrast layout for four quota rows."""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .models import QuotaRecord

W, H = 400, 300

# Pure BWR only — no grays (grays dither poorly / look washed on e-paper)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
# Pure red so make_image.classify() hits the red plane reliably
RED = (255, 0, 0)

BEIJING = ZoneInfo("Asia/Shanghai")

# Canonical short / long windows shown on each row
WINDOW_ORDER = ("5h", "week")


def beijing_now(now: Optional[datetime] = None) -> datetime:
    """Return datetime in Beijing time (UTC+8)."""
    if now is None:
        return datetime.now(BEIJING)
    if now.tzinfo is None:
        return now.replace(tzinfo=BEIJING)
    return now.astimezone(BEIJING)


def to_beijing(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Assume UTC when timezone-naive ISO from APIs
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING)


def format_beijing(dt_or_iso: datetime | str, fmt: str = "%m-%d %H:%M") -> str:
    """Format a datetime or ISO string as Beijing time."""
    if isinstance(dt_or_iso, str):
        s = dt_or_iso.strip()
        if not s:
            return ""
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return s[:16]
    else:
        dt = dt_or_iso
    return to_beijing(dt).strftime(fmt)


_FONTS_DIR = Path(__file__).resolve().parents[2] / "fonts"


def _font(size: int, *, bold: bool = True) -> ImageFont.ImageFont:
    """Latin/digit stack following the manufacturer's own choice.

    ZKONG (ZKC42V's maker) officially licenses **Arial** for its ESL panels,
    citing its smooth, even edges as ideal for small screens; SES-imagotag's
    e-ink guide likewise says prefer heavy bold sans-serif at small sizes.
    So English/digits → Arial (Bold for display & data, Regular fallback).
    CJK stays on Heiti (STHeiti) via _cjk_font.
    """
    if bold:
        for p in (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
        roboto = _FONTS_DIR / "Roboto.ttf"
        if roboto.is_file():
            try:
                f = ImageFont.truetype(str(roboto), size)
                out = f.set_variation_by_axes([700])
                return out or f
            except Exception:
                pass
    else:
        for p in (
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/SFNSMono.ttf",
        ):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _remaining(rec: QuotaRecord) -> Optional[float]:
    if rec.remaining_percent is not None:
        return float(rec.remaining_percent)
    if rec.used_percent is not None:
        return max(0.0, 100.0 - float(rec.used_percent))
    return None


def _win_remaining_percent(w: dict[str, Any]) -> Optional[float]:
    if w.get("remaining_percent") is not None:
        try:
            return float(w["remaining_percent"])
        except (TypeError, ValueError):
            pass
    if w.get("used_percent") is not None:
        try:
            return max(0.0, 100.0 - float(w["used_percent"]))
        except (TypeError, ValueError):
            pass
    rem, lim = w.get("remaining"), w.get("limit")
    try:
        if rem is not None and lim is not None and float(lim) > 0:
            return max(0.0, min(100.0, float(rem) / float(lim) * 100.0))
    except (TypeError, ValueError):
        pass
    return None


def _win_used_percent(w: dict[str, Any]) -> Optional[float]:
    if w.get("used_percent") is not None:
        try:
            return float(w["used_percent"])
        except (TypeError, ValueError):
            pass
    rem = _win_remaining_percent(w)
    if rem is not None:
        return max(0.0, 100.0 - rem)
    return None


def _classify_window_kind(w: dict[str, Any]) -> Optional[str]:
    """Map a raw window dict to canonical '5h' or 'week' (else None)."""
    label = str(w.get("label") or "").strip().lower()
    secs = w.get("window_seconds")
    if secs is not None:
        try:
            s = float(secs)
            # 1h–8h → short rolling bucket; ≥5d → week
            if 3600 <= s <= 8 * 3600:
                return "5h"
            if s >= 5 * 86400:
                return "week"
        except (TypeError, ValueError):
            pass
    if any(k in label for k in ("rolling", "5h", "5-hour", "five hour", "secondary")):
        return "5h"
    if any(k in label for k in ("weekly", "week", "primary")):
        # "primary" is often the weekly codex window when window_seconds missing
        if "primary" in label and secs is not None:
            try:
                if float(secs) < 5 * 86400:
                    return "5h"
            except (TypeError, ValueError):
                pass
        return "week"
    if label in ("monthly", "month"):
        return None
    return None


def pick_display_windows(rec: QuotaRecord) -> list[dict[str, Any]]:
    """Return up to two display slots: 5h then week (synthetic fallbacks ok)."""
    by_kind: dict[str, dict[str, Any]] = {}
    for w in rec.windows or []:
        if not isinstance(w, dict):
            continue
        kind = _classify_window_kind(w)
        if kind and kind not in by_kind:
            by_kind[kind] = dict(w)
            by_kind[kind]["kind"] = kind

    # Synthetic from top-level fields when a slot is empty
    if "week" not in by_kind and (rec.used_percent is not None or rec.remaining_percent is not None):
        # Prefer assigning top-level to week when no windows at all, or only 5h exists
        if not by_kind or "5h" in by_kind:
            by_kind.setdefault(
                "week",
                {
                    "kind": "week",
                    "label": "week",
                    "used_percent": rec.used_percent,
                    "remaining_percent": rec.remaining_percent,
                    "remaining": rec.remaining,
                    "limit": rec.limit,
                    "reset_at": rec.reset_at,
                },
            )
        elif "week" not in by_kind and "5h" not in by_kind:
            by_kind["week"] = {
                "kind": "week",
                "label": "week",
                "used_percent": rec.used_percent,
                "remaining_percent": rec.remaining_percent,
                "remaining": rec.remaining,
                "limit": rec.limit,
                "reset_at": rec.reset_at,
            }

    # If only one unclassified window exists, put it on week slot
    if not by_kind and rec.windows:
        w0 = rec.windows[0]
        if isinstance(w0, dict):
            by_kind["week"] = {**w0, "kind": "week"}
    if not by_kind and rec.status == "ok":
        by_kind["week"] = {
            "kind": "week",
            "label": "week",
            "used_percent": rec.used_percent,
            "remaining_percent": rec.remaining_percent,
            "remaining": rec.remaining,
            "limit": rec.limit,
            "reset_at": rec.reset_at,
        }

    out: list[dict[str, Any]] = []
    for kind in WINDOW_ORDER:
        if kind in by_kind:
            slot = dict(by_kind[kind])
            slot["kind"] = kind
            out.append(slot)
        else:
            out.append({"kind": kind, "missing": True})
    return out


def _balance_parts(w: dict[str, Any]) -> list[tuple[str, tuple[int, int, int]]]:
    """Build (text, color) runs: remaining balance as a single percentage."""
    if w.get("missing"):
        return [("—", BLACK)]
    rem_pct = _win_remaining_percent(w)
    if rem_pct is not None:
        return [(f"{rem_pct:.0f}%", RED)]
    used_pct = _win_used_percent(w)
    if used_pct is not None:
        return [(f"{max(0.0, 100.0 - used_pct):.0f}%", RED)]
    return [("—", BLACK)]


def _parse_dt(value: Any) -> Optional[datetime]:
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _relative_reset(w: dict[str, Any], now: Optional[datetime] = None) -> str:
    """Relative time until reset, e.g. '剩3天2时' / '剩2时30分' / '已重置'."""
    if w.get("missing") or not w.get("reset_at"):
        return "—"
    dt = _parse_dt(w["reset_at"])
    if dt is None:
        return "—"
    bj = beijing_now(now)
    delta = dt.astimezone(BEIJING) - bj
    if delta.total_seconds() <= 0:
        return "已重置"
    days, secs = delta.days, delta.seconds
    hours = secs // 3600
    mins = (secs % 3600) // 60
    if days > 0:
        return f"{days}天{hours}时"
    if hours > 0:
        return f"{hours}时{mins}分"
    return f"{mins}分"


def _draw_runs(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    parts: list[tuple[str, tuple[int, int, int]] | tuple[str, tuple[int, int, int], ImageFont.ImageFont]],
    font: ImageFont.ImageFont,
) -> int:
    """Draw runs left→right. Parts are (text, color) using `font`, or
    (text, color, run_font) to override the font for that run."""
    for part in parts:
        if len(part) == 3:
            text, color, run_font = part
        else:
            text, color = part
            run_font = font
        if not text:
            continue
        d.text((x, y), text, fill=color, font=run_font)
        bb = d.textbbox((x, y), text, font=run_font)
        x = bb[2]
    return x


def _is_alert(rec: QuotaRecord) -> bool:
    if rec.status != "ok":
        return True
    rem = _remaining(rec)
    if rem is not None and rem <= 15:
        return True
    for w in pick_display_windows(rec):
        if w.get("missing"):
            continue
        wr = _win_remaining_percent(w)
        if wr is not None and wr <= 15:
            return True
    return False


_QUOTES: list[dict[str, str]] | None = None


def load_quotes() -> list[dict[str, str]]:
    """Load 道德经 quotes + explanations from the local cache (once)."""
    global _QUOTES
    if _QUOTES is None:
        p = Path(__file__).resolve().parent / "daodejing.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _QUOTES = [x for x in data if isinstance(x, dict) and x.get("q") and x.get("e")]
        except Exception:
            _QUOTES = []
    return _QUOTES


def random_quote(now: Optional[datetime] = None) -> dict[str, str]:
    quotes = load_quotes()
    if not quotes:
        return {"q": "", "e": ""}
    return dict(random.choice(quotes))


def _wrap_cjk(d, text: str, font, max_w: int, max_lines: int) -> list[str]:
    """Wrap Chinese text into ≤ max_lines lines (breaks after punctuation)."""
    lines: list[str] = []
    while text and len(lines) < max_lines:
        if d.textbbox((0, 0), text, font=font)[2] <= max_w:
            lines.append(text)
            return lines
        lo, hi = 1, len(text)
        while lo < hi:  # longest prefix that fits
            mid = (lo + hi + 1) // 2
            if d.textbbox((0, 0), text[:mid], font=font)[2] <= max_w:
                lo = mid
            else:
                hi = mid - 1
        cut = lo
        for i in range(lo, 1, -1):  # break after punctuation if possible
            if text[i - 1] in "，。；、：！？":
                cut = i
                break
        lines.append(text[:cut])
        text = text[cut:].lstrip()
    if text and lines:
        lines[-1] = lines[-1][:-1] + "…"
    elif text:
        lines.append(text[: max(1, max_w // font.size)] + "…")
    return lines


def quantize_bwr(img: Image.Image) -> Image.Image:
    """Force every pixel to pure black / white / red (kill TTF anti-alias grays).

    Matches make_image.classify() intent so the red plane is used and text
    edges stay hard for e-paper contrast.
    """
    src = img.convert("RGB")
    out = Image.new("RGB", src.size, WHITE)
    sp = src.load()
    op = out.load()
    w, h = src.size
    for y in range(h):
        for x in range(w):
            r, g, b = sp[x, y]
            # red-ish (same spirit as make_image.classify)
            if r > 120 and r > g * 1.6 and r > b * 1.6 and (g + b) < 300:
                op[x, y] = RED
            elif (r + g + b) / 3 < 160:
                # slightly softer threshold so AA gray edges become solid ink
                op[x, y] = BLACK
            else:
                op[x, y] = WHITE
    return out


class _ScaledDraw:
    """ImageDraw proxy that scales all geometry by `scale` (for supersampling)."""

    def __init__(self, draw: ImageDraw.ImageDraw, scale: int):
        self._d = draw
        self._s = scale

    @staticmethod
    def _scale(v, s: int):
        if isinstance(v, (int, float)):
            return int(v * s)
        if isinstance(v, (list, tuple)):
            return type(v)(_ScaledDraw._scale(x, s) for x in v)
        return v

    def text(self, xy, text: str, **kw):
        self._d.text(self._scale(xy, self._s), text, **kw)

    def textbbox(self, xy, text: str, **kw):
        # Return bbox in INPUT (unscaled) coordinates so callers like
        # _draw_runs can accumulate x in the same space the proxy scales —
        # otherwise x is scaled twice and later segments drift off-canvas.
        bb = self._d.textbbox(self._scale(xy, self._s), text, **kw)
        return tuple(v // self._s for v in bb)

    def rectangle(self, xy, **kw):
        if kw.get("width"):
            kw = dict(kw, width=kw["width"] * self._s)
        self._d.rectangle(self._scale(xy, self._s), **kw)

    def line(self, xy, **kw):
        if kw.get("width"):
            kw = dict(kw, width=int(kw["width"] * self._s))
        self._d.line(self._scale(xy, self._s), **kw)

    def polygon(self, xy, **kw):
        if kw.get("width"):
            kw = dict(kw, width=int(kw["width"] * self._s))
        self._d.polygon(self._scale(xy, self._s), **kw)

    def ellipse(self, xy, **kw):
        if kw.get("width"):
            kw = dict(kw, width=int(kw["width"] * self._s))
        self._d.ellipse(self._scale(xy, self._s), **kw)


def _hexagon(cx: float, cy: float, r: float, rot: float = 0.0) -> list[tuple[float, float]]:
    return [
        (cx + r * math.cos(rot + i * math.pi / 3), cy + r * math.sin(rot + i * math.pi / 3))
        for i in range(6)
    ]


def draw_logo(d, x: float, y: float, size: float, kind: str, ink, bg) -> None:
    """Small monochrome geometric brand mark, centered at (x, y) in a `size` box.

    Simplified shapes (BWR panel has no grays or midtones), drawn in the tile's
    ink colour: codex → OpenAI-style hexagon knot, grok → xAI "X",
    kimi → crescent moon, opencode-go → terminal prompt chevron.
    """
    h = size / 2.0
    if kind == "codex":
        d.polygon(_hexagon(x, y, h * 0.96), fill=ink)
        d.polygon(_hexagon(x, y, h * 0.44, rot=math.pi / 6), fill=bg)
    elif kind == "grok":
        w = max(2.0, size * 0.16)
        d.line([(x - h * 0.8, y - h * 0.8), (x + h * 0.8, y + h * 0.8)], fill=ink, width=w)
        d.line([(x - h * 0.8, y + h * 0.8), (x + h * 0.8, y - h * 0.8)], fill=ink, width=w)
    elif kind == "kimi":
        d.ellipse([x - h, y - h, x + h, y + h], fill=ink)
        d.ellipse([x - h * 0.35, y - h * 0.9, x + h * 0.95, y + h * 0.9], fill=bg)
    elif kind == "opencode":
        w = max(2.0, size * 0.17)
        d.line(
            [(x - h * 0.6, y - h * 0.72), (x + h * 0.55, y), (x - h * 0.6, y + h * 0.72)],
            fill=ink,
            width=w,
            joint="curve",
        )


def _cjk_font(size: int) -> ImageFont.ImageFont:
    """CJK text (农历/节气/干支) — Heiti (STHeiti) keeps the clean sans look."""
    for p in (
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def china_calendar_line(now: Optional[datetime] = None) -> str:
    """Compact Chinese calendar line: '丙午年 · 立秋 · 六月廿七'."""
    try:
        from lunar_python import Lunar
    except Exception:
        return ""
    try:
        bj = beijing_now(now).replace(tzinfo=None)
        lunar = Lunar.fromDate(bj)
        month = lunar.getMonthInChinese() or ""
        day = lunar.getDayInChinese() or ""
        lunar_str = f"{month}月{day}" if month and day else ""
        year_gz = lunar.getYearInGanZhi() or ""
        now_s = bj.strftime("%Y-%m-%d %H:%M:%S")
        jieqi = ""
        for name, s in lunar.getJieQiTable().items():
            if s.toYmdHms() <= now_s:
                jieqi = name
        return " · ".join(p for p in (f"{year_gz}年" if year_gz else "", jieqi, lunar_str) if p)
    except Exception:
        return ""


def render_quota_image(
    records: Iterable[QuotaRecord],
    *,
    title: str = "AI Quotas",
    now: Optional[datetime] = None,
) -> Image.Image:
    """Render a compact, high-contrast BWR quota dashboard.

    The four services use a 2x2 card grid. Single-window services get a large
    remaining percentage; multi-window services get balance and reset lines.
    """
    recs = list(records)
    # Supersample 2x then LANCZOS-downscale so small text keeps smooth 1-bit
    # edges instead of quantized-AA jaggies (panel is BWR-only, no grays).
    S = 2
    img = Image.new("RGB", (W * S, H * S), WHITE)
    d = _ScaledDraw(ImageDraw.Draw(img), S)

    bj = beijing_now(now)

    # Font sizes below are display pixels; the actual font objects are 2x
    # because the canvas is supersampled.
    header_title_f = _font(13 * S, bold=True)
    header_time_f = _font(22 * S, bold=True)
    header_cjk_f = _cjk_font(15 * S)
    name_f = _font(14 * S, bold=True)
    hero_f = _font(23 * S, bold=True)
    latin_f = _font(12 * S, bold=True)
    balance_f = _font(17 * S, bold=True)
    cjk_detail_f = _cjk_font(11 * S)
    alert_f = _font(12 * S, bold=True)
    alert_detail_f = _font(10 * S, bold=False)
    quote_f = _cjk_font(21 * S)
    quote_explain_f = _cjk_font(12 * S)

    # --- Header: title + clock on the first line, Chinese calendar below. ---
    header_h = 46
    d.text((10, 5), title.upper(), fill=BLACK, font=header_title_f)
    time_str = bj.strftime("%m-%d %H:%M")
    tw = d.textbbox((0, 0), time_str, font=header_time_f)[2]
    d.text((W - 10 - tw, 3), time_str, fill=BLACK, font=header_time_f)
    d.text((10, 28), china_calendar_line(now), fill=BLACK, font=header_cjk_f)
    d.line([(0, header_h - 1), (W, header_h - 1)], fill=BLACK, width=1)

    # --- Bottom bar: quote + explanation, compact and always on-canvas. ---
    # quote_f.size includes the supersampling factor; divide by S when
    # calculating line spacing. The previous code used the scaled size here,
    # which made long quotes run off the bottom of the panel.
    quote = random_quote()
    q_lines: list[str] = []
    e_lines: list[str] = []
    q_line_h = max(1, round(quote_f.size / S * 1.12))
    e_line_h = max(1, round(quote_explain_f.size / S * 1.16))
    if quote.get("q"):
        q_lines = _wrap_cjk(d, quote["q"], quote_f, W - 16, 2)
        e_lines = _wrap_cjk(d, quote["e"], quote_explain_f, W - 16, 2)

    # Shrink the quote panel for short quotes and let the service cards use the
    # recovered pixels. Long quotes still get enough height for two lines of
    # text plus a small top/bottom breathing room.
    quote_content_h = 7 + q_line_h * len(q_lines) + 4 + e_line_h * len(e_lines) + 7
    # Keep the 2x2 service cards compact.  The quote content rarely needs the
    # full minimum; reserving this fixed lower band prevents the cards from
    # growing into large, mostly empty rectangles.
    quote_h = max(112, quote_content_h)
    quote_y0 = H - quote_h
    if q_lines:
        quote_block_h = q_line_h * len(q_lines) + 4 + e_line_h * len(e_lines)
        y = quote_y0 + max(7, (quote_h - quote_block_h) // 2)
        for ln in q_lines:
            d.text((8, y), ln, fill=BLACK, font=quote_f)
            y += q_line_h
        y += 4
        for ln in e_lines:
            d.text((8, y), ln, fill=BLACK, font=quote_explain_f)
            y += e_line_h
    d.line([(0, quote_y0 - 1), (W, quote_y0 - 1)], fill=BLACK, width=1)

    # --- Service area: four equal cards in a 2x2 grid. ---
    rows = (("codex", "grok"), ("kimi", "opencode-go"))
    top = header_h
    row_h = (quote_y0 - top) // len(rows)
    kind_label = {"5h": "5h", "week": "wk"}
    records_by_name = {r.name: r for r in recs}

    for ri, row_names in enumerate(rows):
        y0 = top + ri * row_h
        if ri > 0:
            d.line([(0, y0), (W, y0)], fill=BLACK, width=1)
        ncols = len(row_names)
        for si, name in enumerate(row_names):
            col_x0 = si * (W // ncols)
            col_x1 = W if si == ncols - 1 else (si + 1) * (W // ncols)
            if si > 0:
                d.line([(col_x0, y0), (col_x0, y0 + row_h)], fill=BLACK, width=1)

            rec = records_by_name.get(name)
            if rec is None:
                rec = QuotaRecord(name=name, status="unavailable", detail="missing")
            ink = BLACK
            alert = _is_alert(rec)
            text_y = y0 + 6
            logo_kind = {"opencode-go": "opencode"}.get(name, name)
            draw_logo(d, col_x0 + 16, y0 + 18, 17.0, logo_kind, ink, WHITE)
            name_x = col_x0 + 28
            d.text((name_x, text_y), name, fill=(RED if alert else ink), font=name_f)

            windows: list[dict[str, Any]] = []
            if rec.status == "ok":
                slots = pick_display_windows(rec)
                windows = [w for w in slots if not w.get("missing")]
                if len(windows) == 1:
                    # Large hero on the title line; label and reset below it.
                    w = windows[0]
                    kind = str(w.get("kind") or "?")
                    tag = kind_label.get(kind, kind)
                    parts = [
                        (f"{tag} · ", ink, latin_f),
                        (_relative_reset(w, now), ink, cjk_detail_f),
                    ]
                    _draw_runs(d, col_x0 + 8, y0 + 36, parts, latin_f)
                else:
                    # Separate balance and reset rows so long values cannot
                    # collide with the service name or leave the half-card.
                    balance_parts: list[tuple[str, tuple[int, int, int], object]] = []
                    reset_parts: list[tuple[str, tuple[int, int, int], object]] = []
                    for wi, w in enumerate(windows):
                        if wi:
                            balance_parts.append(("   ", ink, latin_f))
                            reset_parts.append((" / ", ink, latin_f))
                        kind = str(w.get("kind") or "?")
                        tag = kind_label.get(kind, kind)
                        balance_parts.append((f"{tag} ", ink, latin_f))
                        balance_parts.extend([(t, c, balance_f) for t, c in _balance_parts(w)])
                        reset_parts.append((_relative_reset(w, now), ink, cjk_detail_f))
                    _draw_runs(d, col_x0 + 8, y0 + 34, balance_parts, latin_f)
                    _draw_runs(d, col_x0 + 8, y0 + 53, reset_parts, latin_f)
            else:
                # Status and detail get their own lines instead of crowding
                # the service name on a single baseline.
                d.text((col_x0 + 8, y0 + 34), rec.status.upper(), fill=RED, font=alert_f)
                msg = rec.detail or "no details"
                if len(msg) > 22:
                    msg = msg[:19] + "..."
                d.text((col_x0 + 8, y0 + 53), msg, fill=BLACK, font=alert_detail_f)

            # Only single-window services get the large hero; multi-window
            # cards already show every balance on their second line.
            if len(windows) <= 1:
                hero = _remaining(rec)
                if hero is not None:
                    hero_txt = f"{hero:.0f}%"
                    hw = d.textbbox((0, 0), hero_txt, font=hero_f)[2]
                    d.text((col_x1 - 10 - hw, text_y), hero_txt, fill=RED, font=hero_f)

    # Downscale the supersampled render → smooth small text, then snap the
    # anti-aliased grays to pure BWR so the panel gets hard edges + real red plane
    img = img.resize((W, H), Image.LANCZOS)
    return quantize_bwr(img)


def image_has_ink(img: Image.Image, *, min_ink_pixels: int = 200) -> bool:
    """True if image is substantially non-blank (not all white/all black)."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    ink = 0
    white = 0
    black = 0
    red = 0
    px = rgb.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            avg = (r + g + b) / 3
            if r > 200 and g < 40 and b < 40:
                red += 1
                ink += 1
            elif avg < 40:
                black += 1
                ink += 1
            elif avg > 240:
                white += 1
            else:
                ink += 1
    total = w * h
    if black > total * 0.98 or white > total * 0.98:
        return False
    return ink >= min_ink_pixels


def image_bwr_only(img: Image.Image) -> bool:
    """True if every pixel is pure black, white, or pure red (no gray/other)."""
    px = img.convert("RGB").load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if p not in (BLACK, WHITE, RED):
                return False
    return True


def image_has_red(img: Image.Image) -> bool:
    px = img.convert("RGB").load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            if px[x, y] == RED:
                return True
    return False


def save_quota_png(records: Iterable[QuotaRecord], path: str | Path) -> Path:
    path = Path(path)
    img = render_quota_image(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path
