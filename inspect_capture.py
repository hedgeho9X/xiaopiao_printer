"""小票抓包解析与预览生成模块。

这个模块只读取抓到的原始 bytes，不负责监听和转发。它的核心职责是：
1. 判断抓包属于 ESC/POS 文本、ESC/POS 位图，还是银豹 XPS 文档包。
2. 从文本流中剥离 ESC/POS 控制码，得到适合播报/调试的文本。
3. 从 XPS 文档包中提取 Glyphs 的 UnicodeString。
4. 将位图或文本生成 preview.png，方便人工确认抓包内容。
"""

import argparse
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont


CONTROL_NAMES = {
    b"\x1b@": "[ESC/POS INIT]",
    b"\x1ba\x00": "[ALIGN LEFT]",
    b"\x1ba\x01": "[ALIGN CENTER]",
    b"\x1ba\x02": "[ALIGN RIGHT]",
    b"\x1bE\x00": "[BOLD OFF]",
    b"\x1bE\x01": "[BOLD ON]",
    b"\x1dV\x00": "[CUT]",
    b"\x1dV\x01": "[CUT]",
    b"\x1dV\x41": "[CUT]",
    b"\x1dV\x42": "[CUT]",
}


@dataclass
class ParsedCapture:
    """一次抓包解析后的结构化结果。"""

    markers: list[str]
    text: str
    preview_lines: list[str]
    bitmap_count: int
    bitmap_images: list[dict]
    content_kind: str
    package_entries: list[str]


@dataclass
class RasterImage:
    """ESC/POS 光栅位图片段。

    ESC/POS 的 GS v 0 命令按“每行多少字节”和“多少行”描述图片。
    每个字节代表 8 个横向像素，因此最终宽度是 width_bytes * 8。
    """

    width_bytes: int
    height: int
    mode: int
    data: bytes

    @property
    def width_pixels(self) -> int:
        """返回位图横向像素数。"""
        return self.width_bytes * 8


def hex_summary(data: bytes, limit: int = 256) -> str:
    """生成原始 bytes 的十六进制摘要。

    Args:
        data: 原始打印数据。
        limit: 最多展示多少个字节，避免日志过长。
    """
    shown = data[:limit]
    hexed = " ".join(f"{byte:02X}" for byte in shown)
    if len(data) > limit:
        hexed += f" ... ({len(data) - limit} more bytes)"
    return hexed


def is_zip_package(data: bytes) -> bool:
    """判断数据是否是 ZIP/XPS 文档包。

    银豹的驱动打印路径会产生 XPS/FixedDocument 包，它本质上是 ZIP。
    """
    return data.startswith(b"PK\x03\x04")


def list_zip_entries(data: bytes, limit: int = 50) -> list[str]:
    """列出 ZIP/XPS 包内文件名。

    Args:
        data: ZIP/XPS 原始 bytes。
        limit: 最多返回的文件数量。
    """
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            return archive.namelist()[:limit]
    except zipfile.BadZipFile:
        return []


