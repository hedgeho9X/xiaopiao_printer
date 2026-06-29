"""历史订单数据修复工具。

本模块集中处理旧版本数据库遗留问题，例如 unknown 订单、旧去重键格式、
以及保存了 print_job 但没有成功关联订单的孤儿打印输入。
"""

from __future__ import annotations

import sqlite3

from .order_parser import detect_platform_from_text, normalize_order_draft, parse_order_text
from .order_store import OrderStore


def repair_legacy_orders(store: OrderStore) -> int:
    """修复旧版本留下的平台 unknown、重复订单和孤儿 print_jobs。"""
    repaired = 0
    repaired += _delete_status_query_noise(store)
    for rows in _legacy_raw_groups(store):
        canonical = _choose_canonical_order(rows)
        for row in rows:
            if row["id"] == canonical["id"]:
                continue
            _merge_duplicate_order(store, str(canonical["id"]), str(row["id"]))
            repaired += 1
        key = store._make_dedupe_key(str(canonical["platform"]), str(canonical["raw_text"]))
        store.conn.execute("UPDATE orders SET dedupe_key = ? WHERE id = ?", (key, canonical["id"]))
    for row in store.conn.execute("SELECT * FROM orders").fetchall():
        target_key = store._make_dedupe_key(str(row["platform"]), str(row["raw_text"]))
        if target_key and row["dedupe_key"] != target_key:
            store.conn.execute("UPDATE orders SET dedupe_key = ? WHERE id = ?", (target_key, row["id"]))
            repaired += 1
        if str(row["platform"]) == "unknown" or str(row["display_no"]).startswith("未解析订单"):
            repaired += _repair_unknown_order(store, row)
    repaired += _repair_orphan_print_jobs(store)
    store.conn.commit()
    return repaired


def _delete_status_query_noise(store: OrderStore) -> int:
    """删除旧版本误入库的 ESC/POS 状态查询包和空订单。"""
    order_ids = [
        str(row["id"])
        for row in store.conn.execute(
            """
            SELECT o.id
            FROM orders o
            WHERE EXISTS (
                SELECT 1 FROM print_jobs j
                WHERE j.order_id = o.id AND hex(j.raw_bytes) IN ('100401', '100402', '100403', '100404')
            )
            AND NOT EXISTS (
                SELECT 1 FROM print_jobs j
                WHERE j.order_id = o.id AND hex(j.raw_bytes) NOT IN ('100401', '100402', '100403', '100404')
            )
            AND NOT EXISTS (
                SELECT 1 FROM order_items i
                WHERE i.order_id = o.id
            )
            """
        ).fetchall()
    ]
    if order_ids:
        placeholders = ",".join("?" for _ in order_ids)
        store.conn.execute(
            f"UPDATE print_jobs SET order_id = NULL WHERE order_id IN ({placeholders})",
            order_ids,
        )
        store.conn.execute(f"DELETE FROM order_events WHERE order_id IN ({placeholders})", order_ids)
        store.conn.execute(f"DELETE FROM order_items WHERE order_id IN ({placeholders})", order_ids)
        store.conn.execute(f"DELETE FROM orders WHERE id IN ({placeholders})", order_ids)
    deleted_jobs = store.conn.execute(
        """
        DELETE FROM print_jobs
        WHERE order_id IS NULL
          AND hex(raw_bytes) IN ('100401', '100402', '100403', '100404')
        """
    ).rowcount
    return len(order_ids) + max(deleted_jobs, 0)


def _legacy_raw_groups(store: OrderStore) -> list[list[sqlite3.Row]]:
    """按新版 raw 去重键分组，找出旧数据中的重复订单。"""
    groups: dict[str, list[sqlite3.Row]] = {}
    rows = store.conn.execute("SELECT * FROM orders").fetchall()
    for row in rows:
        key = store._make_dedupe_key(str(row["platform"]), str(row["raw_text"]))
        if not key:
            continue
        groups.setdefault(key, []).append(row)
    return [rows for rows in groups.values() if len(rows) > 1]


def _choose_canonical_order(rows: list[sqlite3.Row]) -> sqlite3.Row:
    """为重复订单选择保留行，优先保留已解析平台和打印次数更多的订单。"""
    return sorted(
        rows,
        key=lambda row: (
            1 if str(row["platform"]) != "unknown" else 0,
            int(row["print_count"] or 1),
            str(row["updated_at"]),
        ),
        reverse=True,
    )[0]


