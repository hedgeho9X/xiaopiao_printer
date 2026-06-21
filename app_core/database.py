"""SQLite 数据库初始化与连接管理。

本模块负责确定应用数据目录、创建 SQLite 连接并初始化表结构。
它是“抓包 -> 入库 -> UI/TTS 查询”链路的底层存储入口。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DB_FILE_NAME = "receipt_voice.db"


def app_base_dir() -> Path:
    """返回应用基准目录。

    Returns:
        开发环境返回项目目录；PyInstaller 环境返回 exe 所在目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def app_data_dir() -> Path:
    """返回应用数据目录，并确保目录存在。"""
    configured = os.environ.get("RECEIPT_VOICE_DATA_DIR")
    path = Path(configured) if configured else app_base_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_db_path() -> Path:
    """返回默认 SQLite 数据库路径。"""
    return app_data_dir() / DB_FILE_NAME


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """创建 SQLite 连接并开启外键约束。"""
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """提供简单事务上下文，异常时自动回滚。"""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def initialize(conn: sqlite3.Connection) -> None:
    """初始化 v1 数据库表和索引。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS print_jobs (
          id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          print_method TEXT NOT NULL,
          platform TEXT NOT NULL,
          raw_bytes BLOB NOT NULL,
          raw_text TEXT NOT NULL,
          forwarded INTEGER NOT NULL DEFAULT 0,
          forward_error TEXT,
          order_id TEXT,
          FOREIGN KEY(order_id) REFERENCES orders(id)
        );

        CREATE TABLE IF NOT EXISTS orders (
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
          print_count INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(first_job_id) REFERENCES print_jobs(id),
          FOREIGN KEY(latest_job_id) REFERENCES print_jobs(id)
        );

        CREATE TABLE IF NOT EXISTS order_items (
          id TEXT PRIMARY KEY,
          order_id TEXT NOT NULL,
          sort_order INTEGER NOT NULL,
          name TEXT NOT NULL,
          quantity TEXT,
          price TEXT,
          item_options TEXT,
          raw_text TEXT,
          FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
          UNIQUE(order_id, sort_order)
        );

        CREATE TABLE IF NOT EXISTS order_events (
          id TEXT PRIMARY KEY,
          order_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          event_type TEXT NOT NULL,
          detail TEXT,
          FOREIGN KEY(order_id) REFERENCES orders(id)
        );

        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_print_jobs_created_at
        ON print_jobs(created_at);

        CREATE INDEX IF NOT EXISTS idx_print_jobs_order_id
        ON print_jobs(order_id);

        CREATE INDEX IF NOT EXISTS idx_print_jobs_method_platform
        ON print_jobs(print_method, platform);

        CREATE INDEX IF NOT EXISTS idx_orders_status_created_at
        ON orders(status, created_at);

        CREATE INDEX IF NOT EXISTS idx_orders_platform_order_no
        ON orders(platform, platform_order_no);

        CREATE INDEX IF NOT EXISTS idx_orders_latest_job_id
        ON orders(latest_job_id);

        CREATE INDEX IF NOT EXISTS idx_order_items_order_id
        ON order_items(order_id);

        CREATE INDEX IF NOT EXISTS idx_order_events_order_id_created_at
        ON order_events(order_id, created_at);
        """
    )
    _repair_existing_data(conn)
    conn.commit()


def _repair_existing_data(conn: sqlite3.Connection) -> None:
    """修复旧版本数据库中可能存在的空值。

    早期 PoC 迭代可能留下 `print_count` 为空的历史行，正式查询前统一补成 1，
    避免 UI 序列化订单时被旧数据拖崩。
    """
    conn.execute("UPDATE orders SET print_count = 1 WHERE print_count IS NULL")
