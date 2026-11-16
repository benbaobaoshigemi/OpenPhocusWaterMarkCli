"""Comprehensive verification suite for the 1:1 Phocus Mobile 1.2.0 Watermark Engine."""

from __future__ import annotations

import io
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from phocus_watermark import (
    STYLE_ORDER,
    STYLE_SPECS,
    XCD_LENS_MAP,
    PhocusWatermarkEngine,
    PhotoMetadata,
    SignatureConfig,
    extract_metadata_from_image,
    format_lens_info,
    format_metadata,
)
from phocus_watermark.models import format_timestamp_ms
from phocus_watermark.renderer import ASSETS_DIR, round_to_int
from phocus_watermark.vector_rasterizer import rasterize_svg_signature, rasterize_vector_drawable

ENGINE_ROOT = Path(__file__).resolve().parent.parent

# Horizontal stroke SVG with distinct left-to-right asymmetry for orientation checks
SAMPLE_SIG_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80">
  <rect x="10" y="30" width="160" height="20" fill="#112233"/>
  <circle cx="170" cy="40" r="15" fill="#FF3300"/>
</svg>"""


def make_test_photo(w: int, h: int, fill=(64, 120, 180)) -> Image.Image:
    """Create a synthetic RGB photo with deterministic border-free solid color."""
    return Image.new("RGB", (w, h), fill)


class TestModelsAndExif(unittest.TestCase):
    def test_style_registry_completeness(self):
        expected_ids = (0, 8, 9, 14, 13, 15, 12, 6, 7, 1, 2, 3, 4)
        self.assertEqual(STYLE_ORDER, expected_ids)
        self.assertEqual(set(STYLE_SPECS.keys()), set(expected_ids))
        for sid in (5, 10, 11):
            self.assertNotIn(sid, STYLE_SPECS)

    def test_xcd_lens_normalization_and_metadata(self):
        self.assertEqual(len(XCD_LENS_MAP), 22)
        self.assertEqual(format_lens_info("XCD 90V@extra"), "XCD 2,5 / 90V")
        self.assertEqual(format_lens_info("XCD 135"), "XCD 2,8 / 135")
        self.assertEqual(format_lens_info("XCD Unknown"), "Third Party Lens")
        self.assertEqual(format_lens_info("unknown"), "Third Party Lens")
        self.assertEqual(format_lens_info(""), "Third Party Lens")
        self.assertEqual(format_lens_info("Summilux-M 50"), "Summilux-M 50")

        meta = format_metadata(
            aperture=2.54,
            shutter="1/1600",
            iso=64,
            lens="XCD 38V",
            body_name="X2D II 100C",
            create_date_ms=1771372800000,  # 2026-02-18T00:00:00Z
            offset_time="+08:00",
        )
        self.assertEqual(meta.aperture, "f/2.5")
        self.assertEqual(meta.shutter, "1/1600s")
        self.assertEqual(meta.iso, "64")
        self.assertEqual(meta.lens_info_text, "XCD 2,5 / 38V")
        self.assertEqual(meta.full_body_name, "Hasselblad X2D II 100C")
        self.assertEqual(meta.create_time, "2026 02 18")

        self.assertEqual(format_timestamp_ms(None, "+08:00"), "")
        self.assertEqual(format_timestamp_ms(0, "+08:00"), "")
        self.assertEqual(format_timestamp_ms(1771372800000, "bad"), "")

    def test_extract_metadata_from_jpeg_exif(self):
        img = make_test_photo(400, 300)
        exif = Image.Exif()
        exif[0x0110] = "X2D 100C"  # Model
        exif_ifd = {
            0x829A: (1, 1000),        # ExposureTime -> 1/1000
            0x829D: (28, 10),         # FNumber -> 2.8
            0x8827: 64,               # ISOSpeedRatings
            0xA434: "XCD 55V",        # LensModel
            0x9003: "2026:02:18 14:22:05",
        }
        exif[0x8769] = exif_ifd
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        buf.seek(0)
        reloaded = Image.open(buf)
        meta = extract_metadata_from_image(reloaded)
        self.assertEqual(meta.full_body_name, "Hasselblad X2D 100C")
        self.assertEqual(meta.lens_info_text, "XCD 2,5 / 55V")
        self.assertEqual(meta.aperture, "f/2.8")
        self.assertEqual(meta.shutter, "1/1000s")
        self.assertEqual(meta.iso, "64")
        self.assertEqual(meta.create_time, "2026 02 18")


class TestGeometryAndVectorRasterizer(unittest.TestCase):
    def setUp(self):
        self.engine = PhocusWatermarkEngine()

    def test_scale_and_padding_truncation_all_styles(self):
        cases = [
            (1200, 900, True),    # 4:3 Landscape
            (900, 1200, False),   # 3:4 Portrait
            (900, 900, False),    # 1:1 Square -> portrait branch
            (1300, 480, True),    # XPan wide landscape
        ]
        for w, h, is_land in cases:
            for sid, spec in STYLE_SPECS.items():
                l_dp, t_dp, r_dp, b_dp = spec.padding_landscape if is_land else spec.padding_portrait
                ref = (878.0 if is_land else 462.0) - l_dp - r_dp
                expected_scale = float(w) / ref
                self.assertAlmostEqual(self.engine.compute_scale(spec, w, h), expected_scale, places=9)
                pad = self.engine.compute_padding(spec, w, h)
                self.assertEqual(
                    pad,
                    (int(l_dp * expected_scale), int(t_dp * expected_scale), int(r_dp * expected_scale), int(b_dp * expected_scale)),
                )
                out = self.engine.render(
                    make_test_photo(w, h),
                    sid,
                    metadata=PhotoMetadata("f/2.5", "1/1000", "64", "XCD 2,5/90V", "X2D 100C", "2026 02 18"),
                    seed=7,
                )
                self.assertEqual(out.size, (w + pad[0] + pad[2], h + pad[1] + pad[3]))

    def test_vector_drawables_and_counter_holes(self):
        wordmark = rasterize_vector_drawable(str(ASSETS_DIR / "vectors" / "hasselblad_logo.xml"), 546, 54)
        self.assertEqual(wordmark.size, (546, 54))
        # Center bounding box should contain opaque black ink and transparent counter/gap pixels
        alphas = [wordmark.getpixel((x, 27))[3] for x in range(wordmark.width)]
        self.assertGreater(max(alphas), 240)
        self.assertEqual(min(alphas), 0)
        transitions = sum(1 for a, b in zip(alphas, alphas[1:]) if (a > 128) != (b > 128))
        # 10 letters ("HASSELBLAD") + enclosed counters -> multiple horizontal ink transitions at midline
        self.assertGreaterEqual(transitions, 18)

        orange = rasterize_vector_drawable(
            str(ASSETS_DIR / "vectors" / "ic_hasslblad_logo_orange_vector.xml"), 364, 36
        )
        opaque_rgb = [
            orange.getpixel((x, y))[:3]
            for y in range(orange.height)
            for x in range(orange.width)
            if orange.getpixel((x, y))[3] > 230
        ]
        self.assertTrue(len(opaque_rgb) > 100)
        # Warm orange copper tone (R > G > B)
        avg_r = sum(c[0] for c in opaque_rgb) / len(opaque_rgb)
        avg_g = sum(c[1] for c in opaque_rgb) / len(opaque_rgb)
        avg_b = sum(c[2] for c in opaque_rgb) / len(opaque_rgb)
        self.assertGreater(avg_r, avg_g)
        self.assertGreater(avg_g, avg_b)

    def test_svg_signature_rotation_and_gravity(self):
        # rotation=0 swaps target aspect (D.e) and does not rotate; rotation=90 rotates -90 (PIL +90)
        r0 = rasterize_svg_signature(SAMPLE_SIG_SVG, 80, 176, rotation_deg=0.0, pen_color_argb=-1)
        r90 = rasterize_svg_signature(SAMPLE_SIG_SVG, 176, 80, rotation_deg=90.0, pen_color_argb=None)
        self.assertEqual(r0.size, (80, 176))
        self.assertEqual(r90.size, (176, 80))

        # Check tint override
        tinted = rasterize_svg_signature(SAMPLE_SIG_SVG, 160, 80, rotation_deg=0.0, pen_color_argb=0xFF22AA44)
        opaque = [
            tinted.getpixel((x, y))[:3]
            for y in range(tinted.height)
            for x in range(tinted.width)
            if tinted.getpixel((x, y))[3] > 240
        ]
        self.assertTrue(all(rgb == (0x22, 0xAA, 0x44) for rgb in opaque))

        spec1 = STYLE_SPECS[1]
        spec6 = STYLE_SPECS[6]
        w, h = 1200, 900
        pad_l, pad_t, pad_r, pad_b = self.engine.compute_padding(spec1, w, h)
        cw, ch = w + pad_l + pad_r, h + pad_t + pad_b

        rect_r = self.engine.compute_signature_rect(spec1, SignatureConfig(True, 5, -1, 0.0), w, h, cw, ch)
        rect_e = self.engine.compute_signature_rect(spec1, SignatureConfig(True, 8388613, -1, 0.0), w, h, cw, ch)
        rect_l = self.engine.compute_signature_rect(spec1, SignatureConfig(True, 3, -1, 0.0), w, h, cw, ch)
        rect_c = self.engine.compute_signature_rect(spec1, SignatureConfig(True, 17, -1, 0.0), w, h, cw, ch)

        self.assertLess(rect_l[0], rect_c[0])
        self.assertLess(rect_c[0], rect_r[0])
        # gravity=5 inset 26*s vs gravity=END inset 12*s in landscape
        self.assertGreater(rect_e[0], rect_r[0])
        self.assertEqual(rect_r[2] - rect_r[0], int(80 * self.engine.compute_scale(spec1, w, h)))
        self.assertEqual(rect_r[3] - rect_r[1], int(176 * self.engine.compute_scale(spec1, w, h)))

        # Style 6 signature_top_anchored=True -> anchored near top (14*s)
        rect6 = self.engine.compute_signature_rect(spec6, SignatureConfig(True, 5, -1, 90.0), w, h, w, h)
        self.assertEqual(rect6[1], int(14 * self.engine.compute_scale(spec6, w, h)))


class TestFullRenderingAndGainmap(unittest.TestCase):
    def setUp(self):
        self.engine = PhocusWatermarkEngine()
        self.meta = PhotoMetadata(
            aperture="f/2.5",
            shutter="1/1600",
            iso="64",
            lens_info_text="XCD 2,5/90V",
            full_body_name="X2D II 100C",
            create_time="2026 02 18",
        )

    def test_invalid_style_raises(self):
        with self.assertRaises(ValueError):
            self.engine.render(make_test_photo(400, 300), style_id=5)

    def test_bordered_styles_colors_and_photo_preservation(self):
        photo = make_test_photo(846, 600, fill=(30, 90, 150))  # 846 = 878 - 32 -> exact scale=1.0 for style 1/2/7/9/12
        for sid, expected_border in ((1, (255, 255, 255)), (2, (255, 255, 255)), (12, (0x1D, 0x1D, 0x1D)), (7, (255, 255, 255))):
            spec = STYLE_SPECS[sid]
            pad_l, pad_t, pad_r, pad_b = self.engine.compute_padding(spec, 846, 600)
            out = self.engine.render(photo, sid, metadata=self.meta)
            # Corner border pixel matches MaskView color
            self.assertEqual(out.getpixel((2, 2)), expected_border)
            # Photo interior preserved verbatim (center pixel untouched)
            self.assertEqual(out.getpixel((pad_l + 423, pad_t + 300)), (30, 90, 150))
            # Bottom border has ink pixels distinct from border fill
            has_ink = any(
                out.getpixel((x, y)) != expected_border
                for y in range(pad_t + 600, out.height)
                for x in range(0, out.width, 4)
            )
            self.assertTrue(has_ink, f"Expected bottom ink on style {sid}")

    def test_style15_film_frame_deterministic_sprockets_and_preview_density(self):
        photo_l = make_test_photo(878, 600, fill=(40, 80, 120))
        out1 = self.engine.render(photo_l, 15, metadata=self.meta, seed=1234)
        out2 = self.engine.render(photo_l, 15, metadata=self.meta, seed=1234)
        out3 = self.engine.render(photo_l, 15, metadata=self.meta, seed=5678)
        self.assertEqual(out1.tobytes(), out2.tobytes())
        self.assertNotEqual(out1.tobytes(), out3.tobytes())
        self.assertEqual(out1.size, (878, 600 + 112))

        # Portrait / square places bands left and right
        photo_s = make_test_photo(350, 350, fill=(40, 80, 120))
        out_sq = self.engine.render(photo_s, 15, metadata=self.meta, seed=42)
        scale_sq = self.engine.compute_scale(STYLE_SPECS[15], 350, 350)
        pad_l = int(56.0 * scale_sq)
        self.assertEqual(out_sq.size, (350 + 2 * pad_l, 350))
        # Center photo preserved
        self.assertEqual(out_sq.getpixel((pad_l + 175, 175)), (40, 80, 120))

        # Preview mode should alter grain dot density cleanly without error
        out_prev = self.engine.render(photo_l, 15, metadata=self.meta, seed=1234, preview=True)
        self.assertEqual(out_prev.size, out1.size)
        self.assertNotEqual(out_prev.tobytes(), out1.tobytes())

    def test_no_border_overlays_and_banners_render_ink(self):
        photo = make_test_photo(878, 600, fill=(20, 30, 40))
        for sid in (6, 8, 13, 14):
            out = self.engine.render(photo, sid, metadata=self.meta)
            self.assertEqual(out.size, (878, 600))
            # Top pixel unchanged
            self.assertEqual(out.getpixel((10, 10)), (20, 30, 40))
            # Bottom center region must have overlay graphics different from background
            changed = sum(
                1
                for y in range(440, 595)
                for x in range(200, 680, 4)
                if out.getpixel((x, y)) != (20, 30, 40)
            )
            self.assertGreater(changed, 200, f"Style {sid} overlay had too little ink")

    def test_gainmap_pipeline_excludes_and_tints(self):
        w, h = 846, 600
        photo = make_test_photo(w, h, fill=(40, 80, 120))
        gm_photo = Image.new("L", (423, 300), 210)  # Half-res gainmap

        # Style 1: border tinted to gainColorRect=159, black text darkened, sub_text excluded
        gm1 = self.engine.render_gainmap(photo, gm_photo, 1, metadata=self.meta)
        self.assertEqual(gm1.size, (int((846 + 32) * 0.5), int((600 + 16 + 144) * 0.5)))
        self.assertEqual(gm1.getpixel((2, 2)), 159)
        self.assertEqual(gm1.getpixel((16, 150)), 210)  # Inside hollow photo gainmap preserved

        # Style 15: Film frame border stays gainColorRect=128 while excluded gradient bands don't clobber it,
        # and corner strip logos get gainColor=170!
        photo15 = make_test_photo(878, 600, fill=(40, 80, 120))
        gm15_in = Image.new("L", (439, 300), 200)
        gm15 = self.engine.render_gainmap(photo15, gm15_in, 15, metadata=self.meta, seed=1)
        self.assertEqual(gm15.size, (439, 356))
        # Open area of top strip (between center date & corners, say x=150, y=20) should be 128
        self.assertEqual(gm15.getpixel((150, 20)), 128)
        # Signature gainmap tint test on Style 12
        sig = SignatureConfig(enabled=True, gravity=5, rotation=0.0, svg_content=SAMPLE_SIG_SVG)
        gm12 = self.engine.render_gainmap(photo, gm_photo, 12, metadata=self.meta, signature=sig)
        # Border corner = 128; signature ink area = 159 (0.625 * 255 + 0.5)
        self.assertEqual(gm12.getpixel((2, 2)), 128)
        sig_rect = self.engine.compute_signature_rect(STYLE_SPECS[12], sig, w, h, w + 32, h + 16 + 114)
        sx0 = round(sig_rect[0] * 0.5)
        sy0 = round(sig_rect[1] * 0.5)
        sx1 = round(sig_rect[2] * 0.5)
        sy1 = round(sig_rect[3] * 0.5)
        sig_pixels = {gm12.getpixel((x, y)) for y in range(sy0, sy1) for x in range(sx0, sx1)}
        self.assertIn(159, sig_pixels)

    def test_cli_list_styles_and_batch_export(self):
        res_list = subprocess.run(
            [sys.executable, "-m", "phocus_watermark.cli", "--list-styles"],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        for sid in STYLE_ORDER:
            self.assertIn(f"{sid:2d}:", res_list.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            src_jpg = tmp_dir / "src.jpg"
            out_dir = tmp_dir / "out"
            make_test_photo(900, 600, fill=(50, 70, 90)).save(src_jpg, quality=95)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "phocus_watermark.cli",
                    str(src_jpg),
                    "--all",
                    "--p3",
                    "--seed",
                    "9",
                    "--body",
                    "X2D 100C",
                    "--lens",
                    "XCD 90V",
                    "--aperture",
                    "2.5",
                    "--shutter",
                    "1/1000",
                    "--iso",
                    "64",
                    "--date",
                    "2026 02 18",
                    "-o",
                    str(out_dir),
                ],
                cwd=str(ENGINE_ROOT),
                capture_output=True,
                text=True,
                check=True,
            )
            outputs = list(out_dir.glob("style_*.jpg"))
            self.assertEqual(len(outputs), 13)
            with Image.open(outputs[0]) as sample_out:
                self.assertIn("icc_profile", sample_out.info)


if __name__ == "__main__":
    unittest.main()
