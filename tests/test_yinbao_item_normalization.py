"""银豹商品列校正回归测试。

本文件专门覆盖 LLM 把银豹小票里的价格、小计误当成数量的情况，
确保进入数据库前会用商品原文再做一次确定性校正。
"""

from __future__ import annotations

import unittest

from app_core.models import ItemDraft, OrderDraft
from app_core.order_parser import normalize_order_draft


class YinbaoItemNormalizationTest(unittest.TestCase):
    """验证银豹商品的数量、价格和名称会被商品原文兜底修正。"""

    def test_split_quantity_price_line_keeps_quantity_before_price(self) -> None:
        """商品名下一行只有两个数字时，第一列是数量，第二列是价格或小计。"""
        draft = OrderDraft(
            platform_order_no="201408081818181680008",
            display_no="银豹 8888",
            order_time="2026-06-21 16:51:11",
            order_type="堂食",
            location="8888",
            items=[
                ItemDraft(
                    name="测试商品",
                    quantity="68",
                    price="2",
                    item_options="商品备注",
                    raw_text="测试商品\n2     68\n商品备注",
                )
            ],
        )

        normalized = normalize_order_draft("yinbao", "网口小票机打印测试", draft)
        item = normalized.items[0]

        self.assertEqual(item.name, "测试商品")
        self.assertEqual(item.quantity, "2")
        self.assertEqual(item.price, "68")
        self.assertEqual(item.item_options, "商品备注")

    def test_inline_price_quantity_subtotal_line_cleans_bad_name(self) -> None:
        """商品、单价、数量、小计在同一行时，名称不能保留数字列。"""
        draft = OrderDraft(
            platform_order_no="202606071240274520009",
            display_no="银豹 吧台 P0009",
            order_time="2026-06-07 12:40:31",
            order_type="外带",
            location="吧台 P0009",
            items=[
                ItemDraft(
                    name="抹茶拿铁        25      1       25",
                    quantity=None,
                    price=None,
                    item_options="冷饮/",
                    raw_text="抹茶拿铁        25      1       25\n冷饮/",
                )
            ],
        )

        normalized = normalize_order_draft("yinbao", "手心咖啡", draft)
        item = normalized.items[0]

        self.assertEqual(item.name, "抹茶拿铁")
        self.assertEqual(item.quantity, "1")
        self.assertEqual(item.price, "25")
        self.assertEqual(item.item_options, "冷饮/")

    def test_bracket_item_preserves_llm_combined_name(self) -> None:
        """分类行后面还有商品名时，不能把完整商品名压回分类名。"""
        draft = OrderDraft(
            platform_order_no="202606071052125990004",
            display_no="银豹 大桌子 D1",
            order_time="2026-06-07 10:52:12",
            order_type="堂食",
            location="大桌子 D1",
            items=[
                ItemDraft(
                    name="填好肚子 瑞士卷",
                    quantity=None,
                    price=None,
                    item_options="原味瑞士卷",
                    raw_text="[填好肚子]      15      1       15\n瑞士卷\n原味瑞士卷￥0/",
                )
            ],
        )

        normalized = normalize_order_draft("yinbao", "手心咖啡", draft)
        item = normalized.items[0]

        self.assertEqual(item.name, "填好肚子 瑞士卷")
        self.assertEqual(item.quantity, "1")
        self.assertEqual(item.price, "15")
        self.assertEqual(item.item_options, "原味瑞士卷")


if __name__ == "__main__":
    unittest.main()
