"""Data models and hardware specifications for Zkong ESL devices."""

from __future__ import annotations

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class SupportedModel(str, Enum):
    ZKC42V = "ZKC42V"
    ZKC21V = "ZKC21V"
    ZKC29V = "ZKC29V"
    CUSTOM = "CUSTOM"


class DeviceModel(BaseModel):
    name: str
    description: str
    width: int
    height: int
    init_param: int = Field(default=0x02, description="Model initialization parameter (e.g. 0x02 for ZKC42V SSD1619)")
    colors: List[str] = Field(default=["black", "white", "red"], description="Supported color palette")
    slot: int = Field(default=0, description="Default image slot")


MODEL_SPECS: Dict[str, DeviceModel] = {
    SupportedModel.ZKC42V.value: DeviceModel(
        name="ZKC42V",
        description="Zkong 4.2-inch Three-color E-Paper Tag (400x300 BWR)",
        width=400,
        height=300,
        init_param=0x02,
        colors=["black", "white", "red"],
    ),
    SupportedModel.ZKC21V.value: DeviceModel(
        name="ZKC21V",
        description="Zkong 2.13-inch Three-color E-Paper Tag (250x122 BWR)",
        width=250,
        height=122,
        init_param=0x01,
        colors=["black", "white", "red"],
    ),
    SupportedModel.ZKC29V.value: DeviceModel(
        name="ZKC29V",
        description="Zkong 2.9-inch Three-color E-Paper Tag (296x128 BWR)",
        width=296,
        height=128,
        init_param=0x01,
        colors=["black", "white", "red"],
    ),
}


def get_model_spec(model_name: Optional[str] = None, width: Optional[int] = None, height: Optional[int] = None) -> DeviceModel:
    """Resolve device model specs by name or dimensions."""
    if model_name and model_name.upper() in MODEL_SPECS:
        spec = MODEL_SPECS[model_name.upper()]
        if width and height:
            return DeviceModel(
                name=spec.name,
                description=spec.description,
                width=width,
                height=height,
                init_param=spec.init_param,
                colors=spec.colors,
            )
        return spec
    
    if width and height:
        # Match by resolution
        for spec in MODEL_SPECS.values():
            if spec.width == width and spec.height == height:
                return spec
        return DeviceModel(
            name="CUSTOM",
            description=f"Custom E-Paper Display ({width}x{height})",
            width=width,
            height=height,
            init_param=0x02,
            colors=["black", "white", "red"],
        )

    # Default to ZKC42V
    return MODEL_SPECS[SupportedModel.ZKC42V.value]


class FitMode(str, Enum):
    ASPECT_FIT = "aspect_fit"
    STRETCH = "stretch"
    CROP = "crop"


class DitherMode(str, Enum):
    THRESHOLD = "threshold"
    FLOYD_STEINBERG = "floyd_steinberg"


class TextItem(BaseModel):
    text: str
    size: int = 20
    color: str = "black"  # "black", "red", "white"
    bold: bool = False
    align: str = "left"  # "left", "center", "right"
    x: Optional[int] = None
    y: Optional[int] = None


class ImageUploadRequest(BaseModel):
    image_base64: Optional[str] = Field(None, description="Base64-encoded image data")
    model: str = Field(default="ZKC42V", description="Device model (ZKC42V, ZKC21V, etc.)")
    fit: FitMode = Field(default=FitMode.ASPECT_FIT, description="Image scaling mode")
    dither: DitherMode = Field(default=DitherMode.THRESHOLD, description="Quantization method")
    red_threshold: int = Field(default=120, description="Red channel threshold for BWR classification")
    init_param: Optional[int] = Field(None, description="Override EPD init param byte")
    async_mode: bool = Field(default=False, description="Whether to execute BLE transmission in background")


class TextCardRequest(BaseModel):
    title: str = Field(..., description="Card title text")
    subtitle: Optional[str] = Field(None, description="Subtitle text")
    body_lines: List[str] = Field(default_factory=list, description="List of body text lines")
    footer: Optional[str] = Field(None, description="Footer / note text")
    badge: Optional[str] = Field(None, description="Top right badge / status text")
    badge_color: str = Field(default="red", description="Badge background or text color")
    model: str = Field(default="ZKC42V", description="Device model (ZKC42V, ZKC21V, etc.)")
    invert: bool = Field(default=False, description="Invert black/white backgrounds")
    init_param: Optional[int] = Field(None, description="Override EPD init param byte")
    async_mode: bool = Field(default=False, description="Whether to execute BLE transmission in background")


class PriceTagRequest(BaseModel):
    product_name: str = Field(..., description="Product title / name")
    price: str = Field(..., description="Current selling price (e.g. '19.90' or '￥19.90')")
    original_price: Optional[str] = Field(None, description="Original / crossed-out price (e.g. '29.90')")
    unit: Optional[str] = Field("件", description="Unit of measure (e.g. '件', 'kg', '瓶')")
    spec: Optional[str] = Field(None, description="Specification (e.g. '500ml*12', '盒')")
    origin: Optional[str] = Field(None, description="Country/place of origin (e.g. '产地: 山东')")
    barcode: Optional[str] = Field(None, description="Barcode number text")
    promo_badge: Optional[str] = Field(None, description="Promo tag (e.g. '特惠', '热销', '新品')")
    qr_data: Optional[str] = Field(None, description="Optional text/URL to render as QR code")
    model: str = Field(default="ZKC42V", description="Device model (ZKC42V, ZKC21V, etc.)")
    init_param: Optional[int] = Field(None, description="Override EPD init param byte")
    async_mode: bool = Field(default=False, description="Whether to execute BLE transmission in background")


class ClockRequest(BaseModel):
    model: str = Field(default="ZKC42V", description="Device model (ZKC42V, ZKC21V, etc.)")
    title: Optional[str] = Field("EPD CLOCK", description="Clock title text")
    show_seconds: bool = Field(default=False, description="Whether to show seconds")
    show_date: bool = Field(default=True, description="Whether to show current date")
    invert: bool = Field(default=True, description="True for dark background, False for white")
    init_param: Optional[int] = Field(None, description="Override EPD init param byte")
    async_mode: bool = Field(default=False, description="Whether to execute BLE transmission in background")


class RawCommandRequest(BaseModel):
    commands: List[str] = Field(..., description="List of hex strings to send to 62750002 char (e.g. ['0102', '310000', '05'])")
    listen_seconds: float = Field(default=2.0, description="Seconds to listen for notifications after writes")


class BLEDeviceItem(BaseModel):
    address: str
    name: Optional[str] = None
    rssi: Optional[int] = None
    is_zkong: bool = False
    service_uuids: List[str] = Field(default_factory=list)
    manufacturer_data_hex: Optional[str] = None


class ScanResponse(BaseModel):
    success: bool
    count: int
    devices: List[BLEDeviceItem]


class ActionResponse(BaseModel):
    success: bool
    message: str
    device: Optional[str] = None
    model: Optional[str] = None
    task_id: Optional[str] = None
    duration_seconds: Optional[float] = None
    details: Optional[Dict[str, Any]] = None
