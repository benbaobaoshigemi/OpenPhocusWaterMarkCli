"""Command-line interface for Hasselblad Phocus Mobile 1.2.0 watermark engine."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from PIL import Image

from .models import STYLE_ORDER, STYLE_SPECS, SignatureConfig, format_metadata
from .renderer import ASSETS_DIR, PhocusWatermarkEngine


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Hasselblad Phocus Mobile 1.2.0 1:1 Watermark Engine")
    p.add_argument("input", nargs="?", help="Input photo path")
    p.add_argument("-o", "--output", help="Output path (file for single style, directory for --all)")
    p.add_argument("-s", "--style", type=int, default=1, help="Style ID (default: 1)")
    p.add_argument("--all", action="store_true", help="Render all 13 Phocus styles into output directory")
    p.add_argument("--list-styles", action="store_true", help="List all supported Phocus style IDs")
    p.add_argument("--aperture", type=float, default=None, help="Override aperture f-number (e.g. 2.5)")
    p.add_argument("--shutter", type=str, default=None, help="Override shutter speed (e.g. 1/1600)")
    p.add_argument("--iso", type=str, default=None, help="Override ISO (e.g. 64)")
    p.add_argument("--lens", type=str, default=None, help="Override lens model (e.g. 'XCD 90V')")
    p.add_argument("--body", type=str, default=None, help="Override camera body (e.g. 'X2D 100C')")
    p.add_argument("--date", type=str, default=None, help="Override Film date string ('YYYY MM DD')")
    p.add_argument("--sig-svg", type=str, default=None, help="Optional signature SVG path")
    p.add_argument("--sig-gravity", type=int, default=5, help="Signature gravity: 3=left, 5=right, 17=center, 8388613=end")
    p.add_argument("--sig-color", type=str, default=None, help="Signature tint color #AARRGGBB or #RRGGBB")
    p.add_argument("--sig-rotation", type=float, default=0.0, help="Signature rotation degrees (0 or 90)")
    p.add_argument("--quality", type=int, default=94, help="JPEG output quality (Phocus default: 94)")
    p.add_argument("--p3", action="store_true", help="Embed Phocus Display P3.icc profile on JPEG save")
    p.add_argument("--seed", type=int, default=None, help="Deterministic RNG seed for Film grain")
    p.add_argument("--preview", action="store_true", help="Enable preview mode (1/10 Film grain density)")
    return p


def _parse_argb_hex(s: Optional[str]) -> int:
    if not s:
        return -1
    h = s.strip().lstrip("#")
    if len(h) == 6:
        return int("FF" + h, 16)
    if len(h) == 8:
        val = int(h, 16)
        return val - 0x100000000 if val >= 0x80000000 else val
    raise ValueError(f"Invalid color hex: {s}")


def main() -> int:
    args = build_parser().parse_args()
    if args.list_styles:
        for sid in STYLE_ORDER:
            sp = STYLE_SPECS[sid]
            tag = " [cloud]" if sp.cloud_controlled else ""
            print(f"{sid:2d}: {sp.name}{tag} - {sp.description}")
        return 0

    if not args.input:
        raise SystemExit("Input photo path is required unless --list-styles is passed.")

    img = Image.open(args.input)
    engine = PhocusWatermarkEngine()

    meta = None
    if any(x is not None for x in (args.aperture, args.shutter, args.iso, args.lens, args.body, args.date)):
        meta = format_metadata(
            aperture=args.aperture,
            shutter=args.shutter,
            iso=args.iso,
            lens=args.lens,
            body_name=args.body or "X2D 100C",
            create_time_str=args.date,
        )

    sig = SignatureConfig(
        enabled=bool(args.sig_svg),
        gravity=args.sig_gravity,
        pen_color=_parse_argb_hex(args.sig_color),
        rotation=args.sig_rotation,
        svg_path=args.sig_svg,
    )

    icc_bytes = (ASSETS_DIR / "icc" / "Display P3.icc").read_bytes() if args.p3 else None

    def save_img(out_img: Image.Image, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        save_kwargs = {}
        if dst.suffix.lower() in (".jpg", ".jpeg"):
            save_kwargs["quality"] = args.quality
            save_kwargs["subsampling"] = 0
        if icc_bytes is not None:
            save_kwargs["icc_profile"] = icc_bytes
        out_img.save(dst, **save_kwargs)

    if args.all:
        out_dir = Path(args.output or (Path(args.input).stem + "_watermarks"))
        for sid in STYLE_ORDER:
            res = engine.render(img, sid, metadata=meta, signature=sig, seed=args.seed, preview=args.preview)
            save_img(res, out_dir / f"style_{sid:02d}_{STYLE_SPECS[sid].name.lower().replace(' ', '_')}.jpg")
        print(f"Rendered {len(STYLE_ORDER)} styles into {out_dir}")
        return 0

    out_path = Path(args.output or (Path(args.input).stem + f"_style{args.style}.jpg"))
    res = engine.render(img, args.style, metadata=meta, signature=sig, seed=args.seed, preview=args.preview)
    save_img(res, out_path)
    print(f"Saved style {args.style} -> {out_path} ({res.width}x{res.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
