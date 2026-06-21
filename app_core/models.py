"""核心数据对象。

本模块定义 Python 后端内部传递的订单、商品和打印任务对象。
SQLite 表结构以 docs/database-schema.md 为准，本文件只提供类型化载体。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ItemDraft:
    """解析阶段生成的商品草稿。"""

    name: str
    quantity: str | None = None
    price: str | None = None
    item_options: str | None = None
    raw_text: str | None = None


@dataclass(slots=True)
class OrderDraft:
    """解析阶段生成的订单草稿。

    这个对象不包含数据库内部字段，例如 id、created_at 和 print_count。
    """

    platform_order_no: str | None
    display_no: str
    order_time: str | None = None
    order_type: str | None = None
    pickup_method: str | None = None
    location: str | None = None
    order_note: str | None = None
    items: list[ItemDraft] = field(default_factory=list)


@dataclass(slots=True)
class OrderItem:
    """数据库中的商品行。"""

    id: str
    order_id: str
    sort_order: int
    name: str
    quantity: str | None
    price: str | None
    item_options: str | None
    raw_text: str | None

    def to_ui_dict(self) -> dict[str, Any]:
        """转换成 Web UI 使用的 JSON 字典。"""
        return {
            "id": self.id,
            "sortOrder": self.sort_order,
            "name": self.name,
            "quantity": self.quantity or "",
            "price": self.price or "",
            "itemOptions": self.item_options or "",
            "rawText": self.raw_text or "",
        }


@dataclass(slots=True)
class Order:
    """数据库中的业务订单。"""

    id: str
    dedupe_key: str | None
    created_at: str
    updated_at: str
    status: str
    platform: str
    platform_order_no: str | None
    display_no: str
    order_time: str | None
    order_type: str | None
    pickup_method: str | None
    location: str | None
    order_note: str | None
    raw_text: str
    first_job_id: str
    latest_job_id: str
    print_count: int
    items: list[OrderItem] = field(default_factory=list)

    def to_ui_dict(self) -> dict[str, Any]:
        """转换成 Web UI 使用的 JSON 字典。"""
        return {
            "id": self.id,
            "dedupeKey": self.dedupe_key,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "status": self.status,
            "platform": self.platform,
            "platformOrderNo": self.platform_order_no or "",
            "displayNo": self.display_no,
            "orderTime": self.order_time or "",
            "orderType": self.order_type or "",
            "pickupMethod": self.pickup_method or "",
            "location": self.location or "",
            "orderNote": self.order_note or "",
            "rawText": self.raw_text,
            "printCount": self.print_count,
            "items": [item.to_ui_dict() for item in self.items],
        }

