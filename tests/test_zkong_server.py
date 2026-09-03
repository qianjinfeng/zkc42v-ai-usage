"""Unit tests for Zkong ESL protocol, rendering pipeline, and REST API."""

import io
import sys
from pathlib import Path

# Add project root and zkc folder to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "zkc"))

import unittest
from PIL import Image
from starlette.testclient import TestClient

from zkong.models import MODEL_SPECS, get_model_spec, SupportedModel
from zkong.protocol import (
    rle_compress,
    rle_chunks,
    build_frame,
    parse_frame,
    build_send_steps,
)
from zkong.renderer import (
    classify_pixel_bwr,
    image_to_bwr_planes,
    render_text_card,
    render_price_tag,
    render_clock_face,
    bwr_planes_to_preview_image,
)
from server import app


class TestZkongProtocol(unittest.TestCase):
    def test_rle_compress_literals_and_runs(self):
        # 4 identical bytes -> run_len=4 -> control = 0x80 | 1 = 0x81
        data = b"\xaa\xaa\xaa\xaa"
        comp = rle_compress(data)
        self.assertEqual(comp, bytes([0x81, 0xaa]))

        # Literals
        data2 = b"\x01\x02\x03\x04"
        comp2 = rle_compress(data2)
        self.assertEqual(comp2, bytes([0x03, 0x01, 0x02, 0x03, 0x04]))

    def test_rle_chunks_boundaries(self):
        plane = bytearray(b"\xff" * 15000)
        plane_rle = rle_compress(plane)
        chunks = rle_chunks(plane_rle, plane_flag=0, chunk_size=233)
        self.assertTrue(len(chunks) > 0)
        # First chunk flag should have 0x02 set
        self.assertEqual(chunks[0][0], 0x30)
        self.assertEqual(chunks[0][1] & 0x02, 0x02)
        # Subsequent chunks should not have 0x02 set
        if len(chunks) > 1:
            self.assertEqual(chunks[1][1] & 0x02, 0x00)

    def test_frame_build_and_parse(self):
        w, h = 400, 300
        plane_size = (w * h) // 8
        black = b"\xaa" * plane_size
        red = b"\x55" * plane_size
        frame = build_frame(w, h, black, red)
        
        parsed_w, parsed_h, parsed_black, parsed_red = parse_frame(frame)
        self.assertEqual(parsed_w, w)
        self.assertEqual(parsed_h, h)
        self.assertEqual(parsed_black, black)
        self.assertEqual(parsed_red, red)

    def test_build_send_steps(self):
        w, h = 250, 122
        plane_size = (w * h) // 8
        black = b"\xff" * plane_size
        red = b"\xff" * plane_size
        steps = build_send_steps(black, red, init_param=0x01, slot=0)
        
        self.assertTrue(len(steps) >= 4)
        # Step 0 is INIT
        self.assertEqual(steps[0][0], bytes([0x01, 0x01]))
        # Step 1 is SET_SLOT
        self.assertEqual(steps[1][0], bytes([0x31, 0x00, 0x00]))
        # Last step is REFRESH
        self.assertEqual(steps[-1][0], bytes([0x05]))


class TestZkongRenderer(unittest.TestCase):
    def test_pixel_classification(self):
        self.assertEqual(classify_pixel_bwr((255, 0, 0)), "R")
        self.assertEqual(classify_pixel_bwr((0, 0, 0)), "K")
        self.assertEqual(classify_pixel_bwr((255, 255, 255)), "W")

    def test_image_to_bwr_planes(self):
        im = Image.new("RGB", (400, 300), (255, 255, 255))
        black, red = image_to_bwr_planes(im, 400, 300)
        expected_len = (400 * 300) // 8
        self.assertEqual(len(black), expected_len)
        self.assertEqual(len(red), expected_len)
        # All white image means all bits 1 (0xff)
        self.assertEqual(black, b"\xff" * expected_len)
        self.assertEqual(red, b"\xff" * expected_len)

    def test_render_zkc42v_text_card(self):
        img = render_text_card(
            title="4.2寸测试标题",
            subtitle="Subtitle Info",
            body_lines=["Line 1", "[R]Red Line 2"],
            badge="LIVE",
            model="ZKC42V",
        )
        self.assertEqual(img.size, (400, 300))

    def test_render_zkc21v_price_tag(self):
        img = render_price_tag(
            product_name="进口牛奶",
            price="12.90",
            original_price="19.90",
            unit="盒",
            spec="250ml",
            origin="新西兰",
            barcode="690123456789",
            promo_badge="特惠",
            model="ZKC21V",
        )
        self.assertEqual(img.size, (250, 122))

    def test_render_clock_face(self):
        img = render_clock_face(model="ZKC42V", invert=True)
        self.assertEqual(img.size, (400, 300))
        img21 = render_clock_face(model="ZKC21V", invert=False)
        self.assertEqual(img21.size, (250, 122))


class TestRestAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_root_endpoint(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("supported_devices", data)
        self.assertIn("ZKC42V", data["supported_devices"])
        self.assertIn("ZKC21V", data["supported_devices"])

    def test_list_models_endpoint(self):
        res = self.client.get("/api/models")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("ZKC42V", data)
        self.assertEqual(data["ZKC42V"]["width"], 400)
        self.assertEqual(data["ZKC42V"]["height"], 300)
        self.assertIn("ZKC21V", data)
        self.assertEqual(data["ZKC21V"]["width"], 250)
        self.assertEqual(data["ZKC21V"]["height"], 122)

    def test_preview_text_endpoint(self):
        payload = {
            "title": "API Test Title",
            "subtitle": "Subtitle",
            "body_lines": ["Item A", "Item B"],
            "model": "ZKC42V",
        }
        res = self.client.post("/api/preview/text", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "image/png")
        img = Image.open(io.BytesIO(res.content))
        self.assertEqual(img.size, (400, 300))

    def test_preview_price_tag_endpoint(self):
        payload = {
            "product_name": "有机苹果",
            "price": "9.90",
            "original_price": "15.00",
            "unit": "kg",
            "model": "ZKC21V",
        }
        res = self.client.post("/api/preview/price-tag", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "image/png")
        img = Image.open(io.BytesIO(res.content))
        self.assertEqual(img.size, (250, 122))

    def test_preview_clock_endpoint(self):
        payload = {
            "model": "ZKC42V",
            "invert": True,
        }
        res = self.client.post("/api/preview/clock", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "image/png")


if __name__ == "__main__":
    unittest.main()
