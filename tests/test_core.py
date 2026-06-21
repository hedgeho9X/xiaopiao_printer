"""核心链路单元测试。

本文件使用临时 SQLite 验证建表、订单解析、去重和完成/撤销。
它不操作真实打印机、不启动监听端口，也不调用 TTS。
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from app_core.database import initialize
from app_core.monitor import ReceiptMonitor
from app_core.order_parser import detect_platform_from_text, normalize_order_draft, parse_order_text
from app_core.order_repair import repair_legacy_orders
from app_core.order_store import OrderStore
from app_core.settings import RATE_ORDER, SettingsStore
from app_core.speech import build_overview_text, build_switch_order_text


CASES_DIR = Path(__file__).resolve().parent / "cases"

MEITUAN_TEXT = """**#1美团拼好饭**
【顾客到店自取】
下单时间：2026-05-21 11:41:00
备注：顾客需要餐具；
1号口袋
经典美式
-冷
-【默认】
*1  6.5
"""

YINBAO_TEXT = """手心咖啡
收银员：1001  牌号：大桌子 D1
单据号：202606071052125990004
下单时间：2026-06-07 10:52:12
商品名称        单价    数量    小计
[填好肚子]       15     1       15
瑞士卷
原味瑞士卷￥0/
原价：15.0       总数：1.00
堂食
"""

YINBAO_BAR_TEXT = """手心咖啡
收银员：1001  牌号：P0009
单据号：202606071240274520009
下单时间：2026-06-07 12:40:31
商品名称        单价    数量    小计
抹茶拿铁       25     1       25
冷饮/
原价：25       总数：1
外带
"""

WRONG_PLATFORM_TEXT = """网口小票机打印测试
收银员：1001（1001)
消费流水：201408081818181680008
牌号：8888
点送方式：堂食
打印时间：2026-06-20 20:53:34
--------------------------------
商品名称     单价  数量  小计
测试商品
2     68
商品备注
--------------------------------
总计：34      支付方式：储值卡
应收：68（整单折扣：50%）
本次消费为您节省了：46.00 元
实收：34      找零：0
"""


class CoreTest(unittest.TestCase):
    """核心 SQLite 与解析测试。"""

    def setUp(self) -> None:
        """创建内存数据库。"""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        initialize(self.conn)
        self.store = OrderStore(self.conn)
        self.settings = SettingsStore(self.conn)
        self.settings.initialize_defaults()

    def test_meituan_parse_and_store(self) -> None:
        """美团样例能解析出商品、整单备注和取件位置。"""
        job_id = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=MEITUAN_TEXT.encode("gbk", errors="ignore"),
            raw_text=MEITUAN_TEXT,
        )
        draft = parse_order_text("meituan", MEITUAN_TEXT)
        order_id = self.store.upsert_order_from_draft(
            job_id=job_id,
            platform="meituan",
            raw_text=MEITUAN_TEXT,
            draft=draft,
        )
        order = self.store.get_order(order_id)
        assert order is not None
        self.assertEqual(order.display_no, "美团 #1")
        self.assertIsNone(order.location)
        self.assertEqual(order.order_type, "堂食")
        self.assertEqual(order.order_note, "顾客需要餐具")
        self.assertEqual(order.items[0].name, "经典美式")
        self.assertEqual(order.items[0].item_options, "冷，默认")
        switch_text = build_switch_order_text(order)
        self.assertEqual(switch_text, "美团 #1，堂食，5月21日11点41分。")
        self.assertNotIn("经典美式", switch_text)

    def test_yinbao_parse_and_store(self) -> None:
        """银豹样例能解析出桌号、单据号和商品属性。"""
        job_id = self.store.create_print_job(
            print_method="network_print",
            platform="yinbao",
            raw_bytes=YINBAO_TEXT.encode("gbk", errors="ignore"),
            raw_text=YINBAO_TEXT,
        )
        draft = parse_order_text("yinbao", YINBAO_TEXT)
        order_id = self.store.upsert_order_from_draft(
            job_id=job_id,
            platform="yinbao",
            raw_text=YINBAO_TEXT,
            draft=draft,
        )
        order = self.store.get_order(order_id)
        assert order is not None
        self.assertEqual(order.platform_order_no, "202606071052125990004")
        self.assertEqual(order.location, "大桌子 D1")
        self.assertIsNone(order.pickup_method)
        self.assertEqual(order.items[0].name, "填好肚子 瑞士卷")
        self.assertEqual(order.items[0].item_options, "原味瑞士卷")

    def test_yinbao_bar_order_location(self) -> None:
        """银豹 P 开头牌号会识别为吧台点单位置。"""
        job_id = self.store.create_print_job(
            print_method="network_print",
            platform="yinbao",
            raw_bytes=YINBAO_BAR_TEXT.encode("gbk", errors="ignore"),
            raw_text=YINBAO_BAR_TEXT,
        )
        draft = parse_order_text("yinbao", YINBAO_BAR_TEXT)
        order_id = self.store.upsert_order_from_draft(
            job_id=job_id,
            platform="yinbao",
            raw_text=YINBAO_BAR_TEXT,
            draft=draft,
        )
        order = self.store.get_order(order_id)
        assert order is not None
        self.assertEqual(order.location, "吧台 P0009")
        self.assertEqual(order.order_type, "外带")
        self.assertIsNone(order.pickup_method)
        self.assertIn("吧台 P0009", build_overview_text(order))
        self.assertNotIn("外带，外带", build_overview_text(order))
        switch_text = build_switch_order_text(order)
        self.assertEqual(switch_text, "银豹 吧台 P0009，外带，6月7日12点40分。")
        self.assertNotIn("抹茶拿铁", switch_text)
        self.assertNotIn("2026", switch_text)

    def test_case_files_cover_business_categories(self) -> None:
        """四张真实样例文本能覆盖银豹桌号、银豹吧台、美团堂食和美团外卖。"""
        yinbao_bar = parse_order_text("yinbao", (CASES_DIR / "yinbao_bar_p0009.txt").read_text(encoding="utf-8"))
        yinbao_table = parse_order_text("yinbao", (CASES_DIR / "yinbao_table_d1.txt").read_text(encoding="utf-8"))
        meituan_pickup = parse_order_text("meituan", (CASES_DIR / "meituan_pickup.txt").read_text(encoding="utf-8"))
        meituan_delivery = parse_order_text("meituan", (CASES_DIR / "meituan_delivery.txt").read_text(encoding="utf-8"))

        self.assertEqual(yinbao_bar.location, "吧台 P0009")
        self.assertEqual(yinbao_table.location, "大桌子 D1")
        self.assertIsNone(meituan_pickup.location)
        self.assertEqual(meituan_pickup.order_type, "堂食")
        self.assertIsNone(meituan_delivery.location)
        self.assertEqual(meituan_delivery.order_type, "外卖")

    def test_meituan_text_overrides_wrong_entry_platform(self) -> None:
        """美团文本即使误进网口入口，也会纠正为美团平台。"""
        raw_text = (CASES_DIR / "meituan_pickup.txt").read_text(encoding="utf-8")
        platform = detect_platform_from_text(raw_text, "yinbao")
        draft = normalize_order_draft(platform, raw_text, parse_order_text(platform, raw_text))
        self.assertEqual(platform, "meituan")
        self.assertEqual(draft.platform_order_no, "1")
        self.assertEqual(draft.display_no, "美团 #1")
        self.assertIsNone(draft.location)

    def test_duplicate_print_updates_order(self) -> None:
        """同一小票全文重复打印不会新增订单。"""
        draft = parse_order_text("yinbao", YINBAO_TEXT)
        first_job = self.store.create_print_job(
            print_method="network_print",
            platform="yinbao",
            raw_bytes=b"first",
            raw_text=YINBAO_TEXT,
        )
        first_order = self.store.upsert_order_from_draft(
            job_id=first_job,
            platform="yinbao",
            raw_text=YINBAO_TEXT,
            draft=draft,
        )
        second_job = self.store.create_print_job(
            print_method="network_print",
            platform="yinbao",
            raw_bytes=b"second",
            raw_text=YINBAO_TEXT,
        )
        second_order = self.store.upsert_order_from_draft(
            job_id=second_job,
            platform="yinbao",
            raw_text=YINBAO_TEXT,
            draft=draft,
        )
        order = self.store.get_order(first_order)
        assert order is not None
        self.assertEqual(first_order, second_order)
        self.assertEqual(order.print_count, 2)

    def test_pending_orders_sort_by_order_time_desc(self) -> None:
        """待制作队列优先按小票时间倒序返回。"""
        first_job = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=b"first",
            raw_text=MEITUAN_TEXT,
        )
        first_order = self.store.upsert_order_from_draft(
            job_id=first_job,
            platform="meituan",
            raw_text=MEITUAN_TEXT,
            draft=parse_order_text("meituan", MEITUAN_TEXT),
        )
        second_text = MEITUAN_TEXT.replace("**#1美团拼好饭**", "**#2美团拼好饭**")
        second_job = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=b"second",
            raw_text=second_text,
        )
        second_order = self.store.upsert_order_from_draft(
            job_id=second_job,
            platform="meituan",
            raw_text=second_text,
            draft=parse_order_text("meituan", second_text),
        )
        self.conn.execute(
            "UPDATE orders SET created_at = ?, order_time = ? WHERE id = ?",
            ("2026-06-20T12:00:00", "2026-06-20 10:00:00", first_order),
        )
        self.conn.execute(
            "UPDATE orders SET created_at = ?, order_time = ? WHERE id = ?",
            ("2026-06-20T11:00:00", "2026-06-20 11:00:00", second_order),
        )
        self.conn.commit()
        self.assertEqual([order.id for order in self.store.get_pending_orders()], [second_order, first_order])

    def test_initialize_repairs_legacy_null_print_count(self) -> None:
        """初始化会修复旧版本数据库里为空的打印次数。"""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE orders (
              id TEXT PRIMARY KEY,
              dedupe_key TEXT UNIQUE,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              platform TEXT NOT NULL,
              platform_order_no TEXT,
              display_no TEXT NOT NULL,
              order_time TEXT,
              order_type TEXT,
              pickup_method TEXT,
              location TEXT,
              order_note TEXT,
              raw_text TEXT NOT NULL,
              first_job_id TEXT NOT NULL,
              latest_job_id TEXT NOT NULL,
              print_count INTEGER
            );
            INSERT INTO orders(
              id, created_at, updated_at, status, platform, display_no,
              raw_text, first_job_id, latest_job_id, print_count
            )
            VALUES (
              'ord_legacy', '2026-06-20T10:00:00', '2026-06-20T10:00:00',
              'pending', 'meituan', '旧订单', 'raw', 'job_1', 'job_1', NULL
            );
            """
        )
        initialize(conn)
        row = conn.execute("SELECT print_count FROM orders WHERE id = 'ord_legacy'").fetchone()
        self.assertEqual(row["print_count"], 1)

    def test_tts_rate_cycles_through_five_levels(self) -> None:
        """语速循环覆盖慢、中、快、更快、最快五档。"""
        self.settings.set("tts_rate", "medium")
        actual = [self.settings.cycle_rate() for _ in range(5)]
        self.assertEqual(actual, ["fast", "faster", "fastest", "slow", "medium"])
        self.assertEqual(RATE_ORDER, ["slow", "medium", "fast", "faster", "fastest"])

    def test_invalid_tts_rate_falls_back_to_medium_position(self) -> None:
        """异常语速值会按中速位置继续循环到快速。"""
        self.settings.set("tts_rate", "broken")
        self.assertEqual(self.settings.cycle_rate(), "fast")

    def test_same_order_no_with_different_text_creates_new_order(self) -> None:
        """即使平台单号相同，只要小票全文不同就按新订单处理。"""
        changed_text = YINBAO_TEXT.replace("原味瑞士卷￥0/", "抹茶瑞士卷￥0/")
        first_job = self.store.create_print_job(
            print_method="network_print",
            platform="yinbao",
            raw_bytes=b"first",
            raw_text=YINBAO_TEXT,
        )
        first_order = self.store.upsert_order_from_draft(
            job_id=first_job,
            platform="yinbao",
            raw_text=YINBAO_TEXT,
            draft=parse_order_text("yinbao", YINBAO_TEXT),
        )
        second_job = self.store.create_print_job(
            print_method="network_print",
            platform="yinbao",
            raw_bytes=b"second",
            raw_text=changed_text,
        )
        second_order = self.store.upsert_order_from_draft(
            job_id=second_job,
            platform="yinbao",
            raw_text=changed_text,
            draft=parse_order_text("yinbao", changed_text),
        )
        first = self.store.get_order(first_order)
        second = self.store.get_order(second_order)
        assert first is not None
        assert second is not None
        self.assertNotEqual(first_order, second_order)
        self.assertEqual(first.platform_order_no, second.platform_order_no)
        self.assertEqual(first.print_count, 1)
        self.assertEqual(second.print_count, 1)

    def test_raw_text_dedupes_when_order_no_missing(self) -> None:
        """没有平台单号时，相同原文不会不断创建新订单。"""
        draft = parse_order_text("meituan", WRONG_PLATFORM_TEXT)
        self.assertEqual(draft.items, [])
        first_job = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=b"first",
            raw_text=WRONG_PLATFORM_TEXT,
        )
        first_order = self.store.upsert_order_from_draft(
            job_id=first_job,
            platform="meituan",
            raw_text=WRONG_PLATFORM_TEXT,
            draft=draft,
        )
        second_job = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=b"second",
            raw_text=WRONG_PLATFORM_TEXT,
        )
        second_order = self.store.upsert_order_from_draft(
            job_id=second_job,
            platform="meituan",
            raw_text=WRONG_PLATFORM_TEXT,
            draft=draft,
        )
        order = self.store.get_order(first_order)
        assert order is not None
        self.assertEqual(first_order, second_order)
        self.assertTrue((order.dedupe_key or "").startswith("raw:"))
        self.assertEqual(order.print_count, 2)

    def test_monitor_parse_failure_keeps_detected_platform(self) -> None:
        """解析异常兜底时仍使用已识别平台，不再创建 unknown 订单。"""
        monitor = ReceiptMonitor(settings=self.settings, store=self.store)

        def broken_parse(_platform: str, _raw_text: str):
            raise RuntimeError("模拟解析失败")

        monitor._parse_order = broken_parse  # type: ignore[method-assign]
        order_id = monitor.handle_bytes(MEITUAN_TEXT.encode("utf-8"))
        order = self.store.get_order(order_id)
        assert order is not None
        self.assertEqual(order.platform, "meituan")
        self.assertEqual(order.display_no, "美团 #1")
        self.assertEqual(order.items[0].name, "经典美式")

    def test_repair_legacy_unknown_duplicate(self) -> None:
        """旧版本同 raw 的 unknown 订单会合并回已解析订单。"""
        first_job = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=b"first",
            raw_text=MEITUAN_TEXT,
        )
        known_order_id = self.store.upsert_order_from_draft(
            job_id=first_job,
            platform="meituan",
            raw_text=MEITUAN_TEXT,
            draft=parse_order_text("meituan", MEITUAN_TEXT),
        )
        legacy_job = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=b"legacy",
            raw_text=MEITUAN_TEXT,
        )
        orphan_job = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=b"orphan",
            raw_text=MEITUAN_TEXT,
        )
        legacy_order_id = "ord_legacy_unknown"
        self.conn.execute(
            """
            INSERT INTO orders(
              id, dedupe_key, created_at, updated_at, status, platform, display_no,
              raw_text, first_job_id, latest_job_id, print_count
            )
            VALUES (?, ?, ?, ?, 'done', 'unknown', ?, ?, ?, ?, 1)
            """,
            (
                legacy_order_id,
                "unknown:raw:legacy",
                "2026-06-21T10:00:00",
                "2026-06-21T10:10:00",
                "未解析订单 10:00",
                MEITUAN_TEXT,
                legacy_job,
                legacy_job,
            ),
        )
        self.conn.execute("UPDATE print_jobs SET order_id = ? WHERE id = ?", (legacy_order_id, legacy_job))
        self.conn.commit()

        repaired = repair_legacy_orders(self.store)
        known_order = self.store.get_order(known_order_id)
        assert known_order is not None
        self.assertGreaterEqual(repaired, 1)
        self.assertEqual(known_order.platform, "meituan")
        self.assertEqual(known_order.status, "done")
        self.assertEqual(known_order.print_count, 3)
        self.assertTrue((known_order.dedupe_key or "").startswith("raw:"))
        self.assertIsNone(self.store.get_order(legacy_order_id))
        for job_id in (legacy_job, orphan_job):
            row = self.conn.execute("SELECT order_id FROM print_jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(row["order_id"], known_order_id)

    def test_monitor_refuses_proxy_as_forward_target(self) -> None:
        """监听器不能把原始 bytes 再转发给代理打印机自身。"""
        self.settings.set("target_printer_name", "Receipt Voice Proxy")
        job_id = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=b"loop",
            raw_text="loop",
        )
        monitor = ReceiptMonitor(settings=self.settings, store=self.store)
        monitor._forward(job_id, b"loop")
        row = self.conn.execute(
            "SELECT forwarded, forward_error FROM print_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        self.assertEqual(row["forwarded"], 0)
        self.assertIn("代理打印机", row["forward_error"])

    def test_complete_and_undo(self) -> None:
        """完成和撤销能更新 pending 队列。"""
        job_id = self.store.create_print_job(
            print_method="usb_print",
            platform="meituan",
            raw_bytes=b"job",
            raw_text=MEITUAN_TEXT,
        )
        order_id = self.store.upsert_order_from_draft(
            job_id=job_id,
            platform="meituan",
            raw_text=MEITUAN_TEXT,
            draft=parse_order_text("meituan", MEITUAN_TEXT),
        )
        self.assertTrue(self.store.complete_order(order_id))
        self.assertEqual(self.store.get_pending_orders(), [])
        restored = self.store.undo_last_done()
        assert restored is not None
        self.assertEqual(restored.id, order_id)
        self.assertEqual(len(self.store.get_pending_orders()), 1)


if __name__ == "__main__":
    unittest.main()
