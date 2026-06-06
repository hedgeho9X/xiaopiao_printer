import argparse
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

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
    markers: list[str]
    text: str
    preview_lines: list[str]
    bitmap_count: int
    bitmap_images: list[dict]
    content_kind: str
    package_entries: list[str]


@dataclass
class RasterImage:
    width_bytes: int
    height: int
    mode: int
    data: bytes

    @property
    def width_pixels(self) -> int:
        return self.width_bytes * 8


def hex_summary(data: bytes, limit: int = 256) -> str:
    shown = data[:limit]
    hexed = " ".join(f"{byte:02X}" for byte in shown)
    if len(data) > limit:
        hexed += f" ... ({len(data) - limit} more bytes)"
    return hexed


def is_zip_package(data: bytes) -> bool:
    return data.startswith(b"PK\x03\x04")


def list_zip_entries(data: bytes, limit: int = 50) -> list[str]:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            return archive.namelist()[:limit]
    except zipfile.BadZipFile:
        return []


def decode_text(data: bytes) -> tuple[str, str]:
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
    output = bytearray()
    i = 0

    while i < len(data):
        byte = data[i]

        if byte == 0x1B:  # ESC
            if i + 1 >= len(data):
                i += 1
            elif data[i + 1] in (0x40,):
                i += 2
            elif data[i + 1] in (0x61, 0x45, 0x21, 0x74):
                i += 3
            elif data[i + 1] == 0x2A:
                if i + 4 < len(data):
                    width = data[i + 3] + data[i + 4] * 256
                    i += 5 + width
                else:
                    i = len(data)
            else:
                i += 2
            continue

        if byte == 0x1D:  # GS
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
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line.strip())


def is_noise_text(text: str) -> bool:
    if not text:
        return True
    if "\ufffd" in text:
        return True
    meaningful = sum(1 for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    return meaningful < max(2, len(text) // 5)


def extract_raster_images(data: bytes) -> list[RasterImage]:
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
    if is_zip_package(data):
        entries = list_zip_entries(data)
        lines = [
            "[文档包/疑似 XPS 打印数据]",
            "这不是 ESC/POS 文本小票，原始 .bin 已完整保存。",
            "请优先让收银软件选择 Receipt Voice Proxy，而不是系统测试页或 XPS/PDF 类打印路径。",
        ]
        if entries:
            lines.append("包内文件：")
            lines.extend(entries[:12])
        return "binary", ParsedCapture(
            markers=["[DOCUMENT PACKAGE: ZIP/XPS]"],
            text="",
            preview_lines=lines,
            bitmap_count=0,
            bitmap_images=[],
            content_kind="document_package",
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
