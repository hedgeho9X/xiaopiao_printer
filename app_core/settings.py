"""应用配置读写。

本模块封装 app_settings 表，负责打印方式到平台的映射、TTS 语速、
UI 主题等本地配置。它不直接操作 UI、打印机或 TTS。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .ids import now_iso


DEFAULT_SHORTCUTS: dict[str, str] = {
    "read_full": "1",
    "read_overview": "2",
    "read_items": "3",
    "previous_item": "4",
    "next_item": "5",
    "previous_order": "6",
    "next_order": "7",
    "complete_order": "8",
    "stop_speaking": "9",
    "cycle_rate": "0",
    "undo_complete": "Ctrl+Z",
    "increase_type_scale": "Ctrl+Plus",
    "decrease_type_scale": "Ctrl+Minus",
}

DEFAULT_SETTINGS: dict[str, str] = {
    "schema_version": "1",
    "network_print_platform": "yinbao",
    "usb_print_platform": "meituan",
    "network_print_port": "9100",
    "usb_print_port": "9101",
    "active_print_method": "usb_print",
    "target_printer_name": "",
    "proxy_printer_name": "Receipt Voice Proxy",
    "tts_voice": "",
    "tts_rate": "medium",
    "ui_theme": "light",
    "ui_type_scale": "normal",
    "shortcuts": json.dumps(DEFAULT_SHORTCUTS, ensure_ascii=False),
    "llm_enabled": "0",
    "llm_provider_name": "OpenAI Compatible",
    "llm_base_url": "https://api.openai.com/v1",
    "llm_api_key": "",
    "llm_model": "",
}

PRINT_METHOD_LABELS = {
    "network_print": "网口打印",
    "usb_print": "USB 打印",
}

RATE_ORDER = ["slow", "medium", "fast", "faster", "fastest"]


class SettingsStore:
    """app_settings 表的读写入口。"""

    def __init__(self, conn: sqlite3.Connection):
        """保存 SQLite 连接。"""
        self.conn = conn

    def initialize_defaults(self) -> None:
        """写入缺失的默认配置，不覆盖用户已有设置。"""
        now = now_iso()
        for key, value in DEFAULT_SETTINGS.items():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, value, now),
            )
        self.conn.commit()

    def get(self, key: str, default: str = "") -> str:
        """读取单个配置值。"""
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else default

    def set(self, key: str, value: str) -> None:
        """写入单个配置值。"""
        self.conn.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              updated_at = excluded.updated_at
            """,
            (key, value, now_iso()),
        )
        self.conn.commit()

    def get_all(self) -> dict[str, str]:
        """返回所有配置，供 bridge 传给 UI。"""
        rows = self.conn.execute(
            "SELECT key, value FROM app_settings ORDER BY key"
        ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def save_many(self, values: dict[str, Any]) -> dict[str, str]:
        """批量保存配置，并把复杂值转换成 JSON 字符串。"""
        for key, value in values.items():
            if key == "shortcuts":
                value = self._normalize_shortcuts(value)
            if isinstance(value, (dict, list)):
                self.set(key, json.dumps(value, ensure_ascii=False))
            else:
                self.set(key, str(value))
        return self.get_all()

    def platform_for_method(self, print_method: str) -> str:
        """根据打印方式读取对应平台。"""
        if print_method == "network_print":
            return self.get("network_print_platform", "yinbao")
        if print_method == "usb_print":
            return self.get("usb_print_platform", "meituan")
        return "unknown"

    def cycle_rate(self) -> str:
        """在五档语速之间循环，并返回新档位。"""
        current = self.get("tts_rate", "medium")
        try:
            index = RATE_ORDER.index(current)
        except ValueError:
            index = 1
        next_rate = RATE_ORDER[(index + 1) % len(RATE_ORDER)]
        self.set("tts_rate", next_rate)
        return next_rate

    @staticmethod
    def _normalize_shortcuts(value: Any) -> dict[str, str] | str:
        """校验快捷键配置，避免非 JSON 对象写入设置。"""
        if isinstance(value, dict):
            return {str(key): str(shortcut) for key, shortcut in value.items()}
        if isinstance(value, str):
            payload = json.loads(value)
            if not isinstance(payload, dict):
                raise ValueError("shortcuts 必须是 JSON 对象。")
            return {str(key): str(shortcut) for key, shortcut in payload.items()}
        raise ValueError("shortcuts 必须是 JSON 对象。")