def _merge_duplicate_order(store: OrderStore, canonical_id: str, duplicate_id: str) -> None:
    """把重复订单的打印记录并入保留订单，并删除重复订单。"""
    duplicate = store.get_order(duplicate_id)
    store.conn.execute("UPDATE print_jobs SET order_id = ? WHERE order_id = ?", (canonical_id, duplicate_id))
    if duplicate and duplicate.status == "done":
        store.conn.execute(
            "UPDATE orders SET status = 'done', updated_at = MAX(updated_at, ?) WHERE id = ?",
            (duplicate.updated_at, canonical_id),
        )
    store.conn.execute("DELETE FROM order_events WHERE order_id = ?", (duplicate_id,))
    store.conn.execute("DELETE FROM order_items WHERE order_id = ?", (duplicate_id,))
    store.conn.execute("DELETE FROM orders WHERE id = ?", (duplicate_id,))
    _refresh_order_job_stats(store, canonical_id)
    store._add_event(canonical_id, "repair_duplicate", "合并旧版本重复订单。", commit=False)


def _refresh_order_job_stats(store: OrderStore, order_id: str) -> None:
    """根据 print_jobs 重新计算订单打印次数和首尾 job。"""
    row = store.conn.execute(
        """
        SELECT COUNT(*) AS total,
               (SELECT id FROM print_jobs WHERE order_id = ? ORDER BY created_at ASC LIMIT 1) AS first_id,
               (SELECT id FROM print_jobs WHERE order_id = ? ORDER BY created_at DESC LIMIT 1) AS latest_id
        FROM print_jobs
        WHERE order_id = ?
        """,
        (order_id, order_id, order_id),
    ).fetchone()
    if not row or not row["total"]:
        return
    store.conn.execute(
        """
        UPDATE orders
        SET print_count = ?, first_job_id = ?, latest_job_id = ?
        WHERE id = ?
        """,
        (int(row["total"]), row["first_id"], row["latest_id"], order_id),
    )


def _repair_unknown_order(store: OrderStore, row: sqlite3.Row) -> int:
    """用 print_job 平台和规则解析修复 unknown/未解析订单。"""
    platform = _known_platform_for_order(store, str(row["id"]), str(row["raw_text"]))
    if platform == "unknown":
        return 0
    raw_text = str(row["raw_text"])
    draft = normalize_order_draft(platform, raw_text, parse_order_text(platform, raw_text))
    store.conn.execute(
        """
        UPDATE orders
        SET platform = ?, dedupe_key = ?, platform_order_no = ?, display_no = ?,
            order_time = ?, order_type = ?, pickup_method = ?, location = ?, order_note = ?
        WHERE id = ?
        """,
        (
            platform,
            store._make_dedupe_key(platform, raw_text),
            draft.platform_order_no,
            draft.display_no,
            draft.order_time,
            draft.order_type,
            draft.pickup_method,
            draft.location,
            draft.order_note,
            row["id"],
        ),
    )
    store._replace_items(str(row["id"]), draft.items)
    store._add_event(str(row["id"]), "repair_unknown", "按原始小票重新解析 unknown 订单。", commit=False)
    return 1


def _repair_orphan_print_jobs(store: OrderStore) -> int:
    """把旧版本未关联订单的 print_jobs 挂回对应订单。"""
    repaired = 0
    rows = store.conn.execute("SELECT * FROM print_jobs WHERE order_id IS NULL").fetchall()
    for row in rows:
        platform = str(row["platform"])
        raw_text = str(row["raw_text"])
        if platform == "unknown":
            platform = _known_platform_for_order(store, "", raw_text)
        dedupe_key = store._make_dedupe_key(platform, raw_text)
        existing_id = store._find_order_id(dedupe_key) if dedupe_key else None
        if existing_id:
            store.conn.execute("UPDATE print_jobs SET order_id = ? WHERE id = ?", (existing_id, row["id"]))
            _refresh_order_job_stats(store, existing_id)
            store._add_event(existing_id, "repair_orphan_job", "关联旧版本遗留的打印输入。", commit=False)
        else:
            draft = normalize_order_draft(platform, raw_text, parse_order_text(platform, raw_text))
            store.upsert_order_from_draft(
                job_id=str(row["id"]),
                platform=platform,
                raw_text=raw_text,
                draft=draft,
            )
        repaired += 1
    return repaired


def _known_platform_for_order(store: OrderStore, order_id: str, raw_text: str) -> str:
    """优先使用 print_jobs 平台，其次根据文本重新识别平台。"""
    row = store.conn.execute(
        """
        SELECT platform FROM print_jobs
        WHERE order_id = ? AND platform != 'unknown'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    if row:
        return str(row["platform"])
    return detect_platform_from_text(raw_text, "unknown")
