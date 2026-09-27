"""从 SVG 生成多尺寸 ICO。

不需要 Pillow：PNG 与 ICO 的读写都在这里用标准库实现（zlib + struct），
渲染交给本机已有的无头 Chrome。

**为什么是"渲染一张大图再降采样"，而不是按每个尺寸分别截图**：
Chrome 在 Windows 上对窗口尺寸有下限，`--screenshot --window-size=48,48`
截出来的是全透明的空图（实测小尺寸 0% 不透明像素）。渲染一张 512 的大图
再自己做面积平均，既能得到正确的小图标，抗锯齿效果也比整数缩放更好。

用法：
    .venv/Scripts/python.exe packaging/make_icon.py
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 通过 HTML 包一层：直接打开 SVG 文件时 Chrome 按固有尺寸渲染文档，
# 窗口小的话只会截到左上角。
RENDER_PAGE = HERE / "icon-render.html"
OUT = HERE / "app.ico"
PROFILE = HERE / "_chromeprof"

#: 母图尺寸。要够大，降采样到 256 时才不留锯齿。
SOURCE_SIZE = 512
#: Windows 在不同场合取不同尺寸：标题栏 16、任务栏 32、资源管理器 48/256
TARGET_SIZES = [16, 32, 48, 64, 128, 256]

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------- 渲染
def find_browser() -> str:
    for path in BROWSERS:
        if Path(path).is_file():
            return path
    raise SystemExit("找不到 Chrome 或 Edge，无法渲染图标")


def render_source(browser: str) -> bytes:
    png = HERE / "_source.png"
    if png.exists():
        png.unlink()
    subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            f"--user-data-dir={PROFILE}",
            f"--window-size={SOURCE_SIZE},{SOURCE_SIZE}",
            # 透明背景，否则圆角外面会带一圈白底
            "--default-background-color=00000000",
            f"--screenshot={png}",
            RENDER_PAGE.as_uri(),
        ],
        capture_output=True,
        timeout=180,
        check=False,
    )
    if not png.exists():
        raise SystemExit("渲染失败：浏览器没有产出文件")
    data = png.read_bytes()
    png.unlink()
    if not data.startswith(PNG_MAGIC):
        raise SystemExit("渲染失败：产出的不是 PNG")
    return data


# ----------------------------------------------------------------- PNG 解码
def decode_png(data: bytes) -> tuple[int, int, list[bytearray]]:
    """解出 RGBA 像素行。只支持 8 位 RGBA / RGB，够本例用。"""
    pos = 8
    idat = b""
    width = height = 0
    color_type = 0
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        if chunk_type == b"IHDR":
            width, height, _depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif chunk_type == b"IDAT":
            idat += chunk
        elif chunk_type == b"IEND":
            break
        pos += 12 + length

    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * channels
    raw = zlib.decompress(idat)

    rows: list[bytearray] = []
    prev = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        # PNG 的逐行反过滤
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if filter_type == 1:
                line[x] = (line[x] + a) & 0xFF
            elif filter_type == 2:
                line[x] = (line[x] + b) & 0xFF
            elif filter_type == 3:
                line[x] = (line[x] + (a + b) // 2) & 0xFF
            elif filter_type == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 0xFF
        rows.append(line)
        prev = line

    # 统一成 RGBA
    if channels != 4:
        rgba_rows = []
        for line in rows:
            out = bytearray(width * 4)
            for i in range(width):
                out[i * 4 : i * 4 + 3] = line[i * channels : i * channels + 3]
                out[i * 4 + 3] = 255
            rgba_rows.append(out)
        rows = rgba_rows
    return width, height, rows


# ----------------------------------------------------------------- PNG 编码
def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, rows: list[bytearray]) -> bytes:
    raw = b"".join(b"\x00" + bytes(r) for r in rows)  # 每行过滤器都用 0（None）
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        PNG_MAGIC
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


# ------------------------------------------------------------------- 降采样
def downscale(
    src_w: int, src_h: int, src_rows: list[bytearray], target: int
) -> list[bytearray]:
    """面积平均降采样。

    每个目标像素覆盖源图的一块矩形，取该矩形内像素的加权平均 ——
    比"取最近的像素"平滑得多，小尺寸下不会丢掉星形。
    按整数边界切分，边缘不足一格时按实际覆盖面积加权。
    """
    out_rows: list[bytearray] = []
    scale = src_w / target
    for ty in range(target):
        y0, y1 = ty * scale, (ty + 1) * scale
        row = bytearray(target * 4)
        for tx in range(target):
            x0, x1 = tx * scale, (tx + 1) * scale
            acc = [0.0, 0.0, 0.0, 0.0]
            total = 0.0
            for sy in range(int(y0), min(int(y1) + 1, src_h)):
                wy = min(sy + 1, y1) - max(sy, y0)
                if wy <= 0:
                    continue
                src = src_rows[sy]
                for sx in range(int(x0), min(int(x1) + 1, src_w)):
                    wx = min(sx + 1, x1) - max(sx, x0)
                    if wx <= 0:
                        continue
                    weight = wx * wy
                    base = sx * 4
                    # 预乘 alpha 再加权，否则透明区域的杂色会渗进边缘
                    alpha = src[base + 3] / 255.0
                    acc[0] += src[base] * alpha * weight
                    acc[1] += src[base + 1] * alpha * weight
                    acc[2] += src[base + 2] * alpha * weight
                    acc[3] += src[base + 3] * weight
                    total += weight
            if total <= 0:
                continue
            a = acc[3] / total
            if a <= 0.5:
                continue  # 完全透明
            inv = 255.0 / acc[3]
            row[tx * 4] = min(255, int(acc[0] * inv + 0.5))
            row[tx * 4 + 1] = min(255, int(acc[1] * inv + 0.5))
            row[tx * 4 + 2] = min(255, int(acc[2] * inv + 0.5))
            row[tx * 4 + 3] = min(255, int(a + 0.5))
        out_rows.append(row)
    return out_rows


# ------------------------------------------------------------------- 组装
def build_ico(images: list[tuple[int, bytes]], out: Path) -> None:
    header = struct.pack("<HHH", 0, 1, len(images))  # reserved, type=icon, count
    offset = 6 + 16 * len(images)
    entries = b""
    blobs = b""
    for size, data in images:
        dim = 0 if size >= 256 else size  # 目录项宽高各 1 字节，256 记 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out.write_bytes(header + entries + blobs)


def main() -> int:
    browser = find_browser()
    print(f"  浏览器: {browser}")

    src = render_source(browser)
    src_w, src_h, src_rows = decode_png(src)
    opaque = sum(1 for r in src_rows for x in range(0, len(r), 4) if r[x + 3] > 10)
    print(f"  母图: {src_w}x{src_h}, 不透明像素 {opaque} ({100 * opaque / (src_w * src_h):.1f}%)")
    if opaque == 0:
        raise SystemExit("母图全透明，渲染有问题")

    images: list[tuple[int, bytes]] = []
    for size in TARGET_SIZES:
        rows = downscale(src_w, src_h, src_rows, size)
        png = encode_png(size, size, rows)
        filled = sum(1 for r in rows for x in range(0, len(r), 4) if r[x + 3] > 10)
        print(f"  {size:>3}px -> {len(png):>6} 字节, 不透明像素 {filled}/{size * size}")
        if filled == 0:
            raise SystemExit(f"{size}px 降采样后全透明，尺寸换算有问题")
        images.append((size, png))

    build_ico(images, OUT)
    print(f"\n  已生成 {OUT.name}（{len(images)} 个尺寸，{OUT.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    shutil.rmtree(PROFILE, ignore_errors=True)
    code = main()
    shutil.rmtree(PROFILE, ignore_errors=True)
    sys.exit(code)
