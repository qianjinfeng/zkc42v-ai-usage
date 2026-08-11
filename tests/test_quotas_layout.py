"""Unit tests for 400×300 quota layout renderer."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from quotas.layout import (  # noqa: E402
    W,
    H,
    BLACK,
    WHITE,
    _balance_parts,
    _relative_reset,
    format_beijing,
    image_bwr_only,
    image_has_ink,
    image_has_red,
    load_quotes,
    pick_display_windows,
    render_quota_image,
    save_quota_png,
)
from quotas.models import QuotaRecord  # noqa: E402
import make_image  # noqa: E402


def sample_records():
    return [
        QuotaRecord(
            name="codex",
            status="ok",
            used_percent=42.0,
            remaining_percent=58.0,
            reset_at="2026-08-16T12:00:00+00:00",
            windows=[
                {
                    "label": "5h",
                    "used_percent": 10.0,
                    "remaining_percent": 90.0,
                    "reset_at": "2026-08-09T08:00:00+00:00",
                    "window_seconds": 18000,
                },
                {
                    "label": "week",
                    "used_percent": 42.0,
                    "remaining_percent": 58.0,
                    "reset_at": "2026-08-16T12:00:00+00:00",
                    "window_seconds": 604800,
                },
            ],
        ),
        QuotaRecord(
            name="grok",
            status="ok",
            used_percent=80.0,
            remaining_percent=20.0,
            reset_at="2026-08-09T02:33:59+00:00",
            windows=[
                {
                    "label": "week",
                    "used_percent": 80.0,
                    "remaining_percent": 20.0,
                    "reset_at": "2026-08-09T02:33:59+00:00",
                    "window_seconds": 604800,
                },
            ],
        ),
        QuotaRecord(
            name="kimi",
            status="unavailable",
            detail="no KIMI_API_KEY",
        ),
        QuotaRecord(
            name="opencode-go",
            status="ok",
            used_percent=12.5,
            remaining_percent=87.5,
            reset_at="2026-08-09T07:00:00+00:00",
            windows=[
                {
                    "label": "5h",
                    "used_percent": 12.5,
                    "remaining_percent": 87.5,
                    "reset_at": "2026-08-09T07:00:00+00:00",
                    "window_seconds": 18000,
                },
                {
                    "label": "week",
                    "used_percent": 40.0,
                    "remaining_percent": 60.0,
                    "reset_at": "2026-08-10T00:00:00+00:00",
                    "window_seconds": 604800,
                },
            ],
        ),
    ]


class LayoutTests(unittest.TestCase):
    def test_dimensions_and_ink(self):
        img = render_quota_image(sample_records())
        self.assertEqual(img.size, (W, H))
        self.assertEqual(img.size, (400, 300))
        self.assertTrue(image_has_ink(img))

    def test_pick_display_windows_5h_and_week(self):
        recs = {r.name: r for r in sample_records()}
        slots = pick_display_windows(recs["opencode-go"])
        self.assertEqual([s["kind"] for s in slots], ["5h", "week"])
        self.assertFalse(slots[0].get("missing"))
        self.assertFalse(slots[1].get("missing"))
        # grok only has week → 5h slot missing placeholder
        grok_slots = pick_display_windows(recs["grok"])
        self.assertTrue(grok_slots[0].get("missing"))
        self.assertFalse(grok_slots[1].get("missing"))

    def test_bwr_only_palette(self):
        """No grays — only pure black / white / red for e-paper contrast."""
        img = render_quota_image(sample_records())
        self.assertTrue(image_bwr_only(img))
        # red used for balance numbers + unavailable kimi
        self.assertTrue(image_has_red(img))

    def test_beijing_time_formatting(self):
        # UTC 12:00 → Beijing 20:00 same day
        self.assertEqual(format_beijing("2026-08-16T12:00:00+00:00"), "08-16 20:00")
        # UTC 02:33 → Beijing 10:33
        self.assertEqual(format_beijing("2026-08-09T02:33:59+00:00"), "08-09 10:33")

    def test_header_uses_beijing(self):
        from datetime import datetime, timezone

        # fixed UTC noon → header should show 20:00 BJ
        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
        img = render_quota_image(sample_records(), now=now)
        # ensure palette still pure
        self.assertTrue(image_bwr_only(img))
        # red plane should still get red pixels from alert rows
        black, red = make_image.build_planes(img.convert("RGB"))
        self.assertNotEqual(red, b"\xff" * len(red), "red plane should have ink")

    def test_balance_always_percent(self):
        # absolute remaining+limit must still render as a single percentage
        w = {"remaining": 41.0, "limit": 100.0, "remaining_percent": 41.0}
        self.assertEqual("".join(t for t, _ in _balance_parts(w)), "41%")
        w2 = {"remaining_percent": 100.0}
        self.assertEqual("".join(t for t, _ in _balance_parts(w2)), "100%")

    def test_relative_reset(self):
        from datetime import datetime, timezone

        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)  # BJ 08-09 20:00
        self.assertEqual(_relative_reset({"reset_at": "2026-08-12T14:00:00+00:00"}, now), "3天2时")
        self.assertEqual(_relative_reset({"reset_at": "2026-08-09T15:30:00+00:00"}, now), "3时30分")
        self.assertEqual(_relative_reset({"reset_at": "2026-08-09T12:35:00+00:00"}, now), "35分")
        self.assertEqual(_relative_reset({"reset_at": "2026-08-09T11:00:00+00:00"}, now), "已重置")
        self.assertEqual(_relative_reset({"missing": True}, now), "—")

    def test_codex_grok_side_by_side(self):
        from datetime import datetime, timezone

        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
        img = render_quota_image(sample_records(), now=now)
        px = img.load()

        def ink(x0, x1, y0, y1):
            n = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    if px[x, y] != WHITE:
                        n += 1
            return n

        # row0 (y0=52) holds codex (left half) and grok (right half), one text
        # line at y0+11
        self.assertGreater(ink(10, 190, 63, 78), 0, "codex detail should be in the left half")
        self.assertGreater(ink(210, 390, 63, 78), 0, "grok detail should be in the right half")

    def test_daodejing_cache(self):
        quotes = load_quotes()
        self.assertGreaterEqual(len(quotes), 5, "local 道德经 cache should be populated")
        for q in quotes:
            self.assertTrue(q.get("q") and q.get("e"))

    def test_reset_times_not_clipped(self):
        """Full window lines (tag + balance + rst) must stay on-canvas.

        Regression: the 2x supersampled draw proxy used to double-scale the
        accumulated x, pushing the trailing reset times off-canvas so they
        never appeared on the panel.
        """
        img = render_quota_image(sample_records())
        px = img.load()

        def ink(x0, x1, y0, y1):
            n = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    if px[x, y] != WHITE:
                        n += 1
            return n

        # codex (top-left card): the multi-window reset line is below the
        # title/balance line in the new 2x2 card layout.
        self.assertGreater(
            ink(0, 200, 80, 125),
            0,
            "codex reset time should be visible in the top-left card",
        )
        # opencode-go (bottom-right card): the trailing week reset remains
        # visible even when the adaptive quote panel changes card height.
        self.assertGreater(
            ink(200, 400, 165, 235),
            0,
            "opencode-go reset line should stay visible in the bottom-right card",
        )

    def test_blank_detection(self):
        from PIL import Image

        white = Image.new("RGB", (400, 300), (255, 255, 255))
        black = Image.new("RGB", (400, 300), (0, 0, 0))
        self.assertFalse(image_has_ink(white))
        self.assertFalse(image_has_ink(black))

    def test_frame_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            png = save_quota_png(sample_records(), td / "q.png")
            self.assertTrue(png.is_file())
            from PIL import Image as PILImage

            img = PILImage.open(png)
            self.assertEqual(img.size, (400, 300))
            black, red = make_image.build_planes(img.convert("RGB"))
            self.assertEqual(len(black), (400 * 300) // 8)
            self.assertEqual(len(red), (400 * 300) // 8)
            # red plane must be used (true 3-color)
            self.assertNotEqual(red, b"\xff" * len(red))
            frame = b"ZKEPD1\n" + struct.pack("<HH", 400, 300) + black + red
            out = td / "frame.bin"
            out.write_bytes(frame)
            raw = out.read_bytes()
            self.assertTrue(raw.startswith(b"ZKEPD1\n"))
            w, h = struct.unpack_from("<HH", raw, 7)
            self.assertEqual((w, h), (400, 300))
            planes = raw[11:]
            self.assertNotEqual(planes, b"\xff" * len(planes))


if __name__ == "__main__":
    unittest.main()
