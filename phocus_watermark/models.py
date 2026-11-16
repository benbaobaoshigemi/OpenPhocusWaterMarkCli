"""Data models, style catalog, and EXIF formatting matching Hasselblad Phocus Mobile 1.2.0."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import re
from typing import Dict, Optional, Tuple, Union

from PIL import ExifTags, Image


# Exact XCD lens lookup table from s7.S.f24657a
XCD_LENS_MAP: Dict[str, str] = {
    "XCD 21": "XCD 4,0 / 21",
    "XCD 25V": "XCD 2,5 / 25V",
    "XCD 30": "XCD 3,5 / 30",
    "XCD 38V": "XCD 2,5 / 38V",
    "XCD 45": "XCD 3,5 / 45",
    "XCD 45P": "XCD 4 / 45P",
    "XCD 28P": "XCD 4 / 28P",
    "XCD 55V": "XCD 2,5 / 55V",
    "XCD 65": "XCD 2,8 / 65",
    "XCD 80": "XCD 1,9 / 80",
    "XCD 90V": "XCD 2,5 / 90V",
    "XCD 90": "XCD 3,2 / 90",
    "XCD 120": "XCD 3,5 / 120 MACRO",
    "XCD 135": "XCD 2,8 / 135",
    "XCD 20-35E": "XCD 3,2-4,5 / 20-35E",
    "XCD 35-75": "XCD 3,5-4,5 / 35-75",
    "XCD 75P": "XCD 75P",
    "XCD 35-100E": "XCD 2,8-4 / 35-100E",
    "XCD 90 II": "XCD 2,5 / 90V",
    "XCD 38": "XCD 2,5 / 38V",
    "XCD 55": "XCD 2,5 / 55V",
    "-": "XCD",
}

# All 13 frame styles in H0.<init> in display registration order
STYLE_ORDER: Tuple[int, ...] = (0, 8, 9, 14, 13, 15, 12, 6, 7, 1, 2, 3, 4)


@dataclass(frozen=True)
class PhotoMetadata:
    """Normalized metadata fields matching p099k6.l (MaskImageInfo)."""

    aperture: str = "f/-"
    shutter: str = "-s"
    iso: str = "-"
    lens_info_text: str = "Third Party Lens"
    full_body_name: str = "Hasselblad X2D 100C"
    create_time: str = ""


@dataclass(frozen=True)
class SignatureConfig:
    """Signature overlay settings matching SignModel."""

    enabled: bool = False
    # Android Gravity constants: 3 (LEFT / START_FALLBACK), 5 (RIGHT), 17 (CENTER), 8388613 (END)
    gravity: int = 5
    # RGBA/ARGB int or hex string; -1 uses SVG native color without tinting
    pen_color: int = -1
    # 90.0f or 0.0f (D.e swaps width/height when rotation == 0 and rotates by -rotation otherwise)
    rotation: float = 0.0
    svg_path: Optional[str] = None
    svg_content: Optional[str] = None


@dataclass(frozen=True)
class StyleSpec:
    """Static geometry and rendering specification matching p099k6.m subclasses."""

    style_id: int
    name: str
    description: str
    padding_landscape: Tuple[float, float, float, float]  # (left, top, right, bottom) in 878-ref space
    padding_portrait: Tuple[float, float, float, float]   # (left, top, right, bottom) in 462-ref space
    mask_color: Tuple[int, int, int, int]                 # RGBA
    gain_color: float = 170.0
    gain_color_rect: float = 159.0
    gain_use_solid_text: bool = False                     # m.l()
    signature_top_anchored: bool = False                  # m.m()
    supported_signature_positions: int = 23               # m.j(); 23 supports CENTER (17), 7 does not
    signature_box_landscape: Tuple[int, int] = (176, 80)  # m.f21094d width, height
    signature_box_portrait: Tuple[int, int] = (132, 60)   # m.f21095e width, height
    cloud_controlled: bool = False                        # !m.o() (styles 13, 14)


STYLE_SPECS: Dict[int, StyleSpec] = {
    0: StyleSpec(
        style_id=0,
        name="None",
        description="No border or watermark (signature optional)",
        padding_landscape=(0.0, 0.0, 0.0, 0.0),
        padding_portrait=(0.0, 0.0, 0.0, 0.0),
        mask_color=(255, 255, 255, 255),
    ),
    1: StyleSpec(
        style_id=1,
        name="Classic White",
        description="White border with centered H emblem, camera model, and lens",
        padding_landscape=(16.0, 16.0, 16.0, 144.0),
        padding_portrait=(16.0, 16.0, 16.0, 136.0),
        mask_color=(255, 255, 255, 255),
    ),
    2: StyleSpec(
        style_id=2,
        name="Simple White",
        description="White border with centered HASSELBLAD wordmark at bottom",
        padding_landscape=(16.0, 16.0, 16.0, 114.0),
        padding_portrait=(16.0, 16.0, 16.0, 98.0),
        mask_color=(255, 255, 255, 255),
    ),
    3: StyleSpec(
        style_id=3,
        name="Bottom Bar Simple",
        description="Bottom-only white bar with centered HASSELBLAD wordmark",
        padding_landscape=(0.0, 0.0, 0.0, 90.0),
        padding_portrait=(0.0, 0.0, 0.0, 86.0),
        mask_color=(255, 255, 255, 255),
    ),
    4: StyleSpec(
        style_id=4,
        name="Bottom Bar Info",
        description="Bottom-only white bar with left-aligned camera/lens and right-aligned H emblem",
        padding_landscape=(0.0, 0.0, 0.0, 90.0),
        padding_portrait=(0.0, 0.0, 0.0, 90.0),
        mask_color=(255, 255, 255, 255),
    ),
    6: StyleSpec(
        style_id=6,
        name="Overlay Minimal EXIF",
        description="No-border bottom overlay with white H emblem and Aperture/Shutter/ISO row",
        padding_landscape=(0.0, 0.0, 0.0, 0.0),
        padding_portrait=(0.0, 0.0, 0.0, 0.0),
        mask_color=(255, 255, 255, 255),
        signature_top_anchored=True,
        supported_signature_positions=5,
    ),
    7: StyleSpec(
        style_id=7,
        name="Top Wordmark White",
        description="White border with top HASSELBLAD wordmark and bottom EXIF row",
        padding_landscape=(16.0, 114.0, 16.0, 84.0),
        padding_portrait=(16.0, 114.0, 16.0, 80.0),
        mask_color=(255, 255, 255, 255),
    ),
    8: StyleSpec(
        style_id=8,
        name="Earth Explorer Overlay",
        description="No-border overlay with centered Earth Explorer emblem at bottom",
        padding_landscape=(0.0, 0.0, 0.0, 0.0),
        padding_portrait=(0.0, 0.0, 0.0, 0.0),
        mask_color=(255, 255, 255, 255),
        supported_signature_positions=7,
    ),
    9: StyleSpec(
        style_id=9,
        name="Earth Explorer White",
        description="White border with centered black Earth Explorer emblem at bottom",
        padding_landscape=(16.0, 16.0, 16.0, 118.0),
        padding_portrait=(16.0, 16.0, 16.0, 106.0),
        mask_color=(255, 255, 255, 255),
    ),
    12: StyleSpec(
        style_id=12,
        name="Black Orange",
        description="Dark #1D1D1D border with copper-orange #9F795A HASSELBLAD wordmark",
        padding_landscape=(16.0, 16.0, 16.0, 114.0),
        padding_portrait=(16.0, 16.0, 16.0, 98.0),
        mask_color=(0x1D, 0x1D, 0x1D, 255),
        gain_color=170.0,
        gain_color_rect=128.0,
        gain_use_solid_text=True,
    ),
    13: StyleSpec(
        style_id=13,
        name="New Year 2026",
        description="No-border New Year 2026 full-width bottom graphic overlay",
        padding_landscape=(0.0, 0.0, 0.0, 0.0),
        padding_portrait=(0.0, 0.0, 0.0, 0.0),
        mask_color=(255, 255, 255, 255),
        gain_color=170.0,
        gain_color_rect=159.0,
        gain_use_solid_text=True,
        supported_signature_positions=7,
        cloud_controlled=True,
    ),
    14: StyleSpec(
        style_id=14,
        name="Year of the Horse 2026",
        description="No-border Year of the Horse 2026 full-width bottom graphic overlay",
        padding_landscape=(0.0, 0.0, 0.0, 0.0),
        padding_portrait=(0.0, 0.0, 0.0, 0.0),
        mask_color=(255, 255, 255, 255),
        gain_color=170.0,
        gain_color_rect=159.0,
        gain_use_solid_text=True,
        supported_signature_positions=7,
        cloud_controlled=True,
    ),
    15: StyleSpec(
        style_id=15,
        name="Film Frame",
        description="Film negative strips with gradient, sprocket holes, grain, and pixel date",
        padding_landscape=(0.0, 56.0, 0.0, 56.0),
        padding_portrait=(56.0, 0.0, 56.0, 0.0),
        mask_color=(255, 255, 255, 255),
        gain_color=170.0,
        gain_color_rect=128.0,
        gain_use_solid_text=True,
    ),
}


_OFFSET_RE = re.compile(r"^[+-](\d{2}):(\d{2})$")


def format_lens_info(lens: Optional[str]) -> str:
    """Replicate f.g() lens normalization against S.f24657a."""
    raw = lens if lens is not None else "-"
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    if "xcd" in raw.lower():
        mapped = XCD_LENS_MAP.get(raw)
        return mapped if mapped is not None else "Third Party Lens"
    if len(raw) != 0 and raw.lower() != "unknown":
        return raw
    return "Third Party Lens"


def format_timestamp_ms(create_date_ms: Optional[int], offset_time: Optional[str]) -> str:
    """Replicate s7.m0.a(createDateTime, offsetTime||'00:00', 'yyyy MM dd')."""
    if create_date_ms is None or create_date_ms <= 0:
        return ""
    offset_str = offset_time if offset_time else "+00:00"
    m = _OFFSET_RE.match(offset_str)
    if not m:
        return ""
    hours, mins = int(m.group(1)), int(m.group(2))
    sign = -1 if offset_str.startswith("-") else 1
    tz = timezone(sign * timedelta(hours=hours, minutes=mins))
    dt = datetime.fromtimestamp(create_date_ms / 1000.0, tz=tz)
    return dt.strftime("%Y %m %d")


def format_metadata(
    aperture: Optional[float] = None,
    shutter: Optional[str] = None,
    iso: Optional[Union[int, str]] = None,
    lens: Optional[str] = None,
    body_name: str = "X2D 100C",
    create_date_ms: Optional[int] = None,
    offset_time: Optional[str] = None,
    create_time_str: Optional[str] = None,
) -> PhotoMetadata:
    """Format raw photo metadata exactly matching Phocus f.g()."""
    ap_str = f"f/{aperture:.1f}" if aperture is not None else "f/-"
    sh_str = shutter if shutter is not None else "-"
    if "s" not in sh_str.lower():
        sh_str = f"{sh_str}s"
    iso_str = str(iso) if iso is not None else "-"

    body = body_name.strip()
    if body.lower().startswith("hasselblad"):
        # f.g() unconditionally prepends "Hasselblad " via c.h("Hasselblad ", fullBodyName),
        # whereas callers reading EXIF Model strip "Hasselblad " when populating fullBodyName.
        body = body[len("Hasselblad"):].strip()
    full_body = f"Hasselblad {body}" if body else "Hasselblad "

    lens_text = format_lens_info(lens)
    if create_time_str is not None:
        date_text = create_time_str
    else:
        date_text = format_timestamp_ms(create_date_ms, offset_time)

    return PhotoMetadata(
        aperture=ap_str,
        shutter=sh_str,
        iso=iso_str,
        lens_info_text=lens_text,
        full_body_name=full_body,
        create_time=date_text,
    )


def _ratio_to_float(val: object) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if hasattr(val, "numerator") and hasattr(val, "denominator"):
        return float(val.numerator) / float(val.denominator) if val.denominator else None
    if isinstance(val, tuple) and len(val) == 2 and val[1]:
        return float(val[0]) / float(val[1])
    return None


def _format_shutter_from_exif(exp_val: object) -> Optional[str]:
    secs = _ratio_to_float(exp_val)
    if secs is None or secs <= 0:
        return None
    if secs < 1.0:
        denom = round(1.0 / secs)
        if denom > 0 and abs(secs - 1.0 / denom) / secs < 0.02:
            return f"1/{denom}"
    if abs(secs - round(secs)) < 1e-4:
        return str(int(round(secs)))
    return f"{secs:.1f}".rstrip("0").rstrip(".")


def extract_metadata_from_image(image: Image.Image) -> PhotoMetadata:
    """Extract and format EXIF metadata from a PIL Image."""
    exif_raw = image.getexif()
    exif_named: Dict[str, object] = {}
    if exif_raw:
        for tag_id, val in exif_raw.items():
            name = ExifTags.TAGS.get(tag_id, tag_id)
            exif_named[str(name)] = val
        ifd = exif_raw.get_ifd(ExifTags.IFD.Exif)
        for tag_id, val in ifd.items():
            name = ExifTags.TAGS.get(tag_id, tag_id)
            exif_named[str(name)] = val

    aperture = _ratio_to_float(exif_named.get("FNumber"))
    shutter = _format_shutter_from_exif(exif_named.get("ExposureTime"))
    iso_val = exif_named.get("ISOSpeedRatings") or exif_named.get("PhotographicSensitivity")
    if isinstance(iso_val, (tuple, list)):
        iso_val = iso_val[0] if iso_val else None

    model = exif_named.get("Model")
    body = str(model).strip() if model else "X2D 100C"
    lens = exif_named.get("LensModel")
    lens_str = str(lens).strip() if lens else None

    date_str = ""
    dt_orig = exif_named.get("DateTimeOriginal") or exif_named.get("DateTime")
    if isinstance(dt_orig, str) and len(dt_orig) >= 10:
        parts = dt_orig[:10].replace("-", ":").split(":")
        if len(parts) == 3:
            date_str = f"{parts[0]} {parts[1]} {parts[2]}"

    return format_metadata(
        aperture=aperture,
        shutter=shutter,
        iso=iso_val,
        lens=lens_str,
        body_name=body,
        create_time_str=date_str,
    )
