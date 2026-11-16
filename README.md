# Hasselblad Phocus Watermark Engine (1:1 Replication)

基于 **Hasselblad Phocus Mobile v1.2.0** (`com.hasselblad.phocus2`) 静态逆向分析构建的 1:1 独立水印与画框渲染引擎，完整还原其全部 13 种官方样式、矢量字形、EXIF 映射、Film 胶片颗粒/齿孔算法、SVG 签名变换与 HDR Gainmap 合成管线。

详见技术规范文档：[SPEC.md](./SPEC.md)。

## 目录结构

```
Phocus-Watermark-Engine/
├── phocus_watermark/
│   ├── __init__.py
│   ├── models.py             # 13 种 StyleSpec 定义、XCD 镜头映射表、EXIF 格式化
│   ├── vector_rasterizer.py  # Android VectorDrawable & SVG 签名高精度光栅化
│   ├── renderer.py           # H0/MaskView/FilmFrameGradientView/Gainmap 1:1 渲染器
│   ├── cli.py                # 命令行工具
│   └── assets/               # 从 Phocus 1.2.0 APK 提取的原始矢量/贴图/字体/ICC 资源
│       ├── fonts/            # roboto_flex.ttf, fusion_pixel_8px.otf
│       ├── vectors/          # H 黑白标、HASSELBLAD 字标、橙色字标
│       ├── overlays/         # Earth Explorer、New Year 2026、Horse 2026、Film Strip
│       └── icc/              # Display P3.icc
├── tests/
│   └── test_watermark_engine.py
├── README.md
└── SPEC.md
```

## 快速开始

### Python API

```python
from PIL import Image
from phocus_watermark import PhocusWatermarkEngine, SignatureConfig, format_metadata

engine = PhocusWatermarkEngine()
img = Image.open("photo.jpg")

# 自动提取图片 EXIF 渲染 Style 1 (Classic White)
out = engine.render(img, style_id=1)
out.save("out_style1.jpg", quality=94, subsampling=0)

# 手动指定元数据与 SVG 签名渲染 Film Frame (Style 15)
meta = format_metadata(
    aperture=2.5,
    shutter="1/1600",
    iso=64,
    lens="XCD 90V",
    body_name="X2D 100C",
    create_time_str="2026 02 18",
)
sig = SignatureConfig(enabled=True, gravity=5, svg_path="signature.svg", rotation=0.0)
film = engine.render(img, style_id=15, metadata=meta, signature=sig, seed=42)
```

### CLI 用法

```powershell
# 列出全部 13 种样式
python -m phocus_watermark.cli --list-styles

# 导出单张指定样式并嵌入 Display P3 ICC
python -m phocus_watermark.cli input.jpg -s 12 --p3 -o output_s12.jpg

# 一次批量导出全部 13 种样式，并指定元数据与签名
python -m phocus_watermark.cli input.jpg --all -o ./out_all `
  --body "X2D 100C" --lens "XCD 90V" --aperture 2.5 --shutter "1/1000" --iso 64 --date "2026 02 18"
```

## 运行测试

```powershell
python -m unittest discover -s tests -v
```
