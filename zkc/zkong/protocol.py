"""EPD-nRF5 / Zkong ESL BLE Protocol encoder and compressor."""

from __future__ import annotations

import struct
from typing import List, Tuple

CMD_SERVICE_UUID = "62750001-D828-918D-FB46-B6C11C675AEC"
CMD_CHAR_UUID = "62750002-D828-918D-FB46-B6C11C675AEC"
FW_CHAR_UUID = "62750003-D828-918D-FB46-B6C11C675AEC"


def rle_compress(data: bytes) -> bytes:
    """Pack-bits / RLE compression matching Zkong ESL / epdiy.cn firmware.
    
    Format:
      - Repeat run (3..130 identical bytes): control byte = 0x80 | (run_len - 3), followed by 1 data byte
      - Literal run (1..128 distinct bytes): control byte = (run_len - 1), followed by raw bytes
    """
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


def rle_chunks(plane_rle: bytes, plane_flag: int, chunk_size: int = 233) -> List[bytes]:
    """Split an RLE stream strictly at code boundaries.
    
    Prefix each chunk with WRITE_IMG command (0x30) and flags:
      - 0x04: RLE compression indicator
      - plane_flag: 0x00 for Black/White plane, 0x01 for Red plane
      - 0x02: First chunk of the plane
    """
    chunks: List[bytes] = []
    i = 0
    start = 0
    while i < len(plane_rle):
        control = plane_rle[i]
        code_len = 2 if (control & 0x80) else (int(control) + 2)
        if i - start + code_len > chunk_size and i > start:
            chunks.append(plane_rle[start:i])
            start = i
        i += code_len
    if i > start:
        chunks.append(plane_rle[start:i])
        
    out: List[bytes] = []
    for idx, c in enumerate(chunks):
        flag = 0x04 | plane_flag | (0x02 if idx == 0 else 0x00)
        out.append(b"\x30" + bytes([flag]) + c)
    return out


def build_frame(width: int, height: int, black_plane: bytes, red_plane: bytes) -> bytes:
    """Create a ZKEPD1 binary frame."""
    expected_len = (width * height + 7) // 8
    if len(black_plane) != expected_len or len(red_plane) != expected_len:
        raise ValueError(
            f"Plane length mismatch: expected {expected_len}B each for {width}x{height}, "
            f"got black={len(black_plane)}B, red={len(red_plane)}B"
        )
    return b"ZKEPD1\n" + struct.pack("<HH", width, height) + black_plane + red_plane


def parse_frame(frame_bytes: bytes) -> Tuple[int, int, bytes, bytes]:
    """Parse a ZKEPD1 binary frame."""
    if len(frame_bytes) <= 10 or frame_bytes[:7] != b"ZKEPD1\n":
        raise ValueError("Invalid ZKEPD1 frame header")
    width, height = struct.unpack("<HH", frame_bytes[7:11])
    plane_size = (width * height + 7) // 8
    if len(frame_bytes) != 11 + plane_size * 2:
        raise ValueError(f"Frame length mismatch: {len(frame_bytes)} != 11 + {plane_size}*2")
    black = frame_bytes[11 : 11 + plane_size]
    red = frame_bytes[11 + plane_size : 11 + plane_size * 2]
    return width, height, black, red


def build_send_steps(
    black_plane: bytes,
    red_plane: bytes,
    init_param: int = 0x02,
    slot: int = 0,
    chunk_size: int = 233,
) -> List[Tuple[bytes, bool, float, str]]:
    """Build the exact list of BLE write steps for transmitting an image.
    
    Returns list of: (payload_bytes, with_response, delay_after_sec, description)
    """
    black_rle = rle_compress(black_plane)
    red_rle = rle_compress(red_plane)
    
    black_chunks = rle_chunks(black_rle, plane_flag=0, chunk_size=chunk_size)
    red_chunks = rle_chunks(red_rle, plane_flag=1, chunk_size=chunk_size)
    all_chunks = black_chunks + red_chunks

    steps: List[Tuple[bytes, bool, float, str]] = []
    
    # 1. INIT controller
    steps.append((bytes([0x01, init_param & 0xFF]), True, 0.25, f"INIT model=0x{init_param:02x}"))
    
    # 2. Select slot
    steps.append((bytes([0x31, 0x00, slot & 0xFF]), True, 0.08, f"SET_SLOT slot={slot}"))
    
    # 3. Write image planes
    for i, chunk in enumerate(all_chunks):
        is_last = (i == len(all_chunks) - 1)
        delay = 0.25 if is_last else 0.03
        desc = f"WRITE_IMG chunk {i+1}/{len(all_chunks)} ({len(chunk)}B)"
        steps.append((chunk, False, delay, desc))
        
    # 4. Refresh display
    steps.append((bytes([0x05]), True, 1.0, "REFRESH"))
    
    return steps
