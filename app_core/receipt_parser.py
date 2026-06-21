"""小票文本解析。

本模块负责把打印 bytes 解码成可读文本，并剥离常见 ESC/POS 控制码。
它只处理 text-only 小票，不负责订单字段结构化。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class ReceiptText:
    """一次小票文本解析结果。"""

    text: str
    encoding: str
    warnings: list[str]


CONTROL_PATTERNS = [
    re.compile(r"^[\x00-\x1f\x7f-\x9f]+"),
    re.compile(r"^2�\s*"),
    re.compile(r"^!+"),
    re.compile(r"^-+>"),
]


def parse_receipt_text(data: bytes) -> ReceiptText:
    """将打印 bytes 解析成文本。

    Args:
        data: 收到的原始打印 bytes。

    Returns:
        包含文本、编码和警告的解析结果。解析失败时返回替代文本。
    """
    warnings: list[str] = []
    encoding, text = _decode_best_effort(data)
    text = _strip_escpos_controls(text)
    text = _normalize_lines(text)
    if not text:
        warnings.append("未解析出可读文本。")
        text = "[无可读文本]"
    return ReceiptText(text=text, encoding=encoding, warnings=warnings)


def _decode_best_effort(data: bytes) -> tuple[str, str]:
    """按常见小票编码顺序尝试解码。"""
    for encoding in ("gbk", "gb2312", "utf-8"):
        try:
            return encoding, data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return "gbk-replace", data.decode("gbk", errors="replace")


def _strip_escpos_controls(text: str) -> str:
    """移除常见控制字符，保留真正的中文和 ASCII 文本。"""
    cleaned = []
    for char in text:
        code = ord(char)
        if char in "\n\r\t":
            cleaned.append(char)
        elif code >= 32 and code != 127:
            cleaned.append(char)
    result = "".join(cleaned)
    for pattern in CONTROL_PATTERNS:
        result = pattern.sub("", result)
    return result


def _normalize_lines(text: str) -> str:
    """规范换行、空白和银豹常见打印前缀。"""
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        for pattern in CONTROL_PATTERNS:
            line = pattern.sub("", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)

