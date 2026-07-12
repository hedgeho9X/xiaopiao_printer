"""订单去重文本清洗测试。

本文件验证 raw_text 进入 sha256 前的确定性清洗，避免美团历史订单重打提示、空白和控制残留造成重复订单。
"""

from __future__ import annotations

import sqlite3
import unittest

from app_core.database import initialize
from app_core.models import ItemDraft, OrderDraft
from app_core.order_store import OrderStore, normalize_dedupe_text


class DedupeNormalizationTest(unittest.TestCase):
    """订单去重清洗测试。"""

    def setUp(self) -> None:
        """创建内存数据库和订单仓库。"""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        initialize(self.conn)
        self.store = OrderStore(self.conn)

    def test_meituan_history_reprint_and_spaces_share_same_key(self) -> None:
        """美团历史订单提示和空格差异不会改变去重 key。"""
        first_raw = "@!Ea给商家\na预!a美团拼好饭#1!Ea\n经典美式\n-冷\n-【默认】\n*1 6.5\n"
        second_raw = "@!a历史订单，请勿重复备餐\nEa给商家\na预!a美团拼好饭#1!Ea\n经典 美式\n- 冷\n-【 默认 】\n* 1 6.5\n"

        self.assertEqual(normalize_dedupe_text(first_raw), normalize_dedupe_text(second_raw))
        self.assertEqual(
            self.store._make_dedupe_key("meituan", first_raw),
            self.store._make_dedupe_key("meituan", second_raw),
        )

    def test_dedupe_ignores_ai_note_spacing_when_raw_is_same_business_order(self) -> None:
        """同一业务小票重打时，即使解析备注空格不同也只更新同一条订单。"""
        first_raw = "@!Ea给商家\na预!a美团拼好饭#1!Ea\n经典美式\n-冷\n-【默认】\n*1 6.5\n"
        second_raw = "@!a历史订单，请勿重复备餐\nEa给商家\na预!a美团拼好饭#1!Ea\n经典 美式\n- 冷\n-【 默认 】\n* 1 6.5\n"
        first_job = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=first_raw.encode("gbk", errors="replace"),
            raw_text=first_raw,
        )
        first_order = self.store.upsert_order_from_draft(
            job_id=first_job,
            platform="meituan",
            raw_text=first_raw,
            draft=self._draft("冷,默认"),
        )
        second_job = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=second_raw.encode("gbk", errors="replace"),
            raw_text=second_raw,
        )
        second_order = self.store.upsert_order_from_draft(
            job_id=second_job,
            platform="meituan",
            raw_text=second_raw,
            draft=self._draft("冷, 默认"),
        )

        self.assertEqual(second_order, first_order)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
        order = self.store.get_order(first_order)
        assert order is not None
        self.assertEqual(order.print_count, 2)
        self.assertEqual(order.latest_job_id, second_job)

    @staticmethod
    def _draft(item_options: str) -> OrderDraft:
        """生成一份模拟 LLM 输出的美团订单草稿。"""
        return OrderDraft(
            platform_order_no="1",
            display_no="美团 #1",
            order_time="2026-07-06 13:49:51",
            order_type="外卖",
            items=[
                ItemDraft(
                    name="经典美式",
                    quantity="1",
                    price="6.5",
                    item_options=item_options,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
