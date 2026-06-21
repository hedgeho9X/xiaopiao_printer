"""ID 与时间工具。

本模块负责生成数据库主键和统一时间字符串，避免各模块自行拼接 ID。
它不操作打印机、LLM、TTS 或 UI。
"""

from __future__ import annotations

import datetime as dt
import uuid


def now_iso() -> str:
    """返回当前本地时间的 ISO 8601 字符串。

    Returns:
        精确到秒的本地时间字符串，适合写入 SQLite 文本字段。
    """
    return dt.datetime.now().isoformat(timespec="seconds")


def make_id(prefix: str) -> str:
    """生成带业务前缀的短 ID。

    Args:
        prefix: ID 前缀，例如 ``job``、``ord``、``item``。

    Returns:
        形如 ``job_20260620_153012_a1b2c3d4`` 的字符串。
    """
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"

