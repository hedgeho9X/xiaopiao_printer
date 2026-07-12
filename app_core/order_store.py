"""订单与打印任务存储。

本模块封装 print_jobs、orders、order_items 和 order_events 的写入与查询。
UI、监听器和 TTS 都应通过这里访问订单数据，而不是直接拼 SQL。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3

from .ids import make_id, now_iso
from .models import ItemDraft, Order, OrderDraft, OrderItem


class OrderStore:
    """SQLite 订单仓库。"""

    def __init__(self, conn: sqlite3.Connection):
        """保存 SQLite 连接。"""
        self.conn = conn

    def create_print_job(
        self,
        *,
        print_method: str,
        platform: str,
        raw_bytes: bytes,
        raw_text: str,
    ) -> str:
        """保存一次打印输入，并返回 job_id。"""
        job_id = make_id("job")
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO print_jobs(
              id, created_at, print_method, platform, raw_bytes, raw_text
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, now, print_method, platform, sqlite3.Binary(raw_bytes), raw_text),
        )
        self.conn.commit()
        return job_id

    def update_forward_result(
        self,
        job_id: str,
        *,
        forwarded: bool,
        forward_error: str | None = None,
    ) -> None:
        """更新打印转发结果。"""
        self.conn.execute(
            """
            UPDATE print_jobs
            SET forwarded = ?, forward_error = ?
            WHERE id = ?
            """,
            (1 if forwarded else 0, forward_error, job_id),
        )
        self.conn.commit()

    def upsert_order_from_draft(
        self,
        *,
        job_id: str,
        platform: str,
        raw_text: str,
        draft: OrderDraft,
    ) -> str:
        """根据解析草稿创建或更新订单。"""
        dedupe_key = self._make_dedupe_key(platform, raw_text)
        existing_id = self._find_order_id(dedupe_key) if dedupe_key else None
        if existing_id:
            order_id = existing_id
            self._update_existing_order(order_id, job_id, raw_text, draft)
            self._replace_items(order_id, draft.items)
            self._add_event(order_id, "reprinted", "同一小票全文重复打印，已更新订单内容。")
        else:
            order_id = make_id("ord")
            self._insert_new_order(order_id, dedupe_key, job_id, platform, raw_text, draft)
            self._replace_items(order_id, draft.items)
            self._add_event(order_id, "created", "创建订单。")
        self.conn.execute(
            "UPDATE print_jobs SET order_id = ? WHERE id = ?",
            (order_id, job_id),
        )
        self.conn.commit()
        return order_id

    def get_pending_orders(self) -> list[Order]:
        """返回所有待制作订单，按小票时间倒序排序。"""
        rows = self.conn.execute(
            """
            SELECT * FROM orders
            WHERE status = 'pending'
            """
        ).fetchall()
        rows = self._sort_order_rows(rows, fallback_fields=("created_at",))
        return [self._row_to_order(row) for row in rows]

    def get_done_orders(self, limit: int = 50) -> list[Order]:
        """返回历史订单，按小票时间倒序排序。"""
        rows = self.conn.execute(
            """
            SELECT * FROM orders
            WHERE status = 'done'
            """
        ).fetchall()
        rows = self._sort_order_rows(rows, fallback_fields=("updated_at", "created_at"))[:limit]
        return [self._row_to_order(row) for row in rows]

    def get_order(self, order_id: str) -> Order | None:
        """按 ID 查询一个订单。"""
        row = self.conn.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        return self._row_to_order(row) if row else None

    def complete_order(self, order_id: str) -> bool:
        """把订单标记为完成，并记录事件。"""
        now = now_iso()
        cur = self.conn.execute(
            """
            UPDATE orders
            SET status = 'done', updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, order_id),
        )
        if cur.rowcount:
            self._add_event(order_id, "done", "订单已完成。", commit=False)
            self.conn.commit()
            return True
        return False

    def undo_last_done(self) -> Order | None:
        """撤销最近完成的一单，并返回恢复后的订单。"""
        row = self.conn.execute(
            """
            SELECT * FROM orders
            WHERE status = 'done'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        order_id = str(row["id"])
        self.conn.execute(
            """
            UPDATE orders
            SET status = 'pending', updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), order_id),
        )
        self._add_event(order_id, "undo_done", "撤销最近完成订单。", commit=False)
        self.conn.commit()
        return self.get_order(order_id)

    def restore_order(self, order_id: str) -> Order | None:
        """从历史中恢复指定完成订单。"""
        cur = self.conn.execute(
            """
            UPDATE orders
            SET status = 'pending', updated_at = ?
            WHERE id = ? AND status = 'done'
            """,
            (now_iso(), order_id),
        )
        if not cur.rowcount:
            return None
        self._add_event(order_id, "restore_done", "从历史订单恢复。", commit=False)
        self.conn.commit()
        return self.get_order(order_id)

    def _insert_new_order(
        self,
        order_id: str,
        dedupe_key: str | None,
        job_id: str,
        platform: str,
        raw_text: str,
        draft: OrderDraft,
    ) -> None:
        """插入新订单。"""
        now = now_iso()
        order_time = normalize_order_time(draft.order_time, now) if draft.order_time else None
        self.conn.execute(
            """
            INSERT INTO orders(
              id, dedupe_key, created_at, updated_at, status, platform,
              platform_order_no, display_no, order_time, order_type,
              pickup_method, location, order_note, raw_text,
              first_job_id, latest_job_id, print_count
            )
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                order_id,
                dedupe_key,
                now,
                now,
                platform,
                draft.platform_order_no,
                draft.display_no,
                order_time,
                draft.order_type,
                draft.pickup_method,
                draft.location,
                draft.order_note,
                raw_text,
                job_id,
                job_id,
            ),
        )

    def _update_existing_order(
        self,
        order_id: str,
        job_id: str,
        raw_text: str,
        draft: OrderDraft,
    ) -> None:
        """用重复打印的小票更新已有订单。"""
        now = now_iso()
        order_time = normalize_order_time(draft.order_time, now) if draft.order_time else None
        self.conn.execute(
            """
            UPDATE orders
            SET updated_at = ?,
                platform_order_no = ?,
                display_no = ?,
                order_time = ?,
                order_type = ?,
                pickup_method = ?,
                location = ?,
                order_note = ?,
                raw_text = ?,
                latest_job_id = ?,
                print_count = COALESCE(print_count, 0) + 1
            WHERE id = ?
            """,
            (
                now,
                draft.platform_order_no,
                draft.display_no,
                order_time,
                draft.order_type,
                draft.pickup_method,
                draft.location,
                draft.order_note,
                raw_text,
                job_id,
                order_id,
            ),
        )

    def _replace_items(self, order_id: str, items: list[ItemDraft]) -> None:
        """删除旧商品并写入最新商品列表。"""
        self.conn.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        for index, item in enumerate(items, start=1):
            self.conn.execute(
                """
                INSERT INTO order_items(
                  id, order_id, sort_order, name, quantity, price,
                  item_options, raw_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_id("item"),
                    order_id,
                    index,
                    item.name,
                    item.quantity,
                    item.price,
                    item.item_options,
                    item.raw_text,
                ),
            )

    def _add_event(
        self,
        order_id: str,
        event_type: str,
        detail: str,
        *,
        commit: bool = True,
    ) -> None:
        """写入订单事件。"""
        self.conn.execute(
            """
            INSERT INTO order_events(id, order_id, created_at, event_type, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (make_id("evt"), order_id, now_iso(), event_type, detail),
        )
        if commit:
            self.conn.commit()

    def _row_to_order(self, row: sqlite3.Row) -> Order:
        """把 orders 行转换为 Order 对象，并附带商品。"""
        items = self._load_items(str(row["id"]))
        return Order(
            id=str(row["id"]),
            dedupe_key=row["dedupe_key"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            status=str(row["status"]),
            platform=str(row["platform"]),
            platform_order_no=row["platform_order_no"],
            display_no=str(row["display_no"]),
            order_time=row["order_time"],
            order_type=row["order_type"],
            pickup_method=row["pickup_method"],
            location=row["location"],
            order_note=row["order_note"],
            raw_text=str(row["raw_text"]),
            first_job_id=str(row["first_job_id"]),
            latest_job_id=str(row["latest_job_id"]),
            print_count=int(row["print_count"] or 1),
            items=items,
        )

    def _load_items(self, order_id: str) -> list[OrderItem]:
        """读取订单商品列表。"""
        rows = self.conn.execute(
            """
            SELECT * FROM order_items
            WHERE order_id = ?
            ORDER BY sort_order ASC
            """,
            (order_id,),
        ).fetchall()
        return [
            OrderItem(
                id=str(row["id"]),
                order_id=str(row["order_id"]),
                sort_order=int(row["sort_order"]),
                name=str(row["name"]),
                quantity=row["quantity"],
                price=row["price"],
                item_options=row["item_options"],
                raw_text=row["raw_text"],
            )
            for row in rows
        ]

    @staticmethod
    def _sort_order_rows(rows: list[sqlite3.Row], fallback_fields: tuple[str, ...]) -> list[sqlite3.Row]:
        """按规范化后的业务时间倒序排列订单行。

        SQLite 会把 ``2026-07-12T13:33`` 排在 ``2026-07-12 13:48`` 前面，因为它们是字符串。
        这里先把 ``T``、缺年份、只有时分等格式统一掉，再给 UI 返回稳定的倒序队列。
        """
        return sorted(
            rows,
            key=lambda row: (
                normalize_order_time(row["order_time"], first_row_value(row, fallback_fields)),
                str(row["updated_at"] or ""),
                str(row["created_at"] or ""),
            ),
            reverse=True,
        )

    def _find_order_id(self, dedupe_key: str) -> str | None:
        """按去重键查找订单 ID。"""
        row = self.conn.execute(
            "SELECT id FROM orders WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        return str(row["id"]) if row else None

    @staticmethod
    def _make_dedupe_key(platform: str, raw_text: str) -> str | None:
        """根据平台和完整小票文本指纹生成去重键。

        平台单号和平台名仍会作为业务字段保存，但不参与去重，避免 LLM 输出抖动、
        平台识别兜底或条形码误判把同一份小票拆成多张订单。
        """
        normalized = normalize_dedupe_text(raw_text)
        if not normalized:
            return None
        digest = hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]
        return f"raw:{digest}"


def first_row_value(row: sqlite3.Row, fields: tuple[str, ...]) -> str:
    """从 SQLite 行里按优先级取第一个非空字段。"""
    for field in fields:
        value = str(row[field] or "").strip()
        if value:
            return value
    return ""


def normalize_dedupe_text(raw_text: str) -> str:
    """清洗参与去重的小票原文，避免美团重打提示和空白差异造成重复订单。"""
    lines = [
        line
        for line in str(raw_text or "").splitlines()
        if not _is_meituan_history_reprint_line(line)
    ]
    text = "".join(lines)
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[@!]+", "", text)


def _is_meituan_history_reprint_line(line: str) -> bool:
    """识别美团历史订单重打时额外插入的固定提示行。"""
    compact = re.sub(r"\s+", "", line)
    return "历史订单" in compact and "请勿重复备餐" in compact


def normalize_order_time(value: object, fallback: object = "") -> str:
    """把订单时间统一成 ``YYYY-MM-DD HH:MM:SS``。

    Args:
        value: 小票里的业务时间，可能是完整时间、缺年份时间或只有时分。
        fallback: 缺少年份/日期时使用的参考时间，通常是订单创建时间。
    """
    fallback_text = normalize_order_time(fallback, "") if fallback else "0000-00-00 00:00:00"
    text = str(value or "").strip()
    if not text:
        return fallback_text

    normalized = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", " ")
        .replace("/", "-")
        .replace("T", " ")
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()

    full_match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", normalized)
    if full_match:
        return _format_time_parts(full_match.groups(default="0"))

    month_day_match = re.match(r"^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", normalized)
    if month_day_match:
        year = fallback_text[:4] if re.match(r"^\d{4}", fallback_text) else "0000"
        month, day, hour, minute, second = month_day_match.groups(default="0")
        return _format_time_parts((year, month, day, hour, minute, second))

    time_match = re.match(r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", normalized)
    if time_match and re.match(r"^\d{4}-\d{2}-\d{2}", fallback_text):
        hour, minute, second = time_match.groups(default="0")
        return f"{fallback_text[:10]} {int(hour):02d}:{int(minute):02d}:{int(second):02d}"

    return normalized or fallback_text


def _format_time_parts(parts: tuple[str, ...]) -> str:
    """把年月日时分秒补零成固定宽度时间字符串。"""
    year, month, day, hour, minute, second = parts[:6]
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{int(minute):02d}:{int(second):02d}"
