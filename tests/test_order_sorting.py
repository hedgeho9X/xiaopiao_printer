"""订单时间排序回归测试。

本文件专门验证待制作和历史订单的时间排序，避免 SQLite 字符串排序把不同格式的时间排错。
"""

from __future__ import annotations

import sqlite3
import unittest

from app_core.database import initialize
from app_core.order_store import OrderStore, normalize_order_time


class OrderSortingTest(unittest.TestCase):
    """订单队列排序测试。"""

    def setUp(self) -> None:
        """创建内存数据库和订单仓库。"""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        initialize(self.conn)
        self.store = OrderStore(self.conn)

    def test_pending_orders_sort_iso_t_fallback_after_business_time(self) -> None:
        """有 T 分隔符的 created_at 不能排到更晚的业务时间前面。"""
        self._insert_order(
            order_id="ord_unparsed",
            display_no="未解析订单",
            order_time=None,
            created_at="2026-07-12T13:33:09",
        )
        self._insert_order(
            order_id="ord_newer",
            display_no="银豹 柱子 Z1",
            order_time="2026-07-12 13:48:52",
            created_at="2026-07-12T13:49:01",
        )

        display_numbers = [order.display_no for order in self.store.get_pending_orders()]

        self.assertEqual(display_numbers, ["银豹 柱子 Z1", "未解析订单"])

    def test_pending_orders_fill_missing_year_from_created_at(self) -> None:
        """缺年份的小票时间会使用创建时间的年份再参与倒序排序。"""
        self._insert_order(
            order_id="ord_old",
            display_no="未解析订单",
            order_time="2026-07-12 13:33:09",
            created_at="2026-07-12T13:33:09",
        )
        self._insert_order(
            order_id="ord_new",
            display_no="银豹 柱子 Z1",
            order_time="07-12 13:48",
            created_at="2026-07-12T13:49:01",
        )

        display_numbers = [order.display_no for order in self.store.get_pending_orders()]

        self.assertEqual(display_numbers, ["银豹 柱子 Z1", "未解析订单"])
        self.assertEqual(normalize_order_time("07-12 13:48", "2026-07-12T13:49:01"), "2026-07-12 13:48:00")

    def _insert_order(self, *, order_id: str, display_no: str, order_time: str | None, created_at: str) -> None:
        """插入一条最小订单记录。"""
        self.conn.execute(
            """
            INSERT INTO orders(
              id, dedupe_key, created_at, updated_at, status, platform,
              display_no, order_time, raw_text, first_job_id, latest_job_id, print_count
            )
            VALUES (?, ?, ?, ?, 'pending', 'yinbao', ?, ?, 'raw', 'job_1', 'job_1', 1)
            """,
            (order_id, order_id, created_at, created_at, display_no, order_time),
        )
        self.conn.commit()


if __name__ == "__main__":
    unittest.main()
