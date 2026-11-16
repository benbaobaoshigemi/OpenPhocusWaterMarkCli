# Hasselblad Phocus Mobile 1.2.0 水印与画框样式引擎逆向技术规范

本文档记录对 Hasselblad Phocus Mobile v1.2.0 (`com.hasselblad.phocus2`) 水印/画框系统全部类与资源的静态逆向分析结论，以及 [`phocus_watermark/`](./phocus_watermark/) 中 1:1 复刻实现的数学定义。

---

## 1. 总览与导出分流机制

* **壳与字节码恢复**：目标 APK 使用 AppGuard (`lib/arm64-v8a/libAppGuard.so`)，核心业务方法的 DEX `code_item` 存放于 `assets/dex.dgc2`（`0xa62ac8` 处 `16,781 * 24B` 索引表 + `0xac5024..0xdc35d0` 未加密 `code_item` 池）。回填完整字节码后，Java 层与 native 层分界清晰：
  * **仅 Style 0（无边框）且未启用签名**时，`C1114j` / `C1112h` / `C1113i`（`ExportComposeJob`）直接走 `RawUtil.exportRawDecodeToHandle` 原生直出，不调用 Java 合成。
  * **其余全部 12 种水印/边框样式，或 Style 0 启用签名时**，无论来源是 RAW 还是相机 JPG，最终均由 Java 层的 `H0` (`PicFrameManager`) 执行 `H0.k(...)` 完成合成（JPEG 导出质量 `94`，HDR 导出内嵌 `Display P3.icc` 与 API 34+ `Gainmap`）。

---

## 2. 注册表与 13 种样式全览

`H0.<init>` 按固定顺序注册 13 个 `p099k6.m` 子类实例（不存在 ID `5`、`10`、`11`）：

| Style ID | 类 (`p099k6`) | 名称 | 横屏边距 `(L, T, R, B)` | 竖屏/方图边距 `(L, T, R, B)` | 底框色 `maskColor` | 云控 (`!m.o()`) | Gainmap (`l()`, `gainColorRect`, `gainColor`) |
|---:|:---|:---|:---|:---|:---|:---:|:---|
| `0` | `p` | None（无框） | `(0, 0, 0, 0)` | `(0, 0, 0, 0)` | `#FFFFFF` | 否 | `(False, 159, 170)` |
| `8` | `d` | Earth Explorer Overlay | `(0, 0, 0, 0)` | `(0, 0, 0, 0)` | `#FFFFFF` | 否 | `(False, 159, 170)` |
| `9` | `b(0)` | Earth Explorer White | `(16, 16, 16, 118)` | `(16, 16, 16, 106)` | `#FFFFFF` | 否 | `(False, 159, 170)` |
| `14` | `x` | Year of the Horse 2026 | `(0, 0, 0, 0)` | `(0, 0, 0, 0)` | `#FFFFFF` | **是** | `(True, 159, 170)` |
| `13` | `o` | New Year 2026 | `(0, 0, 0, 0)` | `(0, 0, 0, 0)` | `#FFFFFF` | **是** | `(True, 159, 170)` |
| `15` | `i` | Film Frame | `(0, 56, 0, 56)` | `(56, 0, 56, 0)` | `#FFFFFF` | 否 | `(True, 128, 170)` |
| `12` | `k` | Black Orange | `(16, 16, 16, 114)` | `(16, 16, 16, 98)` | `#1D1D1D` | 否 | `(True, 128, 170)` |
| `6` | `t` | Overlay Minimal EXIF | `(0, 0, 0, 0)` | `(0, 0, 0, 0)` | `#FFFFFF` | 否 | `(False, 159, 170)` |
| `7` | `b(1)` | Top Wordmark White | `(16, 114, 16, 84)` | `(16, 114, 16, 80)` | `#FFFFFF` | 否 | `(False, 159, 170)` |
| `1` | `b(2)` | Classic White | `(16, 16, 16, 144)` | `(16, 16, 16, 136)` | `#FFFFFF` | 否 | `(False, 159, 170)` |
| `2` | `b(3)` | Simple White | `(16, 16, 16, 114)` | `(16, 16, 16, 98)` | `#FFFFFF` | 否 | `(False, 159, 170)` |
| `3` | `b(4)` | Bottom Bar Simple | `(0, 0, 0, 90)` | `(0, 0, 0, 86)` | `#FFFFFF` | 否 | `(False, 159, 170)` |
| `4` | `b(5)` | Bottom Bar Info | `(0, 0, 0, 90)` | `(0, 0, 0, 90)` | `#FFFFFF` | 否 | `(False, 159, 170)` |

