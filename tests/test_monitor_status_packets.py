"""ESC/POS 状态查询包处理测试。

银豹会定时发送 ``DLE EOT`` 状态查询包，这类 bytes 不是小票内容。
本文件验证它们不会污染订单数据库，也能生成兼容 POS 的在线响应。
"""

from __future__ import annotations

import sqlite3
import unittest

from app_core.database import initialize
from app_core.models import OrderDraft
from app_core.monitor import (
    ReceiptMonitor,
    build_escpos_status_response,
    is_escpos_realtime_status_request,
)
from app_core.order_repair import repair_legacy_orders
from app_core.order_store import OrderStore
from app_core.settings import SettingsStore


class MonitorStatusPacketTest(unittest.TestCase):
    """验证 ESC/POS 状态查询包不会被当成订单处理。"""

    def setUp(self) -> None:
        """创建内存数据库和核心服务对象。"""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        initialize(self.conn)
        self.store = OrderStore(self.conn)
        self.settings = SettingsStore(self.conn)
        self.settings.initialize_defaults()

    def test_status_request_is_not_saved_as_print_job(self) -> None:
        """实时状态查询包不创建 print_job，也不创建未解析订单。"""
        monitor = ReceiptMonitor(self.settings, self.store, print_method="network_print")

        order_id = monitor.handle_connection_bytes(b"\x10\x04\x02")

        self.assertIsNone(order_id)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_status_request_response_marks_printer_ready(self) -> None:
        """状态查询会返回 ESC/POS 常用的在线状态字节。"""
        self.assertTrue(is_escpos_realtime_status_request(b"\x10\x04\x02"))
        self.assertEqual(build_escpos_status_response(b"\x10\x04\x02"), b"\x12")
        self.assertEqual(build_escpos_status_response(b"\x10\x04\x01\x10\x04\x04"), b"\x12\x12")
        self.assertFalse(is_escpos_realtime_status_request(b"\x1b\x40" + "真实小票".encode("gbk")))

    def test_pos_printer_test_page_is_not_created_as_order(self) -> None:
        """POS 打印机测试页会保存 print_job，但不会进入待制作订单。"""
        monitor = ReceiptMonitor(self.settings, self.store, print_method="usb_print")
        text = "美团打印机测试页\nReceipt Voice Proxy\n宽度：32个字符\n1列无宽"

        order_id = monitor.handle_bytes(text.encode("gbk", errors="replace"))

        self.assertIsNone(order_id)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_repair_deletes_legacy_status_query_orders(self) -> None:
        """旧版本误创建的状态查询伪订单会在启动修复时删除。"""
        job_id = self.store.create_print_job(
            print_method="network_print",
            platform="yinbao",
            raw_bytes=b"\x10\x04\x02",
            raw_text="[无可读文本]",
        )
        self.store.upsert_order_from_draft(
            job_id=job_id,
            platform="yinbao",
            raw_text="[无可读文本]",
            draft=OrderDraft(platform_order_no=None, display_no="未解析订单"),
        )

        repaired = repair_legacy_orders(self.store)

        self.assertGreaterEqual(repaired, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
