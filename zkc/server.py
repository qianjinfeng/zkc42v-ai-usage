"""FastAPI REST API Server for controlling Zkong Valley ZKC42V and ZKC21V ESL tags."""

from __future__ import annotations

import io
import uuid
import base64
import logging
from typing import Optional, Dict, Any
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from PIL import Image

from zkong.models import (
    MODEL_SPECS,
    DeviceModel,
    ImageUploadRequest,
    TextCardRequest,
    PriceTagRequest,
    ClockRequest,
    RawCommandRequest,
    ScanResponse,
    ActionResponse,
    get_model_spec,
    FitMode,
    DitherMode,
)
from zkong.renderer import (
    image_to_bwr_planes,
    bwr_planes_to_preview_image,
    render_text_card,
    render_price_tag,
    render_clock_face,
)
from zkong.ble import ZkongBLEController

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("zkong.server")

app = FastAPI(
    title="Zkong ESL E-Paper REST API",
    description="REST API for controlling Zkong Valley ZKC42V (4.2\"), ZKC21V (2.13\"), and other BLE e-paper tags directly without cloud dependencies.",
    version="1.0.0",
)

# Enable CORS for browser integration / frontend dashboards
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory background task status tracker
TASKS: Dict[str, Dict[str, Any]] = {}


@app.get("/", summary="API Information")
async def root():
    return {
        "name": "Zkong ESL BLE REST Server",
        "version": "1.0.0",
        "supported_devices": list(MODEL_SPECS.keys()),
        "docs_url": "/docs",
        "redoc_url": "/redoc",
    }


@app.get("/api/models", summary="List supported hardware models")
async def list_models() -> Dict[str, DeviceModel]:
    return MODEL_SPECS


@app.get("/api/scan", response_model=ScanResponse, summary="Scan for nearby BLE devices")
async def scan_devices(seconds: float = 8.0):
    try:
        res = await ZkongBLEController.scan_devices(duration_seconds=seconds)
        return res
    except Exception as e:
        logger.error(f"BLE Scan failed: {e}")
        raise HTTPException(status_code=500, detail=f"Scan error: {e}")


@app.get("/api/devices/{device_address}/inspect", summary="Inspect device GATT profile and services")
async def inspect_device(device_address: str, timeout: float = 25.0):
    try:
        profile = await ZkongBLEController.inspect_device(device_address, timeout=timeout)
        return profile
    except Exception as e:
        logger.error(f"Inspect failed for {device_address}: {e}")
        raise HTTPException(status_code=500, detail=f"Inspect error: {e}")


@app.get("/api/tasks/{task_id}", summary="Get background BLE task status")
async def get_task_status(task_id: str):
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    return TASKS[task_id]


async def _run_ble_send(task_id: str, device_address: str, black_plane: bytes, red_plane: bytes, init_param: int, slot: int):
    TASKS[task_id] = {"status": "in_progress", "device": device_address}
    try:
        res = await ZkongBLEController.send_planes(
            address=device_address,
            black_plane=black_plane,
            red_plane=red_plane,
            init_param=init_param,
            slot=slot,
        )
        TASKS[task_id] = {"status": "completed", "result": res}
    except Exception as e:
        logger.error(f"Background BLE task {task_id} failed: {e}")
        TASKS[task_id] = {"status": "failed", "error": str(e)}


# ----------------------------------------------------------------------
# Image Upload Endpoints
# ----------------------------------------------------------------------

@app.post("/api/devices/{device_address}/image", response_model=ActionResponse, summary="Upload image to device (JSON base64)")
async def upload_image_json(
    device_address: str,
    req: ImageUploadRequest,
    background_tasks: BackgroundTasks,
):
    if not req.image_base64:
        raise HTTPException(status_code=400, detail="Missing image_base64 in payload")

    try:
        # Strip data URL prefix if present
        b64_data = req.image_base64
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
        raw_bytes = base64.b64decode(b64_data)
        img = Image.open(io.BytesIO(raw_bytes))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {e}")

    spec = get_model_spec(req.model)
    init_p = req.init_param if req.init_param is not None else spec.init_param
    black, red = image_to_bwr_planes(
        img,
        width=spec.width,
        height=spec.height,
        fit=req.fit,
        dither=req.dither,
        red_threshold=req.red_threshold,
    )

    if req.async_mode:
        task_id = str(uuid.uuid4())
        background_tasks.add_task(_run_ble_send, task_id, device_address, black, red, init_p, spec.slot)
        return ActionResponse(
            success=True,
            message="Image upload started in background",
            device=device_address,
            model=spec.name,
            task_id=task_id,
        )

    try:
        res = await ZkongBLEController.send_planes(
            address=device_address,
            black_plane=black,
            red_plane=red,
            init_param=init_p,
            slot=spec.slot,
        )
        return ActionResponse(
            success=True,
            message="Image flashed successfully",
            device=device_address,
            model=spec.name,
            duration_seconds=res.get("duration_seconds"),
            details=res,
        )
    except Exception as e:
        logger.error(f"Failed to flash image to {device_address}: {e}")
        raise HTTPException(status_code=500, detail=f"BLE flash failed: {e}")