*注：`H0.r` 会根据设备序列号规则（`AbstractC1425j` / `C1081l0`）过滤样式 `8` 和 `9`，并根据云控映射 `C1098x.f18588b` 决定是否显示 `13` 和 `14`。*

---

## 3. 核心坐标、缩放与画布计算 (`p099k6.m`)

设原图经过 EXIF 方向矫正后的尺寸为 $(W, H)$：

1. **横竖屏判定（所有样式通用）**
   $$\text{is\_landscape} = (W > H)$$
   正方形图片（$W = H$）严格按**竖屏**基准分支计算。

2. **缩放因子 $s = m.b(W, H)$**
   $$s = \begin{cases} \dfrac{W}{878.0 - (L_{\text{land}} + R_{\text{land}})}, & W > H \\ \dfrac{W}{462.0 - (L_{\text{port}} + R_{\text{port}})}, & W \le H \end{cases}$$

3. **边框像素 $m.e(W, H)$**
   四个方向的实际像素边距使用 **float 转 int 截断** (`(int)(dp * s)`):
   $$\text{pad}_L = \lfloor L \cdot s \rfloor,\quad \text{pad}_T = \lfloor T \cdot s \rfloor,\quad \text{pad}_R = \lfloor R \cdot s \rfloor,\quad \text{pad}_B = \lfloor B \cdot s \rfloor$$
   最终输出画布尺寸为：
   $$W_{\text{canvas}} = W + \text{pad}_L + \text{pad}_R,\quad H_{\text{canvas}} = H + \text{pad}_T + \text{pad}_B$$
   原图在画布上的挖空区域 `hollowRect` 为 $[\text{pad}_L,\ \text{pad}_T,\ \text{pad}_L + W,\ \text{pad}_T + H)$。

4. **控件几何量取整**
   样式子类 `a(view, W, H, preview)` 中的内部控件宽高、字号、margin 全程使用 `p101k9.a.D(x)`（`MathKt.roundToInt`，即 $\lfloor x + 0.5 \rfloor$）。

5. **关于 `img_h` 与 `pad_top` 的耦合**
   `b.a(view, W, H, ...)` 中参与垂直定位的变量 `i11` 传入的是 **原图高度 $H$**（不包含 $\text{pad}_T$）。例如 Style 1 的 `pad_top = 16s`，而 `logo.topMargin = round(32s + H)`，使得 Logo 顶部精确位于原图底边 `pad_top + H` 下方 `16s` 处；Style 7 的 `pad_top = 114s`，底部文字栏 `ll_text.topMargin = round(144s + H)`，位于原图底边下方 `30s` 处。

---

## 4. 各样式排版与资产细则

### 4.1 字体规则 (`H0.b`)

`H0.b` 遍历全部 `TextView`：
* 除 `film_date_text` (`2131296646`) 外，其余所有文字强制应用 `roboto_flex.ttf`（默认可变轴 `wght=400, opsz=14`）。
* `film_date_text`（Style 15）使用像素字体 `fusion_pixel_8px.otf`，`textSize = 13s`，颜色 `#9F795A`。

### 4.2 矢量与图文样式 (`1..4`, `6`, `7`, `8`, `9`, `12`, `13`, `14`)

