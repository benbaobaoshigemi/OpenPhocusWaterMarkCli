"""1:1 standalone watermark & photo frame renderer matching Hasselblad Phocus Mobile 1.2.0."""

from __future__ import annotations

import math
from pathlib import Path
import random
from typing import Dict, Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from .models import (
    PhotoMetadata,
    SignatureConfig,
    STYLE_SPECS,
    StyleSpec,
    extract_metadata_from_image,
)
from .vector_rasterizer import (
    argb_int_to_rgba,
    rasterize_svg_signature,
    rasterize_vector_drawable,
)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def round_to_int(x: float) -> int:
    """Match Kotlin MathKt.roundToInt / Java Math.round(float) for non-negative/general floats."""
    return int(math.floor(x + 0.5))


class PhocusWatermarkEngine:
    """1:1 replication of Hasselblad Phocus Mobile 1.2.0 PhotoFrame (H0 + p099k6) engine."""

    def __init__(self, assets_dir: Optional[Path] = None) -> None:
        self.assets_dir = Path(assets_dir) if assets_dir else ASSETS_DIR
        self._font_cache: Dict[Tuple[str, float], ImageFont.FreeTypeFont] = {}
        self._overlay_cache: Dict[str, Image.Image] = {}

    def _get_font(self, rel_path: str, size_px: float) -> ImageFont.FreeTypeFont:
        key = (rel_path, round(size_px, 2))
        cached = self._font_cache.get(key)
        if cached is not None:
            return cached
        font_path = self.assets_dir / rel_path
        font = ImageFont.truetype(str(font_path), max(1.0, float(size_px)))
        self._font_cache[key] = font
        return font

    def _load_overlay(self, filename: str) -> Image.Image:
        cached = self._overlay_cache.get(filename)
        if cached is not None:
            return cached
        path = self.assets_dir / "overlays" / filename
        img = Image.open(path).convert("RGBA")
        self._overlay_cache[filename] = img
        return img

    @staticmethod
    def compute_scale(spec: StyleSpec, img_w: int, img_h: int) -> float:
        """Exact replication of p099k6.m.b(int width, int height)."""
        if img_w > img_h:
            l, _, r, _ = spec.padding_landscape
            return float(img_w) / (878.0 - l - r)
        l, _, r, _ = spec.padding_portrait
        return float(img_w) / (462.0 - l - r)

    @classmethod
    def compute_padding(cls, spec: StyleSpec, img_w: int, img_h: int) -> Tuple[int, int, int, int]:
        """Exact replication of p099k6.m.e(int width, int height) -> (left, top, right, bottom)."""
        scale = cls.compute_scale(spec, img_w, img_h)
        pad = spec.padding_landscape if img_w > img_h else spec.padding_portrait
        return (
            int(pad[0] * scale),
            int(pad[1] * scale),
            int(pad[2] * scale),
            int(pad[3] * scale),
        )

    @classmethod
    def compute_signature_rect(
        cls,
        spec: StyleSpec,
        sig: SignatureConfig,
        img_w: int,
        img_h: int,
        canvas_w: int,
        canvas_h: int,
    ) -> Tuple[int, int, int, int]:
        """Exact replication of p099k6.m.k(rotation, img_w, img_h, canvas_w, canvas_h, gravity)."""
        scale = cls.compute_scale(spec, img_w, img_h)
        pad_l, _, pad_r, pad_b = cls.compute_padding(spec, img_w, img_h)

        box_w_base, box_h_base = (
            spec.signature_box_landscape if img_w > img_h else spec.signature_box_portrait
        )
        if sig.rotation == 90.0:
            raw_w, raw_h = box_w_base, box_h_base
        else:
            raw_w, raw_h = box_h_base, box_w_base

        sig_w = int(raw_w * scale)
        sig_h = int(raw_h * scale)

        is_ge_square = img_w >= img_h
        gravity = sig.gravity
        if gravity == 5:  # Gravity.RIGHT
            margin = 26 if is_ge_square else 12
            x = int((canvas_w - pad_r) - sig_w - (margin * scale))
        elif gravity == 17:  # Gravity.CENTER
            x = int((canvas_w - sig_w) / 2)
        elif gravity == 8388613:  # GravityCompat.END
            margin = 12 if is_ge_square else 26
            x = int((canvas_w - pad_r) - sig_w - (margin * scale))
        else:  # LEFT / START / default
            margin = 16 if is_ge_square else 10
            x = int((margin * scale) + pad_l)

        if spec.signature_top_anchored:
            y = int((14.0 * scale) + pad_b)
        else:
            y = int(((canvas_h - sig_h) - pad_b) - (16.0 * scale))

        return (x, y, x + sig_w, y + sig_h)

    @staticmethod
    def _draw_linear_gradient(
        w: int,
        h: int,
        direction: str,
        c_dark: Tuple[int, int, int] = (0x14, 0x14, 0x14),
        c_light: Tuple[int, int, int] = (0x2C, 0x2C, 0x2C),
    ) -> Image.Image:
        """Render 3-stop 50%-midway LinearGradient matching FilmFrameGradientView.b()."""
        c_mid = (
            max(0, min(255, int(((c_dark[0] - c_light[0]) * 0.5) + c_light[0]))),
            max(0, min(255, int(((c_dark[1] - c_light[1]) * 0.5) + c_light[1]))),
            max(0, min(255, int(((c_dark[2] - c_light[2]) * 0.5) + c_light[2]))),
        )
        # Note FilmFrameGradientView.a(a, b) == int(((b - a) * 0.5) + a), symmetric for c_dark <-> c_light
        start = c_light if direction in ("top", "left") else c_dark
        end = c_dark if direction in ("top", "left") else c_light

        def lerp_rgb(t: float) -> Tuple[int, int, int, int]:
            if t <= 0.5:
                u = t * 2.0
                c0, c1 = start, c_mid
            else:
                u = (t - 0.5) * 2.0
                c0, c1 = c_mid, end
            return (
                int(c0[0] + (c1[0] - c0[0]) * u + 0.5),
                int(c0[1] + (c1[1] - c0[1]) * u + 0.5),
                int(c0[2] + (c1[2] - c0[2]) * u + 0.5),
                255,
            )

        if direction in ("top", "bottom"):
            col = Image.new("RGBA", (1, max(1, h)))
            px = col.load()
            denom = max(1, h - 1)
            for y in range(h):
                px[0, y] = lerp_rgb(y / denom)
            return col.resize((max(1, w), max(1, h)), Image.Resampling.NEAREST)
        else:
            row = Image.new("RGBA", (max(1, w), 1))
            px = row.load()
            denom = max(1, w - 1)
            for x in range(w):
                px[x, 0] = lerp_rgb(x / denom)
            return row.resize((max(1, w), max(1, h)), Image.Resampling.NEAREST)

    def _render_film_band(
        self,
        band_w: int,
        band_h: int,
        direction: str,
        scale: float,
        rng: random.Random,
        base_size: int = 4000,
        noise_amount: float = 0.04,
        noise_alpha: float = 0.4,
        preview: bool = False,
    ) -> Image.Image:
        """Render FilmFrameGradientView gradient + sprocket holes + film grain."""
        band = self._draw_linear_gradient(band_w, band_h, direction)

        # Sprocket holes
        if direction in ("top", "bottom"):
            hole_src = self._load_overlay("film_frame_square_horizontal.webp")
            hole_w = 18.0 * scale
            hole_h = 16.0 * scale
            pad_start = 8.0 * scale
            pad_end = 6.0 * scale
            y_hole = (32.0 * scale) if direction == "top" else pad_start
            avail = band_w - pad_end - hole_w - pad_start
            count = max(1, round_to_int(avail / (hole_w + hole_h)))
            step = avail / count
            hole_bmp = hole_src.resize((max(1, int(round(hole_w))), max(1, int(round(hole_h)))), Image.Resampling.LANCZOS)
            for idx in range(count + 1):
                x0 = int(round((idx * step) + pad_start))
                y0 = int(round(y_hole))
                band.alpha_composite(hole_bmp, (x0, y0))
        else:
            hole_src = self._load_overlay("film_frame_square_vertical.webp")
            hole_w = 16.0 * scale
            hole_h = 18.0 * scale
            pad_start = 4.0 * scale
            pad_end = 6.0 * scale
            x_hole = (32.0 * scale) if direction == "left" else (8.0 * scale)
            avail = band_h - pad_end - hole_h - pad_start
            count = max(1, round_to_int(avail / (hole_h + hole_w)))
            step = avail / count
            hole_bmp = hole_src.resize((max(1, int(round(hole_w))), max(1, int(round(hole_h)))), Image.Resampling.LANCZOS)
            for idx in range(count + 1):
                x0 = int(round(x_hole))
                y0 = int(round((idx * step) + pad_start))
                band.alpha_composite(hole_bmp, (x0, y0))

        # Film grain particles (FilmFrameGradientView.onDraw lines 418-455)
        if noise_amount > 0.0 and noise_alpha > 0.0 and band_w > 0 and band_h > 0:
            f_max = max(float(band_w), float(band_h)) / float(base_size)
            s_min = 1.0 * f_max
            s_max = 2.0 * f_max
            avg = (s_min + s_max) * 0.5
            num_dots = int((band_w * band_h * noise_amount) / max(1e-6, avg * avg))
            if preview:
                num_dots = max(1, int(num_dots / 10.0))
            alpha_byte = max(0, min(255, int(255.0 * noise_alpha)))
            noise_layer = Image.new("RGBA", (band_w, band_h), (0, 0, 0, 0))
            nd = ImageDraw.Draw(noise_layer)
            rect_fill = (0, 0, 0, alpha_byte)
            for _ in range(num_dots):
                cx = rng.random() * band_w
                cy = rng.random() * band_h
                half = (s_min + (s_max - s_min) * rng.random()) * 0.5
                nd.rectangle((cx - half, cy - half, cx + half, cy + half), fill=rect_fill)
            band.alpha_composite(noise_layer)

        return band

    def _draw_text_centered_y(
        self,
        draw: ImageDraw.ImageDraw,
        x: float,
        box_top: float,
        box_h: float,
        text: str,
        font: ImageFont.FreeTypeFont,
        color: Tuple[int, int, int, int],
        anchor_x: str = "l",
    ) -> float:
        """Draw single-line text centered vertically within [box_top, box_top + box_h]; return advance width."""
        cy = box_top + box_h * 0.5
        draw.text((x, cy), text, font=font, fill=color, anchor=f"{anchor_x}m")
        return float(draw.textlength(text, font=font))

    def _build_layers(
        self,
        spec: StyleSpec,
        img_w: int,
        img_h: int,
        canvas_w: int,
        canvas_h: int,
        metadata: PhotoMetadata,
        seed: Optional[int],
        preview: bool = False,
    ) -> Tuple[Optional[Image.Image], Image.Image, Image.Image]:
        """
        Render MaskView + inflated layout layers onto transparent RGBA canvases of size (canvas_w, canvas_h).
        Returns (border_bg_rgba_or_None, main_overlay_rgba, excluded_overlay_rgba) where `excluded_overlay_rgba`
        holds views tagged android:tag="exclude" (sub_text in styles 1/4, icon_logo_back in styles 13/14,
        FilmFrameGradientView in style 15) so SDR and Gainmap paths compose identically to H0.k().
        """
        scale = self.compute_scale(spec, img_w, img_h)
        pad_l, pad_t, pad_r, pad_b = self.compute_padding(spec, img_w, img_h)
        is_landscape = img_w > img_h

        # Border frame ring (MaskView.a)
        bg = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        has_border = any(p > 0 for p in (pad_l, pad_t, pad_r, pad_b))
        if has_border:
            bd = ImageDraw.Draw(bg)
            mc = spec.mask_color
            if pad_t > 0:
                bd.rectangle((0, 0, canvas_w, pad_t), fill=mc)
            if pad_b > 0:
                bd.rectangle((0, pad_t + img_h, canvas_w, canvas_h), fill=mc)
            if pad_l > 0:
                bd.rectangle((0, 0, pad_l, canvas_h), fill=mc)
            if pad_r > 0:
                bd.rectangle((pad_l + img_w, 0, canvas_w, canvas_h), fill=mc)

        overlay = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        excluded = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw_excl = ImageDraw.Draw(excluded)
        sid = spec.style_id

        if sid == 1:
            # Classic White (b(2) + normal_pic_info_layout)
            logo_sz = max(1, round_to_int(48.0 * scale))
            logo_top = round_to_int(32.0 * scale + img_h)
            logo_x = (canvas_w - logo_sz) // 2
            logo_bmp = rasterize_vector_drawable(
                str(self.assets_dir / "vectors" / "hblack_logo_vector.xml"), logo_sz, logo_sz
            )
            overlay.alpha_composite(logo_bmp, (logo_x, logo_top))

            if is_landscape:
                main_top_m, main_sp, sub_top_m, sub_sp, sub_h = 6.0, 16.0, 2.0, 14.0, 24.0
            else:
                main_top_m, main_sp, sub_top_m, sub_sp, sub_h = 4.0, 14.0, 0.0, 12.0, 20.0

            main_top = logo_top + logo_sz + round_to_int(main_top_m * scale)
            main_h = round_to_int(24.0 * scale)
            f_main = self._get_font("fonts/roboto_flex.ttf", main_sp * scale)
            self._draw_text_centered_y(
                draw, canvas_w * 0.5, main_top, main_h, metadata.full_body_name, f_main, (0, 0, 0, 255), anchor_x="m"
            )

            sub_top = main_top + main_h + round_to_int(sub_top_m * scale)
            sub_box_h = round_to_int(sub_h * scale)
            f_sub = self._get_font("fonts/roboto_flex.ttf", sub_sp * scale)
            self._draw_text_centered_y(
                draw_excl, canvas_w * 0.5, sub_top, sub_box_h, metadata.lens_info_text, f_sub, (0, 0, 0, 0x80), anchor_x="m"
            )

        elif sid in (2, 3, 12):
            # Wordmark frames: Simple White (2), Bottom Bar Simple (3), Black Orange (12)
            word_w = max(1, round_to_int(182.0 * scale))
            word_h = max(1, round_to_int((182.0 * scale) / 10.111111))
            if sid == 3:
                top_dp = 36.0 if is_landscape else 34.0
            else:
                top_dp = 64.0 if is_landscape else 56.0
            top_y = round_to_int(top_dp * scale + img_h)
            x0 = (canvas_w - word_w) // 2
            vec_name = "ic_hasslblad_logo_orange_vector.xml" if sid == 12 else "hasselblad_logo.xml"
            word_bmp = rasterize_vector_drawable(str(self.assets_dir / "vectors" / vec_name), word_w, word_h)
            overlay.alpha_composite(word_bmp, (x0, top_y))

        elif sid == 4:
            # Bottom Bar Info (b(5) + normal_noboader_pic_info_layout)
            logo_sz = max(1, round_to_int(48.0 * scale))
            logo_right_m = round_to_int(24.0 * scale)
            logo_bot_m = round_to_int(21.0 * scale)
            logo_x = canvas_w - logo_right_m - logo_sz
            logo_y = canvas_h - logo_bot_m - logo_sz
            logo_bmp = rasterize_vector_drawable(
                str(self.assets_dir / "vectors" / "hblack_logo_vector.xml"), logo_sz, logo_sz
            )
            overlay.alpha_composite(logo_bmp, (logo_x, logo_y))

            text_x = round_to_int(24.0 * scale)
            main_h = round_to_int(24.0 * scale)
            f_main = self._get_font("fonts/roboto_flex.ttf", 16.0 * scale)
            self._draw_text_centered_y(
                draw, text_x, logo_y, main_h, metadata.full_body_name, f_main, (0, 0, 0, 255), anchor_x="l"
            )

            sub_top = logo_y + main_h + round_to_int(2.0 * scale)
            sub_h = round_to_int(24.0 * scale)
            f_sub = self._get_font("fonts/roboto_flex.ttf", 14.0 * scale)
            self._draw_text_centered_y(
                draw_excl, text_x, sub_top, sub_h, metadata.lens_info_text, f_sub, (0, 0, 0, 0x80), anchor_x="l"
            )

        elif sid == 6:
            # Minimal Overlay (t + pic_info_normal_no_all_boader_layout)
            logo_sz = max(1, round_to_int(32.0 * scale))
            logo_bot_m = round_to_int(44.0 * scale)
            logo_x = (canvas_w - logo_sz) // 2
            logo_y = canvas_h - logo_bot_m - logo_sz
            logo_bmp = rasterize_vector_drawable(
                str(self.assets_dir / "vectors" / "hwhite_logo_vector.xml"), logo_sz, logo_sz
            )
            overlay.alpha_composite(logo_bmp, (logo_x, logo_y))

            row_h = round_to_int(20.0 * scale)
            row_bot_m = round_to_int(16.0 * scale)
            row_top = canvas_h - row_bot_m - row_h
            gap_m = round_to_int(20.0 * scale)
            f_row = self._get_font("fonts/roboto_flex.ttf", 12.0 * scale)
            c_lbl = (255, 255, 255, 0xCC)
            c_pipe = (255, 255, 255, 0x99)
            c_val = (255, 255, 255, 0xF2)
            segments = [
                ("Aperture", c_lbl, 0),
                ("  |  ", c_pipe, 0),
                (metadata.aperture, c_val, 0),
                ("Shutter", c_lbl, gap_m),
                ("  |  ", c_pipe, 0),
                (metadata.shutter, c_val, 0),
                ("ISO", c_lbl, gap_m),
                ("  |  ", c_pipe, 0),
                (metadata.iso, c_val, 0),
            ]
            total_w = sum(draw.textlength(txt, font=f_row) + ml for txt, _, ml in segments)
            cur_x = (canvas_w - total_w) * 0.5
            for txt, col, ml in segments:
                cur_x += ml
                cur_x += self._draw_text_centered_y(draw, cur_x, row_top, row_h, txt, f_row, col, anchor_x="l")

        elif sid == 7:
            # Top Wordmark White (b(1) + pic_info_normal_logo_top_layout)
            word_w = max(1, round_to_int(182.0 * scale))
            word_h = max(1, round_to_int((182.0 * scale) / 10.111111))
            word_top = round_to_int(48.0 * scale)
            word_x = (canvas_w - word_w) // 2
            word_bmp = rasterize_vector_drawable(
                str(self.assets_dir / "vectors" / "hasselblad_logo.xml"), word_w, word_h
            )
            overlay.alpha_composite(word_bmp, (word_x, word_top))

            row_top = round_to_int(144.0 * scale + img_h)
            row_h = round_to_int(20.0 * scale)
            gap_m = round_to_int(24.0 * scale)
            f_row = self._get_font("fonts/roboto_flex.ttf", 12.0 * scale)
            c_lbl = (0, 0, 0, 0x80)
            c_val = (0, 0, 0, 0xF2)
            segments = [
                ("Aperture", c_lbl, 0),
                ("   ", c_val, 0),
                (metadata.aperture, c_val, 0),
                ("Shutter", c_lbl, gap_m),
                ("   ", c_val, 0),
                (metadata.shutter, c_val, 0),
                ("ISO", c_lbl, gap_m),
                ("   ", c_val, 0),
                (metadata.iso, c_val, 0),
            ]
            total_w = sum(draw.textlength(txt, font=f_row) + ml for txt, _, ml in segments)
            cur_x = (canvas_w - total_w) * 0.5
            for txt, col, ml in segments:
                cur_x += ml
                cur_x += self._draw_text_centered_y(draw, cur_x, row_top, row_h, txt, f_row, col, anchor_x="l")

        elif sid == 8:
            # Earth Explorer Overlay (d + earth_explorer_no_boader_pic_info_layout)
            w_dp, bot_dp = (138.0, 24.0) if is_landscape else (106.0, 16.0)
            logo_w = max(1, round_to_int(w_dp * scale))
            logo_h = max(1, round_to_int((w_dp * scale) / 1.9032258))
            bot_m = round_to_int(bot_dp * scale)
            src = self._load_overlay("earth_explorer_logo.png").resize((logo_w, logo_h), Image.Resampling.LANCZOS)
            overlay.alpha_composite(src, ((canvas_w - logo_w) // 2, canvas_h - bot_m - logo_h))

        elif sid == 9:
            # Earth Explorer White (b(0) + earth_explorer_pic_info_layout)
            w_dp = 140.0 if is_landscape else 118.0
            asset_name = "earth_explorer_logo_black_horizontal.png" if is_landscape else "earth_explorer_logo_black_vertical.png"
            logo_w = max(1, round_to_int(w_dp * scale))
            logo_h = max(1, round_to_int((w_dp * scale) / 1.9032258))
            top_y = round_to_int(36.0 * scale + img_h)
            src = self._load_overlay(asset_name).resize((logo_w, logo_h), Image.Resampling.LANCZOS)
            overlay.alpha_composite(src, ((canvas_w - logo_w) // 2, top_y))

        elif sid in (13, 14):
            # New Year 2026 (o) and Year of Horse 2026 (x)
            if sid == 13:
                h_dp = 157.0 if is_landscape else 126.71429
                back_name = "new_year_logo_2026_horizontal_back.webp" if is_landscape else "new_year_logo_2026_vertical_back.webp"
                logo_name = "new_year_logo_2026_horizontal_logo.webp" if is_landscape else "new_year_logo_2026_vertical_logo.webp"
            else:
                h_dp = 160.01744 if is_landscape else 110.00001
                back_name = "new_year_horse_2026_horizontal_back.webp" if is_landscape else "new_year_horse_2026_vertical_back.webp"
                logo_name = "new_year_horse_2026_horizontal_logo.webp" if is_landscape else "new_year_horse_2026_vertical_logo.webp"
            banner_h = max(1, round_to_int(h_dp * scale))
            y_pos = canvas_h - banner_h
            back_img = self._load_overlay(back_name).resize((canvas_w, banner_h), Image.Resampling.LANCZOS)
            logo_img = self._load_overlay(logo_name).resize((canvas_w, banner_h), Image.Resampling.LANCZOS)
            excluded.alpha_composite(back_img, (0, y_pos))
            overlay.alpha_composite(logo_img, (0, y_pos))

        elif sid == 15:
            # Film Frame (i + FilmFrameGradientView + pic_info_film_layout_{horizontal,vertical})
            band_px = max(1, round_to_int(56.0 * scale))
            rng = random.Random(seed) if seed is not None else random.Random()
            if is_landscape:
                top_band = self._render_film_band(canvas_w, band_px, "top", scale, rng, preview=preview)
                bot_band = self._render_film_band(canvas_w, band_px, "bottom", scale, rng, preview=preview)
                excluded.alpha_composite(top_band, (0, 0))
                excluded.alpha_composite(bot_band, (0, canvas_h - band_px))

                h_strip = max(1, round_to_int(32.0 * scale))
                ul = self._load_overlay("film_frame_logo_horizontal_up_left.webp").resize(
                    (max(1, round_to_int(260.0 * scale)), h_strip), Image.Resampling.LANCZOS
                )
                ur = self._load_overlay("film_frame_logo_horizontal_up_right.webp").resize(
                    (max(1, round_to_int(140.0 * scale)), h_strip), Image.Resampling.LANCZOS
                )
                bl = self._load_overlay("film_frame_logo_horizontal_bottom_left.webp").resize(
                    (max(1, round_to_int(140.0 * scale)), h_strip), Image.Resampling.LANCZOS
                )
                bc = self._load_overlay("film_frame_logo_horizontal.webp").resize(
                    (max(1, round_to_int(140.0 * scale)), h_strip), Image.Resampling.LANCZOS
                )
                br = self._load_overlay("film_frame_logo_horizontal_bottom_right.webp").resize(
                    (max(1, round_to_int(230.0 * scale)), h_strip), Image.Resampling.LANCZOS
                )
                overlay.alpha_composite(ul, (0, 0))
                overlay.alpha_composite(ur, (canvas_w - ur.width, 0))
                overlay.alpha_composite(bl, (0, canvas_h - h_strip))
                overlay.alpha_composite(bc, ((canvas_w - bc.width) // 2, canvas_h - h_strip))
                overlay.alpha_composite(br, (canvas_w - br.width, canvas_h - h_strip))

                if metadata.create_time:
                    f_film = self._get_font("fonts/fusion_pixel_8px.otf", 13.0 * scale)
                    self._draw_text_centered_y(
                        draw,
                        canvas_w * 0.5,
                        0.0,
                        float(h_strip),
                        metadata.create_time,
                        f_film,
                        (0x9F, 0x79, 0x5A, 255),
                        anchor_x="m",
                    )
            else:
                left_band = self._render_film_band(band_px, canvas_h, "left", scale, rng, preview=preview)
                right_band = self._render_film_band(band_px, canvas_h, "right", scale, rng, preview=preview)
                excluded.alpha_composite(left_band, (0, 0))
                excluded.alpha_composite(right_band, (canvas_w - band_px, 0))

                w_strip = max(1, round_to_int(32.0 * scale))
                h_icon = max(1, round_to_int(140.0 * scale))
                ul = self._load_overlay("film_frame_logo_vertical_up_left.webp").resize((w_strip, h_icon), Image.Resampling.LANCZOS)
                ur = self._load_overlay("film_frame_logo_vertical_up_right.webp").resize((w_strip, h_icon), Image.Resampling.LANCZOS)
                lc = self._load_overlay("film_frame_logo_vertical.webp").resize((w_strip, h_icon), Image.Resampling.LANCZOS)
                bl = self._load_overlay("film_frame_logo_vertical_bottom_left.webp").resize((w_strip, h_icon), Image.Resampling.LANCZOS)
                br = self._load_overlay("film_frame_logo_vertical_bottom_right.webp").resize((w_strip, h_icon), Image.Resampling.LANCZOS)

                overlay.alpha_composite(ul, (0, 0))
                overlay.alpha_composite(ur, (canvas_w - w_strip, 0))
                overlay.alpha_composite(lc, (0, (canvas_h - h_icon) // 2))
                overlay.alpha_composite(bl, (0, canvas_h - h_icon))
                overlay.alpha_composite(br, (canvas_w - w_strip, canvas_h - h_icon))

                if metadata.create_time:
                    f_film = self._get_font("fonts/fusion_pixel_8px.otf", 13.0 * scale)
                    tw = max(1, int(math.ceil(draw.textlength(metadata.create_time, font=f_film) + 8)))
                    th = max(1, int(math.ceil(20.0 * scale)))
                    txt_bmp = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
                    td = ImageDraw.Draw(txt_bmp)
                    td.text((tw * 0.5, th * 0.5), metadata.create_time, font=f_film, fill=(0x9F, 0x79, 0x5A, 255), anchor="mm")
                    # Android android:rotation="90" rotates 90 deg clockwise -> PIL rotate(-90)
                    rot_txt = txt_bmp.rotate(-90, expand=True)
                    cx = canvas_w - w_strip + (w_strip - rot_txt.width) // 2
                    cy = (canvas_h - rot_txt.height) // 2
                    overlay.alpha_composite(rot_txt, (cx, cy))

        return (bg if has_border else None), overlay, excluded

    def _draw_signature_on(
        self,
        canvas: Image.Image,
        spec: StyleSpec,
        signature: Optional[SignatureConfig],
        img_w: int,
        img_h: int,
        canvas_w: int,
        canvas_h: int,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        override_color_argb: Optional[int] = None,
    ) -> None:
        """Rasterize and composite user/Phocus signature matching H0.f()."""
        if not signature or not signature.enabled:
            return
        svg_txt = signature.svg_content
        if svg_txt is None and signature.svg_path:
            svg_txt = Path(signature.svg_path).read_text(encoding="utf-8")
        if not svg_txt:
            return

        rect = self.compute_signature_rect(spec, signature, img_w, img_h, canvas_w, canvas_h)
        dst_x0 = round(rect[0] * scale_x)
        dst_y0 = round(rect[1] * scale_y)
        dst_x1 = round(rect[2] * scale_x)
        dst_y1 = round(rect[3] * scale_y)
        rw = max(1, dst_x1 - dst_x0)
        rh = max(1, dst_y1 - dst_y0)

        pen_color = override_color_argb if override_color_argb is not None else signature.pen_color
        sig_bmp = rasterize_svg_signature(
            svg_txt,
            max(1, rect[2] - rect[0]),
            max(1, rect[3] - rect[1]),
            rotation_deg=signature.rotation,
            pen_color_argb=pen_color,
        )
        sig_bmp = sig_bmp.resize((rw, rh), Image.Resampling.LANCZOS)
        canvas.alpha_composite(sig_bmp, (dst_x0, dst_y0))

    @staticmethod
    def _tint_src_in(rgba: Image.Image, gray: int) -> Image.Image:
        """Exact PorterDuff.Mode.SRC_IN tint with opaque (gray, gray, gray, 255)."""
        q = max(0, min(255, int(gray)))
        band = Image.new("L", rgba.size, q)
        return Image.merge("RGBA", (band, band, band, rgba.getchannel("A")))

    @staticmethod
    def _apply_gainmap_luminance_alpha(rgba: Image.Image) -> Image.Image:
        """
        Exact ColorMatrixColorFilter({0..0, -0.299, -0.587, -0.114, 1, 0}) used when !m.l():
        R'=G'=B'=0, A'=clamp(A - (0.299*R + 0.587*G + 0.114*B), 0, 255).
        """
        r, g, b, a = rgba.split()
        luma = Image.merge("RGB", (r, g, b)).convert("L")
        new_a = ImageChops.subtract(a, luma)
        zero = Image.new("L", rgba.size, 0)
        return Image.merge("RGBA", (zero, zero, zero, new_a))

    def render(
        self,
        image: Image.Image,
        style_id: int,
        metadata: Optional[PhotoMetadata] = None,
        signature: Optional[SignatureConfig] = None,
        apply_exif_transpose: bool = True,
        seed: Optional[int] = None,
        preview: bool = False,
    ) -> Image.Image:
        """
        Render a framed/watermarked RGB Image matching H0.k() 1:1.

        If `metadata` is omitted, EXIF metadata is extracted directly from `image`.
        If `apply_exif_transpose` is True, EXIF Orientation is applied before measuring (W, H),
        matching H0.n() / ExportComposeJob rotation handling.
        When `preview` is True, Film grain dot count is scaled by 1/10 matching FilmFrameGradientView.
        """
        if style_id not in STYLE_SPECS:
            raise ValueError(f"Unsupported Phocus style_id={style_id}. Valid IDs: {sorted(STYLE_SPECS)}")

        spec = STYLE_SPECS[style_id]
        src = ImageOps.exif_transpose(image) if apply_exif_transpose else image
        if metadata is None:
            metadata = extract_metadata_from_image(src)

        photo = src.convert("RGBA")
        img_w, img_h = photo.size
        pad_l, pad_t, pad_r, pad_b = self.compute_padding(spec, img_w, img_h)
        canvas_w = img_w + pad_l + pad_r
        canvas_h = img_h + pad_t + pad_b

        border_bg, overlay_main, overlay_excluded = self._build_layers(
            spec=spec,
            img_w=img_w,
            img_h=img_h,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            metadata=metadata,
            seed=seed,
            preview=preview,
        )

        # Compose photo inside hollowRect + border_bg + excluded + main + signature (matching H0.k)
        out = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        if border_bg is not None:
            out.alpha_composite(border_bg)
        out.paste(photo, (pad_l, pad_t))
        out.alpha_composite(overlay_excluded)
        out.alpha_composite(overlay_main)
        self._draw_signature_on(out, spec, signature, img_w, img_h, canvas_w, canvas_h)
        return out.convert("RGB")

    def render_gainmap(
        self,
        image: Image.Image,
        gainmap: Image.Image,
        style_id: int,
        metadata: Optional[PhotoMetadata] = None,
        signature: Optional[SignatureConfig] = None,
        apply_exif_transpose: bool = True,
        seed: Optional[int] = None,
        preview: bool = False,
    ) -> Image.Image:
        """
        Render the HDR Gainmap matching H0.k() lines 681-779 (Android API 34+ Gainmap pipeline).

        `image` provides the SDR photo dimensions & EXIF metadata; `gainmap` is the input Gainmap bitmap
        whose ratio (gainmap.width / image.width) scales the output framed Gainmap dimensions.
        """
        if style_id not in STYLE_SPECS:
            raise ValueError(f"Unsupported Phocus style_id={style_id}. Valid IDs: {sorted(STYLE_SPECS)}")

        spec = STYLE_SPECS[style_id]
        src = ImageOps.exif_transpose(image) if apply_exif_transpose else image
        gm_src = ImageOps.exif_transpose(gainmap) if apply_exif_transpose else gainmap
        if metadata is None:
            metadata = extract_metadata_from_image(src)

        img_w, img_h = src.size
        gm_w, gm_h = gm_src.size
        pad_l, pad_t, pad_r, pad_b = self.compute_padding(spec, img_w, img_h)
        canvas_w = img_w + pad_l + pad_r
        canvas_h = img_h + pad_t + pad_b

        scale_g = float(gm_w) / float(img_w)
        out_gm_w = max(1, int(canvas_w * scale_g))
        out_gm_h = max(1, int(canvas_h * scale_g))

        border_bg, overlay_main, overlay_excluded = self._build_layers(
            spec=spec,
            img_w=img_w,
            img_h=img_h,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            metadata=metadata,
            seed=seed,
            preview=preview,
        )

        # Build SDR mask bitmap prior to DST_OVER (border + excluded + main)
        sdr_mask = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        if border_bg is not None:
            sdr_mask.alpha_composite(border_bg)
        sdr_mask.alpha_composite(overlay_excluded)
        sdr_mask.alpha_composite(overlay_main)

        gm_canvas = Image.new("RGBA", (out_gm_w, out_gm_h), (0, 0, 0, 0))
        gm_rgba = gm_src.convert("RGBA")
        gm_canvas.paste(gm_rgba, (int(round(pad_l * scale_g)), int(round(pad_t * scale_g))))

        scale_x = float(out_gm_w) / float(canvas_w)
        scale_y = float(out_gm_h) / float(canvas_h)

        # paint2: tint full SDR mask by gain_color_rect (SRC_IN) scaled to gainmap canvas
        gm_rect_layer = self._tint_src_in(sdr_mask, int(spec.gain_color_rect)).resize(
            (out_gm_w, out_gm_h), Image.Resampling.LANCZOS
        )
        gm_canvas.alpha_composite(gm_rect_layer)

        # paint3: draw non-excluded views with either gain_color SRC_IN or luminance-alpha filter
        if spec.gain_use_solid_text:
            gm_text_layer = self._tint_src_in(overlay_main, int(spec.gain_color))
        else:
            gm_text_layer = self._apply_gainmap_luminance_alpha(overlay_main)
        gm_text_layer = gm_text_layer.resize((out_gm_w, out_gm_h), Image.Resampling.LANCZOS)
        gm_canvas.alpha_composite(gm_text_layer)

        # Signature drawn onto gainmap with Color.rgb(0.625f, 0.625f, 0.625f) -> 0xFF9F9F9F
        sig_gray = int(0.625 * 255.0 + 0.5)
        sig_color = (0xFF << 24) | (sig_gray << 16) | (sig_gray << 8) | sig_gray
        self._draw_signature_on(
            gm_canvas,
            spec,
            signature,
            img_w,
            img_h,
            canvas_w,
            canvas_h,
            scale_x=scale_x,
            scale_y=scale_y,
            override_color_argb=sig_color,
        )

        out_mode = gm_src.mode if gm_src.mode in ("L", "RGB") else "RGB"
        return gm_canvas.convert(out_mode)