@app.post("/api/devices/{device_address}/upload-file", response_model=ActionResponse, summary="Upload image file (multipart/form-data)")
async def upload_image_file(
    device_address: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    model: str = Form("ZKC42V"),
    fit: FitMode = Form(FitMode.ASPECT_FIT),
    red_threshold: int = Form(120),
    init_param: Optional[int] = Form(None),
    async_mode: bool = Form(False),
):
    try:
        content = await file.read()
        img = Image.open(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid uploaded image file: {e}")

    spec = get_model_spec(model)
    init_p = init_param if init_param is not None else spec.init_param
    black, red = image_to_bwr_planes(
        img,
        width=spec.width,
        height=spec.height,
        fit=fit,
        red_threshold=red_threshold,
    )

    if async_mode:
        task_id = str(uuid.uuid4())
        background_tasks.add_task(_run_ble_send, task_id, device_address, black, red, init_p, spec.slot)
        return ActionResponse(
            success=True,
            message="Image upload started in background",
            device=device_address,
            model=spec.name,
            task_id=task_id,
        )

    try:
        res = await ZkongBLEController.send_planes(
            address=device_address,
            black_plane=black,
            red_plane=red,
            init_param=init_p,
            slot=spec.slot,
        )
        return ActionResponse(
            success=True,
            message="Image file flashed successfully",
            device=device_address,
            model=spec.name,
            duration_seconds=res.get("duration_seconds"),
            details=res,
        )
    except Exception as e:
        logger.error(f"Failed to flash image to {device_address}: {e}")
        raise HTTPException(status_code=500, detail=f"BLE flash failed: {e}")


# ----------------------------------------------------------------------
# Text & Template Rendering Endpoints
# ----------------------------------------------------------------------

@app.post("/api/devices/{device_address}/text", response_model=ActionResponse, summary="Render and display structured text card")
async def send_text_card(
    device_address: str,
    req: TextCardRequest,
    background_tasks: BackgroundTasks,
):
    spec = get_model_spec(req.model)
    img = render_text_card(
        title=req.title,
        subtitle=req.subtitle,
        body_lines=req.body_lines,
        footer=req.footer,
        badge=req.badge,
        badge_color=req.badge_color,
        model=spec.name,
        invert=req.invert,
    )
    init_p = req.init_param if req.init_param is not None else spec.init_param
    black, red = image_to_bwr_planes(img, width=spec.width, height=spec.height)

    if req.async_mode:
        task_id = str(uuid.uuid4())
        background_tasks.add_task(_run_ble_send, task_id, device_address, black, red, init_p, spec.slot)
        return ActionResponse(
            success=True,
            message="Text card render started in background",
            device=device_address,
            model=spec.name,
            task_id=task_id,
        )

    try:
        res = await ZkongBLEController.send_planes(
            address=device_address,
            black_plane=black,
            red_plane=red,
            init_param=init_p,
            slot=spec.slot,
        )
        return ActionResponse(
            success=True,
            message="Text card flashed successfully",
            device=device_address,
            model=spec.name,
            duration_seconds=res.get("duration_seconds"),
            details=res,
        )
    except Exception as e:
        logger.error(f"Failed to flash text card to {device_address}: {e}")
        raise HTTPException(status_code=500, detail=f"BLE flash failed: {e}")


@app.post("/api/devices/{device_address}/price-tag", response_model=ActionResponse, summary="Render and display retail price tag")
async def send_price_tag(
    device_address: str,
    req: PriceTagRequest,
    background_tasks: BackgroundTasks,
):
    spec = get_model_spec(req.model)
    img = render_price_tag(
        product_name=req.product_name,
        price=req.price,
        original_price=req.original_price,
        unit=req.unit,
        spec=req.spec,
        origin=req.origin,
        barcode=req.barcode,
        promo_badge=req.promo_badge,
        qr_data=req.qr_data,
        model=spec.name,
    )
    init_p = req.init_param if req.init_param is not None else spec.init_param
    black, red = image_to_bwr_planes(img, width=spec.width, height=spec.height)

    if req.async_mode:
        task_id = str(uuid.uuid4())
        background_tasks.add_task(_run_ble_send, task_id, device_address, black, red, init_p, spec.slot)
        return ActionResponse(
            success=True,
            message="Price tag render started in background",
            device=device_address,
            model=spec.name,
            task_id=task_id,
        )

    try:
        res = await ZkongBLEController.send_planes(
            address=device_address,
            black_plane=black,
            red_plane=red,
            init_param=init_p,
            slot=spec.slot,
        )
        return ActionResponse(
            success=True,
            message="Price tag flashed successfully",
            device=device_address,
            model=spec.name,
            duration_seconds=res.get("duration_seconds"),
            details=res,
        )
    except Exception as e:
        logger.error(f"Failed to flash price tag to {device_address}: {e}")
        raise HTTPException(status_code=500, detail=f"BLE flash failed: {e}")


@app.post("/api/devices/{device_address}/clock", response_model=ActionResponse, summary="Render and display digital clock screen")
async def send_clock(
    device_address: str,
    req: ClockRequest,
    background_tasks: BackgroundTasks,
):
    spec = get_model_spec(req.model)
    img = render_clock_face(
        title=req.title or "EPD CLOCK",
        show_seconds=req.show_seconds,
        show_date=req.show_date,
        invert=req.invert,
        model=spec.name,
    )
    init_p = req.init_param if req.init_param is not None else spec.init_param
    black, red = image_to_bwr_planes(img, width=spec.width, height=spec.height)

    if req.async_mode:
        task_id = str(uuid.uuid4())
        background_tasks.add_task(_run_ble_send, task_id, device_address, black, red, init_p, spec.slot)
        return ActionResponse(
            success=True,
            message="Clock render started in background",
            device=device_address,
            model=spec.name,
            task_id=task_id,
        )

    try:
        res = await ZkongBLEController.send_planes(
            address=device_address,
            black_plane=black,
            red_plane=red,
            init_param=init_p,
            slot=spec.slot,
        )
        return ActionResponse(
            success=True,
            message="Clock face flashed successfully",
            device=device_address,
            model=spec.name,
            duration_seconds=res.get("duration_seconds"),
            details=res,
        )
    except Exception as e:
        logger.error(f"Failed to flash clock to {device_address}: {e}")
        raise HTTPException(status_code=500, detail=f"BLE flash failed: {e}")


@app.post("/api/devices/{device_address}/command", summary="Send raw hex commands to EPD command characteristic")
async def send_raw_command(device_address: str, req: RawCommandRequest):
    try:
        res = await ZkongBLEController.send_raw_commands(
            address=device_address,
            commands_hex=req.commands,
            listen_seconds=req.listen_seconds,
        )
        return res
    except Exception as e:
        logger.error(f"Failed to send raw commands to {device_address}: {e}")
        raise HTTPException(status_code=500, detail=f"Raw command error: {e}")


# ----------------------------------------------------------------------
# Image Preview Endpoints (Returns PNG directly)
# ----------------------------------------------------------------------

@app.post("/api/preview/text", summary="Preview rendered text card (returns PNG)")
async def preview_text_card(req: TextCardRequest):
    spec = get_model_spec(req.model)
    img = render_text_card(
        title=req.title,
        subtitle=req.subtitle,
        body_lines=req.body_lines,
        footer=req.footer,
        badge=req.badge,
        badge_color=req.badge_color,
        model=spec.name,
        invert=req.invert,
    )
    # Simulate BWR quantization
    black, red = image_to_bwr_planes(img, width=spec.width, height=spec.height)
    preview = bwr_planes_to_preview_image(black, red, width=spec.width, height=spec.height)

    buf = io.BytesIO()
    preview.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/api/preview/price-tag", summary="Preview rendered price tag (returns PNG)")
async def preview_price_tag(req: PriceTagRequest):
    spec = get_model_spec(req.model)
    img = render_price_tag(
        product_name=req.product_name,
        price=req.price,
        original_price=req.original_price,
        unit=req.unit,
        spec=req.spec,
        origin=req.origin,
        barcode=req.barcode,
        promo_badge=req.promo_badge,
        qr_data=req.qr_data,
        model=spec.name,
    )
    black, red = image_to_bwr_planes(img, width=spec.width, height=spec.height)
    preview = bwr_planes_to_preview_image(black, red, width=spec.width, height=spec.height)

    buf = io.BytesIO()
    preview.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/api/preview/clock", summary="Preview rendered clock face (returns PNG)")
async def preview_clock(req: ClockRequest):
    spec = get_model_spec(req.model)
    img = render_clock_face(
        title=req.title or "EPD CLOCK",
        show_seconds=req.show_seconds,
        show_date=req.show_date,
        invert=req.invert,
        model=spec.name,
    )
    black, red = image_to_bwr_planes(img, width=spec.width, height=spec.height)
    preview = bwr_planes_to_preview_image(black, red, width=spec.width, height=spec.height)

    buf = io.BytesIO()
    preview.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/api/preview/image", summary="Preview quantized BWR planes for an uploaded image (returns PNG)")
async def preview_image(
    file: UploadFile = File(...),
    model: str = Form("ZKC42V"),
    fit: FitMode = Form(FitMode.ASPECT_FIT),
    red_threshold: int = Form(120),
):
    try:
        content = await file.read()
        img = Image.open(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image file: {e}")

    spec = get_model_spec(model)
    black, red = image_to_bwr_planes(
        img,
        width=spec.width,
        height=spec.height,
        fit=fit,
        red_threshold=red_threshold,
    )
    preview = bwr_planes_to_preview_image(black, red, width=spec.width, height=spec.height)

    buf = io.BytesIO()
    preview.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


if __name__ == "__main__":
    import uvicorn
    print("Starting Zkong ESL REST Server on http://0.0.0.0:8000 ...")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