* **Style 1 (`Classic White`)**：底框 `#FFFFFF`；水平居中 `hblack_logo_vector.xml`（$48s \times 48s$，`top = round(32s + H)`）；`main_text`（机身全称，`24s` 高，横屏 `topMargin=6s, 16sp`，竖屏 `4s, 14sp`，`#FF000000`）；`sub_text`（镜头文本，带 `tag="exclude"`，横屏 `topMargin=2s, 14sp, 24s高`，竖屏 `0s, 12sp, 20s高`，`#80000000`）。
* **Style 2 / 3 / 12 (`Simple White` / `Bottom Bar Simple` / `Black Orange`)**：居中绘制 `hasselblad_logo.xml`（Style 12 使用橙色矢量 `ic_hasslblad_logo_orange_vector.xml`），尺寸 $182s \times \text{round}(182s / 10.111111)$。`topMargin`：Style 2/12 横屏 `round(64s + H)`、竖屏 `round(56s + H)`；Style 3 横屏 `round(36s + H)`、竖屏 `round(34s + H)`。
* **Style 4 (`Bottom Bar Info`)**：仅底部白框 `90s`；右侧对齐 `hblack_logo_vector.xml`（$48s \times 48s$，`right=24s, bottom=21s`）；左侧 `left=24s` 与 Logo 顶边对齐绘制 `main_text`（`16sp`, `24s` 高，`#FF000000`），下方隔 `2s` 绘制 `sub_text`（`14sp`, `24s` 高，`#80000000`，`tag="exclude"`）。
* **Style 6 (`Overlay Minimal EXIF`)**：无边框叠加；居中 `hwhite_logo_vector.xml`（$32s \times 32s$，`bottom=44s`）；底部 `bottom=16s` 绘制单行参数（字号 `12sp`, 行高 `20s`）：`Aperture` (`#CCFFFFFF`) `  |  ` (`#99FFFFFF`) 值 (`#F2FFFFFF`)，组间左间距 `20s`。
* **Style 7 (`Top Wordmark White`)**：顶部居中 `hasselblad_logo.xml`（`182s`, `topMargin=48s`）；底部 `topMargin = round(144s + H)` 绘制单行参数（`12sp`, 行高 `20s`）：标签 (`#80000000`)、值 (`#F2000000`) 之间由 `24s` 左间距分隔，标签与值之间为三空格间隔。
* **Style 8 (`Earth Explorer Overlay`)**：无边框底部居中 `earth_explorer_logo.png`，横屏宽 `138s` 距底 `24s`；竖屏宽 `106s` 距底 `16s`；宽高比 `1.9032258`。
* **Style 9 (`Earth Explorer White`)**：白底边框，横屏 `earth_explorer_logo_black_horizontal.png`（宽 `140s`），竖屏 `earth_explorer_logo_black_vertical.png`（宽 `118s`），宽高比 `1.9032258`，`topMargin = round(36s + H)`。
* **Style 13 (`New Year 2026`) & Style 14 (`Year of the Horse 2026`)**：无边框底部全宽双层叠加（底图层带 `tag="exclude"` + 前景 Logo 层）：
  * Style 13：高度横屏 `157.0 * s`，竖屏 `126.71429 * s`。
  * Style 14：高度横屏 `160.01744 * s`，竖屏 `110.00001 * s`。

### 4.3 Style 15 (`Film Frame`) 胶片齿孔与颗粒算法 (`FilmFrameGradientView`)

1. **胶片带方向与边距**：横屏在**顶部与底部**各加 `56s` 高胶片带；竖屏/方图在**左、右**各加 `56s` 宽胶片带（底层 `MaskView` 同步留出 `#FFFFFF` 挖空外框，并在 Gainmap 中使用 `gainColorRect=128`）。
2. **三段线性渐变**：`#2C2C2C`（内侧，靠近照片边）→ `#202020`（`0.5` 中点）→ `#141414`（外侧，画布边缘）。
3. **齿孔排布**：
   * 横屏使用 `film_frame_square_horizontal.webp`（孔宽 $w_h = 18s$，高 $h_h = 16s$，`padStart=8s, padEnd=6s`；Top 带 $y=32s$，Bottom 带 $y=8s$）；可用长度 $\text{avail} = W_{\text{band}} - \text{padEnd} - w_h - \text{padStart}$，数量 $N = \max(1, \text{round}(\text{avail} / (w_h + h_h)))$，步距 $\text{avail}/N$，从 $i=0..N$ 共绘制 $N+1$ 个齿孔。
   * 竖屏使用 `film_frame_square_vertical.webp`（孔宽 $w_h = 16s$，高 $h_h = 18s$，`padStart=4s, padEnd=6s`；Left 带 $x=32s$，Right 带 $x=8s$），$\text{avail} = H_{\text{band}} - \text{padEnd} - h_h - \text{padStart}$，$N = \max(1, \text{round}(\text{avail} / (h_h + w_h)))$。
4. **胶片颗粒（Noise）**：
   $$f_{\max} = \frac{\max(W_{\text{band}}, H_{\text{band}})}{4000.0},\quad s_{\min}=f_{\max},\quad s_{\max}=2f_{\max},\quad \bar{s}=1.5f_{\max}$$
   $$\text{count} = \left\lfloor \frac{W_{\text{band}} \cdot H_{\text{band}} \cdot 0.04}{\bar{s}^2} \right\rfloor \quad (\text{preview 模式除以 } 10)$$
   每颗粒子为居中随机方形，`size ~ Uniform(s_min, s_max)`，填充 `#66000000`（`alpha = int(255 * 0.4) = 102`）。
