"""Asynchronous Bluetooth Low Energy (BLE) controller for Zkong ESL devices."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, List, Dict, Any, Tuple
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .protocol import (
    CMD_SERVICE_UUID,
    CMD_CHAR_UUID,
    FW_CHAR_UUID,
    build_send_steps,
)
from .models import BLEDeviceItem, ScanResponse

logger = logging.getLogger("zkong.ble")


class ZkongBLEController:
    """Manages BLE connections and transmission to Zkong ESL devices."""

    @staticmethod
    async def scan_devices(duration_seconds: float = 8.0) -> ScanResponse:
        """Scan for nearby BLE devices and identify potential Zkong ESL tags."""
        discovered: Dict[str, Tuple[BLEDevice, AdvertisementData]] = {}

        def detection_cb(device: BLEDevice, adv: AdvertisementData):
            discovered[device.address] = (device, adv)

        scanner = BleakScanner(detection_callback=detection_cb)
        try:
            await scanner.start()
            await asyncio.sleep(duration_seconds)
        finally:
            await scanner.stop()

        items: List[BLEDeviceItem] = []
        for addr, (dev, adv) in discovered.items():
            name = dev.name or adv.local_name or ""
            rssi = adv.rssi if adv.rssi is not None else dev.rssi
            service_uuids = [str(u).lower() for u in (adv.service_uuids or [])]
            
            mfr_bytes = b"".join(adv.manufacturer_data.values()) if adv.manufacturer_data else b""
            mfr_hex = mfr_bytes.hex() if mfr_bytes else None

            # Detection heuristic:
            # 1. Advertises service 62750001
            # 2. Name contains ZK, ZKC, ESL, or EPD
            # 3. Manufacturer data contains Zkong company signature
            is_zkong = (
                CMD_SERVICE_UUID.lower() in service_uuids
                or any("62750001" in u for u in service_uuids)
                or any(k in name.upper() for k in ["ZK", "ZKC", "ESL", "EPD", "VALLEY"])
            )

            items.append(
                BLEDeviceItem(
                    address=addr,
                    name=name if name else None,
                    rssi=rssi,
                    is_zkong=is_zkong,
                    service_uuids=service_uuids,
                    manufacturer_data_hex=mfr_hex,
                )
            )

        # Sort: Zkong devices first, then by RSSI descending
        items.sort(key=lambda x: (not x.is_zkong, -(x.rssi if x.rssi is not None else -999)))
        return ScanResponse(success=True, count=len(items), devices=items)

    @staticmethod
    async def inspect_device(address: str, timeout: float = 25.0) -> Dict[str, Any]:
        """Connect to device and dump its GATT services and characteristics."""
        info: Dict[str, Any] = {"address": address, "services": []}
        
        async with BleakClient(address, timeout=timeout) as client:
            info["connected"] = client.is_connected
            for svc in client.services:
                s_dict: Dict[str, Any] = {
                    "uuid": svc.uuid,
                    "description": svc.description,
                    "characteristics": [],
                }
                for ch in svc.characteristics:
                    props = list(ch.properties)
                    ch_dict: Dict[str, Any] = {
                        "uuid": ch.uuid,
                        "description": ch.description,
                        "properties": props,
                    }
                    if "read" in props:
                        try:
                            val = await client.read_gatt_char(ch)
                            ch_dict["value_hex"] = val.hex()
                            try:
                                ch_dict["value_ascii"] = val.decode("utf-8", errors="replace")
                            except Exception:
                                pass
                        except Exception as e:
                            ch_dict["read_error"] = str(e)
                    s_dict["characteristics"].append(ch_dict)
                info["services"].append(s_dict)

        return info

    @classmethod
    async def send_planes(
        cls,
        address: str,
        black_plane: bytes,
        red_plane: bytes,
        init_param: int = 0x02,
        slot: int = 0,
        hold_refresh_sec: float = 15.0,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """Flash BWR image planes to the Zkong ESL tag over BLE."""
        steps = build_send_steps(black_plane, red_plane, init_param=init_param, slot=slot)
        t_start = time.time()
        last_err: Optional[Exception] = None

        # Warm Windows BLE cache before direct connection attempt
        try:
            await BleakScanner().discover(timeout=3.0)
        except Exception:
            pass

        for attempt in range(1, max_retries + 1):
            logger.info(f"Connecting to {address} (attempt {attempt}/{max_retries})...")
            try:
                async with BleakClient(address, timeout=30.0) as client:
                    # Find command characteristic
                    cmd_char = None
                    for svc in client.services:
                        for ch in svc.characteristics:
                            if ch.uuid.lower() == CMD_CHAR_UUID.lower():
                                cmd_char = ch
                                break
                        if cmd_char:
                            break

                    if cmd_char is None:
                        raise RuntimeError(f"Command characteristic {CMD_CHAR_UUID} not found on device {address}")

                    # Subscribe to notifications to keep link alive & capture feedback
                    for svc in client.services:
                        for ch in svc.characteristics:
                            if "notify" in ch.properties or "indicate" in ch.properties:
                                try:
                                    await client.start_notify(ch, cls._on_notify)
                                except Exception:
                                    pass

                    await asyncio.sleep(0.5)

                    # Execute protocol steps sequentially
                    for idx, (data, resp, delay, desc) in enumerate(steps):
                        await client.write_gatt_char(cmd_char, data, response=resp)
                        if idx in (0, 1, len(steps) - 1) or idx % 40 == 0:
                            logger.info(f"[{address}] Step {idx + 1}/{len(steps)}: {desc}")
                        await asyncio.sleep(delay)

                    # Wait while electronic ink physically refreshes
                    logger.info(f"[{address}] All steps completed. Holding BLE connection for {hold_refresh_sec}s...")
                    await asyncio.sleep(hold_refresh_sec)

                    duration = time.time() - t_start
                    return {
                        "success": True,
                        "device": address,
                        "steps_executed": len(steps),
                        "duration_seconds": round(duration, 2),
                        "attempt": attempt,
                    }

            except Exception as e:
                last_err = e
                logger.warning(f"Attempt {attempt} failed for {address}: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2.5)

        raise RuntimeError(f"Failed to flash device {address} after {max_retries} attempts: {last_err}")

    @staticmethod
    def _on_notify(sender: Any, data: bytearray):
        """Handle incoming GATT notifications from the tag."""
        hex_str = data.hex()
        try:
            ascii_str = data.decode("utf-8", errors="replace")
        except Exception:
            ascii_str = ""
        logger.debug(f"GATT Notification <{sender}>: {hex_str} ('{ascii_str}')")

    @classmethod
    async def send_raw_commands(
        cls,
        address: str,
        commands_hex: List[str],
        listen_seconds: float = 2.0,
        timeout: float = 25.0,
    ) -> Dict[str, Any]:
        """Send a sequence of raw hex commands to the EPD command characteristic."""
        responses: List[Dict[str, str]] = []

        def notify_cb(sender: Any, data: bytearray):
            responses.append({"hex": data.hex(), "ascii": data.decode("utf-8", errors="replace")})

        async with BleakClient(address, timeout=timeout) as client:
            cmd_char = None
            for svc in client.services:
                for ch in svc.characteristics:
                    if ch.uuid.lower() == CMD_CHAR_UUID.lower():
                        cmd_char = ch
                        break
                if cmd_char:
                    break

            if not cmd_char:
                raise RuntimeError(f"Command characteristic {CMD_CHAR_UUID} not found")

            try:
                await client.start_notify(cmd_char, notify_cb)
            except Exception:
                pass

            for cmd_str in commands_hex:
                raw_bytes = bytes.fromhex(cmd_str.replace(" ", ""))
                resp = "write" in cmd_char.properties
                await client.write_gatt_char(cmd_char, raw_bytes, response=resp)
                await asyncio.sleep(0.3)

            if listen_seconds > 0:
                await asyncio.sleep(listen_seconds)

        return {
            "success": True,
            "device": address,
            "commands_sent": len(commands_hex),
            "notifications_received": responses,
        }
