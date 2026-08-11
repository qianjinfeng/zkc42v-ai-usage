#!/usr/bin/env python3
"""Windows BLE bridge for the ZKC42V e-paper tag (port of mac/BLEProbe.swift).

Uses `bleak` (WinRT backend). The tag is addressed by its BLE MAC address
(XX:XX:XX:XX:XX:XX) as printed by `scan`, not a CoreBluetooth UUID.

Commands:
  bleprobe.py scan [seconds]              Discover nearby BLE devices
  bleprobe.py send <ADDR> <frame.bin>     Flash a frame to the tag
  bleprobe.py seq <ADDR> <listen> <c:hex>...  Sequential writes with notify listen
  bleprobe.py inspect <ADDR>              Dump GATT profile
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys
import time
from pathlib import Path

CMD_CHAR = "62750002-D828-918D-FB46-B6C11C675AEC"


def rle_compress(data: bytes) -> bytes:
    """Port of BLEProbe.swift rleCompress (matches epdiy.cn rle.js)."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        run_len = 1
        while i + run_len < n and run_len < 130 and data[i + run_len] == data[i]:
            run_len += 1
        if run_len >= 3:
            out.append(0x80 | (run_len - 3))
            out.append(data[i])
            i += run_len
        else:
            start = i
            length = 0
            while i < n and length < 127:
                if i + 2 < n and data[i] == data[i + 1] and data[i] == data[i + 2]:
                    break
                length += 1
                i += 1
            if length == 0:
                out.append(0x00)
                out.append(data[i])
                i += 1
            else:
                out.append(length - 1)
                out.extend(data[start : start + length])
    return bytes(out)


def rle_chunks(plane: bytes, plane_flag: int, chunk: int = 233) -> list[bytes]:
    """Split an RLE plane at code boundaries; prefix each chunk with WRITE_IMG(0x30, flag)."""
    chunks: list[bytes] = []
    i = 0
    start = 0
    while i < len(plane):
        control = plane[i]
        code_len = 2 if (control & 0x80) else (int(control) + 2)
        if i - start + code_len > chunk and i > start:
            chunks.append(plane[start:i])
            start = i
        i += code_len
    if i > start:
        chunks.append(plane[start:i])
    out: list[bytes] = []
    for idx, c in enumerate(chunks):
        flag = 0x04 | plane_flag | (0x02 if idx == 0 else 0x00)
        out.append(b"\x30" + bytes([flag]) + c)
    return out


def read_frame(path: str) -> tuple[int, int, bytes, bytes]:
    """Parse ZKEPD1 frame: magic, <w LE, h LE, black plane, red plane."""
    data = Path(path).read_bytes()
    if len(data) <= 10 or data[:7] != b"ZKEPD1\n":
        raise ValueError(f"bad frame file: {path}")
    w, h = struct.unpack("<HH", data[7:11])
    plane = (w * h) // 8
    if len(data) != 11 + plane * 2:
        raise ValueError(f"frame size mismatch: {len(data)} != 11 + {plane}*2")
    return w, h, data[11 : 11 + plane], data[11 + plane : 11 + plane * 2]


def hex_bytes(s: str) -> bytes:
    h = s.replace(" ", "")
    if len(h) % 2 != 0 or not h:
        raise ValueError(f"bad hex: {s!r}")
    return bytes.fromhex(h)


def find_char(services, uuid: str):
    for svc in services:
        for ch in svc.characteristics:
            if ch.uuid.lower() == uuid.lower():
                return ch
    return None


async def cmd_scan(seconds: int) -> int:
    from bleak import BleakScanner

    devices: dict[str, tuple] = {}

    def cb(device, adv):
        devices[device.address] = (device, adv)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(seconds)
    await scanner.stop()

    print(f"=== {len(devices)} unique devices ===")
    rows = []
    for addr, (dev, adv) in devices.items():
        rssi = getattr(adv, "rssi", None) if adv is not None else None
        if rssi is None:
            rssi = getattr(dev, "rssi", None)
        name = ""
        if dev is not None:
            name = dev.name or ""
        if adv is not None and not name:
            name = getattr(adv, "local_name", "") or ""
        mfr = getattr(adv, "manufacturer_data", None) if adv is not None else None
        mfr_hex = "".join(b.hex() for b in mfr.values()) if mfr else ""
        uuids = [str(u) for u in (getattr(adv, "service_uuids", []) or [])] if adv is not None else []
        rows.append((rssi if rssi is not None else -999, name, addr, uuids, mfr_hex))
    for rssi, name, addr, uuids, mfr_hex in sorted(rows, key=lambda r: r[0], reverse=True):
        svc = ",".join(uuids) if uuids else "-"
        print(f"{rssi:4d} dBm | {name} | {addr} | {svc}")
        if mfr_hex:
            print(f"        mfr={mfr_hex}")
    return 0