5. **胶片角标与像素日期**：
   * 横屏：`32s` 高底条，贴合外边对齐绘制 5 张角标：左上 `260s×32s`、右上 `140s×32s`、左下 `140s×32s`、下中 `140s×32s`、右下 `230s×32s`；上居中绘制 `createTime`（`YYYY MM DD`）。
   * 竖屏：`32s` 宽侧条，角标宽 `32s` 高 `140s`（左上、右上、左中、左下、右下）；日期文字以中心为轴顺时针旋转 `90°`（`android:rotation="90"`），置于右侧居中区域。

---

## 5. 签名定位与旋转 (`SignModel`, `m.k`, `D.e`)

* 默认配置：`SignModel(enable, gravity, penColor, rotation)`，`PhotoFrameActivity` 默认 `gravity=5 (RIGHT), penColor=-1 (SVG原色), rotation=0f`。
* 是否允许居中 (`17`)：检查 `(supported_signature_positions & 17) == 17`；`6` 仅支持 `5`（顶部锚定 `signature_top_anchored = True`），`8/13/14` 支持 `7`（无居中）。
* **边框内尺寸**：横屏参考框 `176×80`，竖屏/方图 `132×60`；当 `rotation == 90.0` 时宽取 `rect.width`、高取 `rect.height`，当 `rotation != 90.0`（如 `0°`）时宽高对调，按缩放因子 $s$ 截断取整得 `(rw, rh)`。`D.e` 按 `-rotation` 逆时针旋转 SVG（`0°` 时宽高交换以贴合预设水平手写体框）。
* **位置计算**（`is_sq_or_land = (W >= H)`）：
  * `gravity = 5 (RIGHT)`：`x = W_canvas - pad_R - rw - (26 if W >= H else 12) * s`
  * `gravity = 8388613 (END)`：`x = W_canvas - pad_R - rw - (12 if W >= H else 26) * s`
  * `gravity = 17 (CENTER)`：`x = (W_canvas - rw) // 2`
  * 其他（`3 LEFT`）：`x = pad_L + (16 if W >= H else 10) * s`
  * 垂直 `y`：若 `m.m()` 为真（Style 6），`y = (14s + pad_B)`；否则 `y = H_canvas - rh - pad_B - 16s`。

---

## 6. EXIF 元数据格式化 (`f.g`, `S`, `j0`, `m0`)

* **光圈 (`j0.a`)**：强制保留 1 位小数，`"f/%.1f"`；缺失则为 `"f/-"`。
* **镜头 (`f.g` + `S.f24657a`)**：先按 `"@"` 截断；若子串含 `"xcd"`（不区分大小写）则查询 22 款 XCD 镜头映射表，未命中回退 `"Third Party Lens"`；非 XCD 且非空/`"unknown"` 时保留原镜头名，否则为 `"Third Party Lens"`。
* **日期 (`m0.a`)**：结合原图 `OffsetTimeOriginal` / `OffsetTime` 偏移量，将时间戳格式化为 `"yyyy MM dd"`；无时间时为空串。

---

## 7. HDR Gainmap 合成规则 (`H0.k` L681–779)

当输入图携带 API 34+ Gainmap（分辨率缩放比 $g = W_{\text{gm}} / W$）时：

1. 将原图 Gainmap 贴入边框 `hollowRect` 缩放置换位置 $(g \cdot \text{pad}_L,\ g \cdot \text{pad}_T)$。
2. 将完整 SDR 水印+外框掩码图层用 `PorterDuff.Mode.SRC_IN` 染为单色 `(Q_rect, Q_rect, Q_rect)`（`Q_rect = clamp(gainColorRect, 0, 255)`，Style 12/15 为 `128`，其余为 `159`）后缩放到 Gainmap 尺寸并叠绘。
3. 遍历视图树，跳过所有 `android:tag="exclude"` 的节点（Style 1/4 `sub_text`、Style 13/14 `icon_logo_back`、Style 15 `FilmFrameGradientView`），对剩余前景节点执行：
   * 若 `m.l() == True`（Style 12/13/14/15）：以 `Q_text = gainColor (170)` 执行 `SRC_IN` 染色叠绘。
   * 若 `m.l() == False`：应用颜色矩阵 `R'=G'=B'=0, A'=clamp(A - (0.299R + 0.587G + 0.114B), 0, 255)` 叠绘（黑字变暗压低 Gainmap，纯白字变透明透出底层图片亮度）。
4. 若启用签名，将其染为 `Color.rgb(0.625f, 0.625f, 0.625f)` (`#9F9F9F`) 按比例叠加于 Gainmap 对应位置。
