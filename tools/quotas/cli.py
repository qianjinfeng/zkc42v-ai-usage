#!/usr/bin/env python3
"""CLI: fetch provider quotas, render 400×300 image + frame.bin for e-paper."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Allow `python3 tools/quotas/cli.py` and `python3 -m tools.quotas`
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT / "tools"))

from quotas.credentials import discover_credentials  # noqa: E402
from quotas.fetch import fetch_all_quotas  # noqa: E402
from quotas.layout import image_has_ink, render_quota_image  # noqa: E402
from quotas.models import SERVICE_NAMES  # noqa: E402


def _build_frame(png_path: Path, frame_path: Path) -> None:
    """Reuse tools/make_image.py plane builder."""
    # Import sibling make_image
    tools_dir = Path(__file__).resolve().parents[1]
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import make_image  # type: ignore

    from PIL import Image

    img = Image.open(png_path).convert("RGB")
    if img.size != (make_image.W, make_image.H):
        img = img.resize((make_image.W, make_image.H))
    black, red = make_image.build_planes(img)
    import struct

    frame = b"ZKEPD1\n" + struct.pack("<HH", make_image.W, make_image.H) + black + red
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(frame)


def _print_records(records, as_json: bool) -> None:
    if as_json:
        print(json.dumps([r.to_dict() for r in records], indent=2, ensure_ascii=False))
        return
    for r in records:
        if r.status == "ok":
            rem = r.remaining_percent
            if rem is None and r.used_percent is not None:
                rem = 100.0 - r.used_percent
            used = r.used_percent
            parts = [f"{r.name:12s}", "ok"]
            if rem is not None:
                parts.append(f"remaining={rem:.1f}%")
            if used is not None:
                parts.append(f"used={used:.1f}%")
            if r.reset_at:
                parts.append(f"reset={r.reset_at}")
            if r.detail:
                parts.append(f"({r.detail})")
            print(" ".join(parts))
        else:
            print(f"{r.name:12s} {r.status}: {r.detail}")


def _records_signature(records) -> str:
    """Stable signature of the quota data actually shown on the panel.

    Rounds floats (API responses can drift in low bits) so an unchanged
    snapshot compares equal across refreshes. Timestamps are truncated to the
    minute because the panel only renders "MM-DD HH:MM" resets — sub-minute
    drift (e.g. microseconds from weekly APIs) must not count as a change.
    Ignores nothing else: any change in balance / reset / status means the
    panel must be re-pushed.
    """

    def norm_ts(v):
        if isinstance(v, str) and v and ("T" in v or ":" in v):
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%dT%H:%M")
            except ValueError:
                pass
        return v

    def norm(v):
        if isinstance(v, float):
            return round(v, 3)
        if isinstance(v, str):
            return norm_ts(v)
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [norm(x) for x in v]
        return v

    return json.dumps([norm(r.to_dict()) for r in records], sort_keys=True, ensure_ascii=False)


def run_once(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / "epaper-quotas.png"
    frame_path = out_dir / "epaper-quotas-frame.bin"

    creds = discover_credentials()
    if args.verbose:
        for name in SERVICE_NAMES:
            print(f"# cred {name}: {creds[name].redacted()}", file=sys.stderr)

    records = fetch_all_quotas(creds)
    _print_records(records, as_json=args.json)

    # Differential refresh: the firmware only supports full-plane WRITE_IMG +
    # whole-panel REFRESH (0x30/0x05), so re-pushing an unchanged snapshot would
    # just flash the panel for nothing. Skip render + BLE push when nothing
    # changed; --force overrides (e.g. to refresh the header clock).
    if args.send and not args.json and not args.force:
        last_file = out_dir / "epaper-quotas-last.json"
        sig = _records_signature(records)
        if last_file.is_file():
            try:
                prev = last_file.read_text()
            except OSError:
                prev = None
            if prev == sig:
                print(
                    "quota data unchanged; skipped render + BLE push "
                    "(--force to update anyway)",
                    file=sys.stderr,
                )
                return 0
        last_file.write_text(sig)

    img = render_quota_image(records)
    img.save(png_path)
    print(f"wrote {png_path} {img.size[0]}x{img.size[1]} ink={image_has_ink(img)}")
    _build_frame(png_path, frame_path)
    print(f"wrote {frame_path} bytes={frame_path.stat().st_size}")

    if args.send:
        uuid = os.environ.get("EPAPER_UUID", "").strip()
        if not uuid:
            print("EPAPER_UUID not set; skip BLE send", file=sys.stderr)
            return 2
        ble = Path(args.bleprobe)
        if not ble.is_file():
            print(f"bleprobe not found: {ble}", file=sys.stderr)
            return 2
        import subprocess

        cmd = [str(ble), "send", uuid, str(frame_path)]
        print("running:", " ".join(cmd), file=sys.stderr)
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"bleprobe send failed rc={rc}", file=sys.stderr)
            return rc

    # Exit 0 even with partial provider failures (isolated errors)
    return 0


def _in_active_window(start: str | None, end: str | None) -> bool:
    """True if now is inside the [start, end] active window (HH:MM, 24h).

    No window → always active. A start>end window wraps midnight (e.g.
    22:00–06:00 is active overnight).
    """
    if not start and not end:
        return True
    now = time.strftime("%H:%M")
    if start and end:
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end
    if start:
        return now >= start
    return now <= end


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch AI quotas and render for ZKC42V e-paper")
    ap.add_argument(
        "--out-dir",
        default=os.environ.get("EPAPER_QUOTA_OUT", "/tmp"),
        help="Directory for PNG + frame.bin (default: /tmp or EPAPER_QUOTA_OUT)",
    )
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON records")
    ap.add_argument("--send", action="store_true", help="Push frame via bleprobe send")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Push even if quota data is unchanged (delta refresh skips redundant pushes)",
    )
    ap.add_argument(
        "--bleprobe",
        default=str(_ROOT / "build" / "bleprobe"),
        help="Path to bleprobe binary",
    )
    ap.add_argument(
        "--loop",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Repeat every SECONDS (0 = once). Default interval for schedule: 900",
    )
    ap.add_argument(
        "--start",
        default=None,
        metavar="HH:MM",
        help="Active window start (e.g. 09:00). Outside the window refreshes are skipped.",
    )
    ap.add_argument(
        "--end",
        default=None,
        metavar="HH:MM",
        help="Active window end (e.g. 18:00). start>end wraps midnight.",
    )
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    if args.loop and args.loop > 0:
        while True:
            if _in_active_window(args.start, args.end):
                try:
                    run_once(args)
                except Exception as e:
                    print(f"loop iteration error: {e}", file=sys.stderr)
            else:
                print(
                    f"[{time.strftime('%H:%M')}] outside active window "
                    f"({args.start or '00:00'}–{args.end or '24:00'}); skipping",
                    file=sys.stderr,
                )
            time.sleep(args.loop)
    if _in_active_window(args.start, args.end):
        return run_once(args)
    print(
        f"outside active window ({args.start or '00:00'}–{args.end or '24:00'}); nothing to do",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