def extract_xps_text(data: bytes) -> str:
    """从银豹 XPS/FixedDocument 包中提取文本。

    XPS 页面中的文字通常存放在 ``Glyphs`` 元素的 ``UnicodeString`` 属性里。
    这里按页面坐标 OriginY/OriginX 重新排序，尽量还原小票上的阅读顺序。
    """
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            page_names = sorted(
                name
                for name in archive.namelist()
                if name.lower().endswith(".fpage") or "/pages/" in name.lower()
            )
            # 保存为 (y, x, text)，后面按坐标恢复页面上的行顺序。
            glyphs: list[tuple[float, float, str]] = []
            for page_name in page_names:
                try:
                    root = ElementTree.fromstring(archive.read(page_name))
                except ElementTree.ParseError:
                    continue
                for element in root.iter():
                    if element.tag.rsplit("}", 1)[-1] != "Glyphs":
                        continue
                    text = element.attrib.get("UnicodeString", "")
                    if not text:
                        continue
                    try:
                        x = float(element.attrib.get("OriginX", "0"))
                        y = float(element.attrib.get("OriginY", "0"))
                    except ValueError:
                        x, y = 0.0, 0.0
                    glyphs.append((y, x, text))
    except zipfile.BadZipFile:
        return ""

    if not glyphs:
        return ""

    # 同一行的 Y 坐标可能有很小偏差，先粗略归并再按 X 排序。
    glyphs.sort(key=lambda item: (round(item[0] / 3) * 3, item[1]))
    lines: list[list[tuple[float, str]]] = []
    current_y: float | None = None
    current_line: list[tuple[float, str]] = []
    tolerance = 4.0

    for y, x, text in glyphs:
        if current_y is None or abs(y - current_y) <= tolerance:
            current_line.append((x, text))
            current_y = y if current_y is None else current_y
        else:
            lines.append(current_line)
            current_line = [(x, text)]
            current_y = y
    if current_line:
        lines.append(current_line)

    rendered_lines: list[str] = []
    for line in lines:
        pieces = [text for _x, text in sorted(line, key=lambda item: item[0])]
        rendered = "".join(pieces).strip()
        if rendered:
            rendered_lines.append(rendered)
    return "\n".join(rendered_lines)


def decode_text(data: bytes) -> tuple[str, str]:
    """从 ESC/POS 文本流中解码可读文字。

    先剥离控制码，再分别按 GBK 和 UTF-8 尝试解码，选择替换字符最少的结果。
    """
    cleaned = strip_escpos_controls(data)
    candidates: list[tuple[str, str, int]] = []
    for encoding in ("gbk", "utf-8"):
        decoded = cleaned.decode(encoding, errors="replace")
        replacement_count = decoded.count("\ufffd")
        candidates.append((encoding, decoded, replacement_count))

    encoding, decoded, _ = min(candidates, key=lambda item: item[2])
    decoded = normalize_text(decoded)
    return encoding, decoded


def strip_escpos_controls(data: bytes) -> bytes:
    """剥离常见 ESC/POS 控制指令，只保留可能的文字 bytes。

    注意：这个函数只用于解析文本，不会影响转发给真实小票机的原始数据。
    """
    output = bytearray()
    i = 0

    while i < len(data):
        byte = data[i]

        if byte == 0x1B:  # ESC，常见打印机控制命令前缀。
            if i + 1 >= len(data):
                i += 1
            elif data[i + 1] in (0x40,):
                i += 2
            elif data[i + 1] in (0x4A, 0x64, 0x61, 0x45, 0x21, 0x74):
                i += 3
            elif data[i + 1] == 0x70:
                # ESC p m t1 t2：开钱箱/蜂鸣脉冲，银豹 IP 打印开头会带这个。
                i += 5
            elif data[i + 1] == 0x2A:
                if i + 4 < len(data):
                    width = data[i + 3] + data[i + 4] * 256
                    i += 5 + width
                else:
                    i = len(data)
            else:
                i += 2
            continue

        if byte == 0x1C:  # FS，中文字符集/中文字体样式相关命令。
            if i + 1 >= len(data):
                i += 1
            elif data[i + 1] in (0x21, 0x2D, 0x43, 0x57):
                i += 3
            elif data[i + 1] in (0x26, 0x2E):
                i += 2
            else:
                i += 2
            continue

        if byte == 0x1D:  # GS，切纸、二维码、光栅位图等命令常用前缀。
            if i + 1 >= len(data):
                i += 1
            elif data[i + 1] == 0x56:
                i += 4 if i + 2 < len(data) and data[i + 2] in (0x41, 0x42) else 3
            elif data[i + 1] == 0x76 and i + 7 < len(data) and data[i + 2] == 0x30:
                width = data[i + 4] + data[i + 5] * 256
                height = data[i + 6] + data[i + 7] * 256
                i += 8 + width * height
            elif data[i + 1] == 0x28 and i + 4 < len(data):
                length = data[i + 3] + data[i + 4] * 256
                i += 5 + length
            else:
                i += 2
            continue

        if byte == 0x10 and i + 1 < len(data):  # DLE realtime/status commands.
            i += 3 if i + 2 < len(data) else 2
            continue

        if byte in (0x00, 0x07, 0x0D):
            i += 1
            continue

        output.append(byte)
        i += 1

    return bytes(output)