async def cmd_inspect(addr: str) -> int:
    from bleak import BleakClient

    async with BleakClient(addr, timeout=30) as client:
        print(f"connected: {addr}")
        for svc in client.services:
            print(f"service {svc.uuid}")
            for ch in svc.characteristics:
                props = ch.properties
                extra = ""
                if "read" in props:
                    try:
                        val = await client.read_gatt_char(ch)
                        asc = "".join(
                            chr(b) if 32 <= b < 127 else "."
                            for b in val
                        )
                        extra = f" value={val.hex()} '{asc}'"
                    except Exception:
                        pass
                print(f"  char {ch.uuid} [{','.join(props)}]{extra}")
    return 0


async def cmd_send(addr: str, frame_path: str, init_param: int = 0x02) -> int:
    from bleak import BleakClient, BleakScanner

    w, h, black, red = read_frame(frame_path)
    black_rle = rle_compress(black)
    red_rle = rle_compress(red)
    chunks = rle_chunks(black_rle, 0) + rle_chunks(red_rle, 1)
    print(f"frame: {w}x{h}, black {len(black)}B -> RLE {len(black_rle)}B, red {len(red)}B -> RLE {len(red_rle)}B")

    steps: list[tuple[bytes, bool, float]] = []
    steps.append((bytes([0x01, init_param]), True, 0.30))
    steps.append((bytes([0x31, 0x00, 0x00]), True, 0.10))
    for i, c in enumerate(chunks):
        last = i == len(chunks) - 1
        steps.append((c, False, 0.50 if last else 0.05))
    steps.append((bytes([0x05]), True, 1.0))
    print(f"total {len(steps)} steps, starting ...")
    for i, (data, resp, _) in enumerate(steps):
        if i == 0:
            print(f"+ INIT model=0x{init_param:02x}")
        elif i == 1:
            print("+ SET_SLOT slot=0")
        elif i == len(steps) - 1:
            print("+ REFRESH")
        elif i == 2:
            print(f"+ WRITE_IMG {len(chunks)} chunks")
        print(f"  step {i + 1}/{len(steps)} ({len(data)}B, {'resp' if resp else 'nresp'})")

    # Warm Windows' BLE device cache before connecting (direct connect is flaky).
    try:
        await BleakScanner().discover(timeout=4)
    except Exception:
        pass

    last_err = None
    for attempt in range(1, 4):
        try:
            async with BleakClient(addr, timeout=30) as client:
                ch = find_char(client.services, CMD_CHAR)
                if ch is None:
                    print("command characteristic not found", file=sys.stderr)
                    return 1
                for svc in client.services:
                    for c in svc.characteristics:
                        if "notify" in c.properties or "indicate" in c.properties:
                            try:
                                await client.start_notify(c)
                            except Exception:
                                pass
                await asyncio.sleep(1.0)
                print(f"connected (attempt {attempt}), sending ...")
                t0 = time.time()
                for i, (data, resp, delay) in enumerate(steps):
                    await client.write_gatt_char(ch, data, response=resp)
                    if i in (0, 1, 2, len(steps) - 1) or i % 50 == 0:
                        print(f"[+{time.time() - t0:.0f}s] step {i + 1}/{len(steps)}")
                    await asyncio.sleep(delay)
                print("all writes done, holding BLE 20s while EPD refreshes ...")
                await asyncio.sleep(20)
                return 0
        except Exception as e:
            last_err = e
            print(f"attempt {attempt} failed: {e}", file=sys.stderr)
            await asyncio.sleep(3)
    print(f"send failed after retries: {last_err}", file=sys.stderr)
    return 1


async def cmd_seq(addr: str, listen: int, pairs: list[tuple[str, bytes]]) -> int:
    from bleak import BleakClient

    async with BleakClient(addr, timeout=30) as client:
        for cid, data in pairs:
            ch = find_char(client.services, cid)
            if ch is None:
                print(f"char {cid} not found, skipping", file=sys.stderr)
                continue
            resp = bool(getattr(ch, "write", None))
            print(f"WRITE -> {cid}: {data.hex()}")
            await client.write_gatt_char(ch, data, response=resp)
            await asyncio.sleep(1.5)
        print(f"listening {listen}s ...")
        await asyncio.sleep(listen)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]
    if cmd == "scan":
        seconds = int(args[1]) if len(args) > 1 else 12
        return asyncio.run(cmd_scan(seconds))
    if cmd == "send":
        if len(args) < 3:
            print("usage: bleprobe.py send <ADDR> <frame.bin> [--initparam <hex>]")
            return 2
        init_param = 0x02
        i = 3
        while i < len(args):
            if args[i] == "--initparam" and i + 1 < len(args):
                init_param = hex_bytes(args[i + 1])[0]
                i += 2
            else:
                i += 1
        return asyncio.run(cmd_send(args[1], args[2], init_param=init_param))
    if cmd == "seq":
        if len(args) < 4:
            print("usage: bleprobe.py seq <ADDR> <listen> <char:hex> ...")
            return 2
        listen = int(args[2])
        pairs = []
        for pair in args[3:]:
            cid, _, hexs = pair.partition(":")
            pairs.append((cid, hex_bytes(hexs)))
        return asyncio.run(cmd_seq(args[1], listen, pairs))
    if cmd == "inspect":
        if len(args) < 2:
            print("usage: bleprobe.py inspect <ADDR>")
            return 2
        return asyncio.run(cmd_inspect(args[1]))
    print(f"unknown command {cmd!r}", file=sys.stderr)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
