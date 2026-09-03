# ZKong ESL BLE Controller & REST API Service

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com)
[![Bleak](https://img.shields.io/badge/Bleak-BLE-blueviolet.svg)](https://github.com/hbldh/bleak)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](../LICENSE)

A cross-platform (Windows / macOS / Linux) Python driver, rendering pipeline, and REST API service for directly driving **Zkong Valley (中科微光/智控)** Bluetooth Electronic Shelf Labels (ESL) without cloud dependencies or base stations.

Supports **ZKC42V (4.2" 400x300)**, **ZKC21V (2.13" 250x122)**, and **ZKC29V (2.9" 296x128)** BWR (Black / White / Red) three-color e-paper tags.

---

## Features

- 🔗 **Direct BLE Communication**: Direct GATT connection to ESL tags using `bleak` without official cloud or AP base station.
- 🖼 **BWR Color Rendering**: Automatic 3-color quantization (Black/White/Red) with thresholding and aspect-fit / stretch / crop scaling.
- 🎨 **Built-in Templates**:
  - **Retail Price Tags**: Product name, prominent promotion price, original price, unit, origin, spec, and barcode.
  - **Information / Text Cards**: Structured cards with titles, badges, formatted body items, and footers.
  - **Digital Clock**: High-contrast digital clock face with date and status line.
  - **Custom Images**: Upload PNG / JPG / BMP via multipart file upload or Base64 JSON.
- 🚀 **RESTful API**: Powered by FastAPI with automated Swagger / OpenAPI documentation.
- 👁 **Live Preview Endpoints**: Render and preview generated screens as PNG without needing physical hardware connected.
- ⚡ **Sync & Async Modes**: Support for synchronous blocking writes or background task queueing.

---

## Supported Hardware Specifications

| Model | Screen Size | Resolution | Colors | SoC / Controller | EPD Init Param |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **ZKC42V** | 4.2 inch | **400 × 300** | Black / White / Red | GigaDevice GR5513 / SSD1619 | `0x02` |
| **ZKC21V** | 2.13 inch | **250 × 122** | Black / White / Red | GR5513 / nRF52810 / SSD1680 | `0x01` |
| **ZKC29V** | 2.9 inch | **296 × 128** | Black / White / Red | GR5513 / SSD1680 | `0x01` |

---

## Protocol Overview

The tag acts as a BLE GATT Server advertising custom service `62750001`:

- **Service UUID**: `62750001-D828-918D-FB46-B6C11C675AEC`
- **Command Characteristic**: `62750002-D828-918D-FB46-B6C11C675AEC` (Write + Notify)
- **Firmware Version**: `62750003-D828-918D-FB46-B6C11C675AEC` (Read: `v1.10-gr5513`)

### Transmission Sequence

1. `INIT (0x01, model)`: Initialize screen controller IC (`0x02` for 4.2", `0x01` for 2.13").
2. `SET_SLOT (0x31, 0x00, slot)`: Select memory display slot (Slot 0).
3. `WRITE_IMG (0x30, flag, RLE_data)`: Stream RLE-compressed black and red planes in chunks $\le 233$ bytes.
4. `REFRESH (0x05)`: Trigger full electronic paper physical display refresh.

---

## Directory Structure

```text
zkc/
├── README.md             # Project documentation
├── requirements.txt      # Python dependencies
├── server.py             # FastAPI REST application entry point
└── zkong/                # Core driver package
    ├── __init__.py       # Package exports
    ├── ble.py            # Asynchronous Bleak BLE controller
    ├── models.py         # Pydantic schemas & hardware specs
    ├── protocol.py       # EPD-nRF5 packet framing & RLE compressor
    └── renderer.py       # Pillow image generation & BWR quantization
```

---

## Installation

### 1. Clone & Navigate
```bash
git clone https://github.com/qianjinfeng/zkc42v-ai-usage.git
cd zkc42v-ai-usage/zkc
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

---

## Quick Start

### Start the REST API Server

```bash
python server.py
```
Or with Uvicorn:
```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Once started, open your browser:
- **Interactive Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Alternative ReDoc UI**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## REST API Reference

### 1. Device Discovery & Inspection

#### Scan for BLE Devices
```http
GET /api/scan?seconds=8.0
```

#### Inspect Device GATT Services
```http
GET /api/devices/{device_address}/inspect
```

#### List Supported Hardware Models
```http
GET /api/models
```

---

### 2. Sending Content to Tags

#### Upload Image (File)
```http
POST /api/devices/{device_address}/upload-file
Content-Type: multipart/form-data
```
**Form Parameters:**
- `file`: Image file (PNG/JPG/BMP)
- `model`: `ZKC42V` or `ZKC21V` (default: `ZKC42V`)
- `fit`: `aspect_fit` | `stretch` | `crop`

#### Upload Image (Base64 JSON)
```http
POST /api/devices/{device_address}/image
Content-Type: application/json
```
```json
{
  "image_base64": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...",
  "model": "ZKC42V",
  "fit": "aspect_fit",
  "async_mode": false
}
```

#### Push Retail Price Tag
```http
POST /api/devices/{device_address}/price-tag
Content-Type: application/json
```
```json
{
  "product_name": "Premium Organic Milk",
  "price": "19.90",
  "original_price": "29.90",
  "unit": "box",
  "spec": "250ml*12",
  "origin": "New Zealand",
  "barcode": "6901234567890",
  "promo_badge": "HOT",
  "model": "ZKC42V"
}
```

#### Push Text Card
```http
POST /api/devices/{device_address}/text
Content-Type: application/json
```
```json
{
  "title": "Server Status",
  "subtitle": "Node cluster-us-east-1",
  "body_lines": [
    "CPU Usage: 42%",
    "Memory: 16.4 / 32 GB",
    "[R]Alert: Disk space at 89%"
  ],
  "badge": "ONLINE",
  "badge_color": "red",
  "footer": "Updated at 14:30:00",
  "model": "ZKC42V",
  "invert": false
}
```
*(Lines prefixed with `[R]` are highlighted in red).*

#### Push Clock Face
```http
POST /api/devices/{device_address}/clock
Content-Type: application/json
```
```json
{
  "title": "EPD CLOCK",
  "show_seconds": false,
  "show_date": true,
  "invert": true,
  "model": "ZKC42V"
}
```

---

### 3. Live Image Previews (No Device Required)

Preview rendered output directly in browser or tools without sending over BLE:

| Endpoint | Method | Output | Description |
| :--- | :--- | :--- | :--- |
| `/api/preview/text` | POST | `image/png` | Preview text card layout |
| `/api/preview/price-tag` | POST | `image/png` | Preview retail price tag |
| `/api/preview/clock` | POST | `image/png` | Preview clock screen |
| `/api/preview/image` | POST | `image/png` | Preview 3-color quantized image |

---

## Python SDK Example

You can also use the `zkong` package directly in your Python code:

```python
import asyncio
from PIL import Image
from zkong import (
    ZkongBLEController,
    image_to_bwr_planes,
    render_price_tag,
    get_model_spec,
)

async def main():
    device_mac = "XX:XX:XX:XX:XX:XX"  # Replace with tag MAC or UUID
    model = get_model_spec("ZKC42V")

    # 1. Render a price tag image
    img = render_price_tag(
        product_name="Fuji Apple",
        price="8.80",
        original_price="12.00",
        unit="kg",
        promo_badge="SALE",
        model="ZKC42V",
    )

    # 2. Convert to BWR binary planes
    black, red = image_to_bwr_planes(img, width=model.width, height=model.height)

    # 3. Flash to tag over BLE
    print(f"Connecting to {device_mac}...")
    result = await ZkongBLEController.send_planes(
        address=device_mac,
        black_plane=black,
        red_plane=red,
        init_param=model.init_param,
    )
    print("Success:", result)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Running Tests

Execute the automated test suite covering protocol encoding, pixel quantization, templates, and API endpoints:

```bash
python tests/test_zkong_server.py
```

---

## License

[MIT License](../LICENSE)