def normalize_text(text: str) -> str:
    """清理解码后的文本。

    主要处理换行、不可见控制字符，以及银豹 IP 打印开头残留的符号前缀。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    lines = [line.rstrip() for line in text.split("\n")]
    lines = [line for line in lines if line.strip()]
    if lines:
        lines[0] = re.sub(r"^[\-=>!\ufffd\s]+(?=[\u4e00-\u9fff])", "", lines[0]).strip()
    return "\n".join(lines)


def is_noise_text(text: str) -> bool:
    """判断一段文本是否像误解码噪声。

    位图数据如果被强行按 GBK/UTF-8 解码，会出现大量替换字符或无意义符号。
    """
    if not text:
        return True
    if "\ufffd" in text:
        return True
    meaningful = sum(1 for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    return meaningful < max(2, len(text) // 5)


def extract_raster_images(data: bytes) -> list[RasterImage]:
    """提取 ESC/POS GS v 0 光栅位图片段。"""
    images: list[RasterImage] = []
    i = 0
    while i < len(data):
        if data.startswith(b"\x1dv0", i) and i + 7 < len(data):
            mode = data[i + 3]
            width_bytes = data[i + 4] + data[i + 5] * 256
            height = data[i + 6] + data[i + 7] * 256
            size = width_bytes * height
            start = i + 8
            end = start + size
            if width_bytes > 0 and height > 0 and end <= len(data):
                images.append(RasterImage(width_bytes, height, mode, data[start:end]))
                i = end
                continue
        i += 1
    return images


def parse_markers(data: bytes) -> list[str]:
    """识别抓包中出现过的关键打印控制标记。"""
    if is_zip_package(data):
        return ["[DOCUMENT PACKAGE: ZIP/XPS]"]

    markers: list[str] = []
    i = 0
    while i < len(data):
        matched = False
        for pattern, name in CONTROL_NAMES.items():
            if data.startswith(pattern, i):
                markers.append(name)
                i += len(pattern)
                matched = True
                break
        if matched:
            continue

        if data.startswith(b"\x1dv0", i):
            markers.append("[IMAGE/QR/BITMAP: GS v 0 raster image]")
            i += 3
            continue

        if data.startswith(b"\x1b*", i):
            markers.append("[IMAGE/QR/BITMAP: ESC * bit image]")
            i += 2
            continue

        if data.startswith(b"\x1d(k", i):
            markers.append("[IMAGE/QR/BITMAP: QR or 2D code command]")
            i += 3
            continue

        i += 1

    deduped: list[str] = []
    for marker in markers:
        if not deduped or deduped[-1] != marker:
            deduped.append(marker)
    return deduped


def parse_capture(data: bytes) -> tuple[str, ParsedCapture]:
    """解析一份完整抓包。

    Returns:
        二元组 ``(encoding, parsed)``。encoding 表示文本来源/编码，
        parsed 是结构化解析结果。
    """
    if is_zip_package(data):
        entries = list_zip_entries(data)
        xps_text = extract_xps_text(data)
        lines = [
            "[银豹/XPS 文档包打印数据]",
            "原始 .bin 已完整保存，文字从 XPS Glyphs 中提取。",
        ]
        if xps_text:
            lines.extend(xps_text.splitlines())
        if entries:
            lines.append("包内文件：")
            lines.extend(entries[:12])
        return "xps", ParsedCapture(
            markers=["[DOCUMENT PACKAGE: ZIP/XPS]"],
            text=xps_text,
            preview_lines=lines,
            bitmap_count=0,
            bitmap_images=[],
            content_kind="xps_document",
            package_entries=entries,
        )

    encoding, text = decode_text(data)
    markers = parse_markers(data)
    raster_images = extract_raster_images(data)
    if raster_images and is_noise_text(text):
        text = ""

    if raster_images and text:
        content_kind = "mixed"
    elif raster_images:
        content_kind = "bitmap"
    else:
        content_kind = "text"

    preview_lines = []

    for marker in markers:
        if "IMAGE/QR/BITMAP" in marker:
            preview_lines.append(marker)

    preview_lines.extend(text.splitlines() if text else ["[NO READABLE TEXT]"])
    bitmap_images = [
        {
            "width_pixels": image.width_pixels,
            "width_bytes": image.width_bytes,
            "height": image.height,
            "mode": image.mode,
            "byte_count": len(image.data),
        }
        for image in raster_images
    ]
    return encoding, ParsedCapture(
        markers=markers,
        text=text,
        preview_lines=preview_lines,
        bitmap_count=len(raster_images),
        bitmap_images=bitmap_images,
        content_kind=content_kind,
        package_entries=[],
    )


def find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """选择一个能显示中文的系统字体。"""
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def make_preview(lines: list[str], output_path: Path) -> None:
    """把文本行渲染成简单预览图。"""
    font = find_font(24)
    padding = 24
    line_height = 34
    width = 576
    height = max(180, padding * 2 + line_height * len(lines))

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    y = padding
    for line in lines:
        if "IMAGE/QR/BITMAP" in line:
            draw.rectangle((padding, y, width - padding, y + line_height), outline="black")
            draw.text((padding + 8, y + 4), line, fill="black", font=font)
        else:
            draw.text((padding, y), line, fill="black", font=font)
        y += line_height

    image.save(output_path)


def raster_to_image(raster: RasterImage) -> Image.Image:
    """把 ESC/POS 光栅位图片段转换为 Pillow 图片。"""
    image = Image.new("1", (raster.width_pixels, raster.height), 1)
    pixels = image.load()
    for y in range(raster.height):
        row_start = y * raster.width_bytes
        for byte_index in range(raster.width_bytes):
            value = raster.data[row_start + byte_index]
            for bit in range(8):
                x = byte_index * 8 + bit
                pixels[x, y] = 0 if value & (0x80 >> bit) else 1
    return image.convert("RGB")


def make_capture_preview(data: bytes, lines: list[str], output_path: Path) -> None:
    """根据抓包类型生成预览图。

    如果抓包中有 ESC/POS 位图，则优先拼接位图；否则按文本渲染。
    """
    rasters = extract_raster_images(data)
    if not rasters:
        make_preview(lines, output_path)
        return

    padding = 24
    gap = 0
    rendered = [raster_to_image(raster) for raster in rasters]
    width = max(576, max(image.width for image in rendered) + padding * 2)
    height = padding * 2 + sum(image.height + gap for image in rendered)
    canvas = Image.new("RGB", (width, max(180, height)), "white")

    y = padding
    for image in rendered:
        canvas.paste(image, (padding, y))
        y += image.height + gap

    canvas.save(output_path)


def main() -> int:
    """命令行入口：解析一个 .bin 文件并生成预览。"""
    parser = argparse.ArgumentParser(description="Inspect captured ESC/POS bytes.")
    parser.add_argument("capture", type=Path, help="Path to a .bin capture file.")
    args = parser.parse_args()

    data = args.capture.read_bytes()
    encoding, parsed = parse_capture(data)
    preview_path = args.capture.with_suffix(".preview.png")
    make_capture_preview(data, parsed.preview_lines, preview_path)

    print(f"File: {args.capture}")
    print(f"Size: {len(data)} bytes")
    print("\nHex summary:")
    print(hex_summary(data))
    print(f"\nDecoded text ({encoding}):")
    print(parsed.text or "[NO READABLE TEXT]")
    print(f"\nContent kind: {parsed.content_kind}")
    print(f"Bitmap images: {parsed.bitmap_count}")
    print("\nESC/POS markers:")
    if parsed.markers:
        for marker in parsed.markers:
            print(f"- {marker}")
    else:
        print("- [NO KNOWN MARKERS]")
    print(f"\nPreview: {preview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
