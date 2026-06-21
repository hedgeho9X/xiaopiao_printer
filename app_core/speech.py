"""TTS 朗读服务。

本模块负责根据订单数据生成朗读文本，并通过 Windows SAPI 播报。
Web UI 只触发行为，不直接调用浏览器 Web Speech。
"""

from __future__ import annotations

import re

from .models import Order, OrderItem
from .settings import SettingsStore


RATE_VALUES = {
    "slow": -4,
    "medium": -2,
    "fast": 0,
    "faster": 3,
    "fastest": 6,
}

RATE_LABELS = {
    "slow": "慢速",
    "medium": "中速",
    "fast": "快速",
    "faster": "更快",
    "fastest": "最快",
}


class SpeechService:
    """订单朗读与 SAPI 调用入口。"""

    def __init__(self, settings: SettingsStore):
        """初始化 TTS 服务。"""
        self.settings = settings
        self._speaker = None

    def speak(self, text: str) -> str:
        """朗读指定文本，并返回实际朗读文本。"""
        if not text.strip():
            return ""
        speaker = self._get_speaker()
        if speaker is None:
            return text
        speaker.Rate = RATE_VALUES.get(self.settings.get("tts_rate", "medium"), -2)
        speaker.Volume = 100
        # 3 = 异步朗读 + 先清空上一段，避免多个快捷键排队叠加。
        speaker.Speak(text, 3)
        return text

    def stop(self) -> None:
        """停止当前朗读。"""
        speaker = self._get_speaker()
        if speaker is not None:
            speaker.Speak("", 3)

    def speak_full(self, order: Order | None) -> str:
        """朗读当前订单全文。"""
        text = build_full_text(order)
        return self.speak(text)

    def speak_overview(self, order: Order | None) -> str:
        """朗读当前订单概览。"""
        text = build_overview_text(order)
        return self.speak(text)

    def speak_switch_order(self, order: Order | None) -> str:
        """朗读切换订单后的短提示。"""
        text = build_switch_order_text(order)
        return self.speak(text)

    def speak_items(self, order: Order | None) -> str:
        """朗读当前订单商品列表。"""
        text = build_items_text(order)
        return self.speak(text)

    def speak_item(self, order: Order | None, item_index: int) -> str:
        """朗读当前订单的某个商品。"""
        text = build_single_item_text(order, item_index)
        return self.speak(text)

    def cycle_rate(self) -> str:
        """循环语速并朗读确认。"""
        rate = self.settings.cycle_rate()
        label = RATE_LABELS.get(rate, rate)
        return self.speak(f"语速已切换为{label}。")

    def _get_speaker(self):
        """懒加载 Windows SAPI speaker。"""
        if self._speaker is not None:
            return self._speaker
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore

            pythoncom.CoInitialize()
            self._speaker = win32com.client.Dispatch("SAPI.SpVoice")
        except Exception:
            self._speaker = None
        return self._speaker


def build_full_text(order: Order | None) -> str:
    """生成“读全文”的朗读文本。"""
    if order is None:
        return "当前没有待制作订单。"
    if not order.items:
        return order.raw_text
    parts = [_order_prefix(order)]
    for item in order.items:
        parts.append(_item_text(item))
    if order.order_note:
        parts.append(f"整单备注：{order.order_note}。")
    return " ".join(part for part in parts if part)


def build_overview_text(order: Order | None) -> str:
    """生成“读概览”的朗读文本。"""
    if order is None:
        return "当前没有待制作订单。"
    if not order.items:
        return f"{_order_prefix(order)} 当前订单没有结构化商品。"
    item_text = "，".join(_item_brief(item) for item in order.items)
    return f"{_order_prefix(order)} {item_text}。共{len(order.items)}项。"


def build_switch_order_text(order: Order | None) -> str:
    """生成上一单/下一单切换后的短朗读文本。"""
    if order is None:
        return "当前没有待制作订单。"
    parts = _unique_parts([
        order.display_no,
        _service_type_text(order),
        _short_time_text(order.order_time),
    ])
    return "，".join(parts) + "。"


def build_items_text(order: Order | None) -> str:
    """生成“读商品列表”的朗读文本。"""
    if order is None:
        return "当前没有待制作订单。"
    if not order.items:
        return order.raw_text
    parts = [
        f"第 {index} 项，{_item_text(item)}"
        for index, item in enumerate(order.items, start=1)
    ]
    if order.order_note:
        parts.append(f"整单备注：{order.order_note}。")
    return " ".join(parts)


def build_single_item_text(order: Order | None, item_index: int) -> str:
    """生成单个商品的朗读文本。"""
    if order is None:
        return "当前没有待制作订单。"
    if not order.items:
        return "当前订单没有结构化商品，可以按一读全文。"
    if item_index < 0:
        return "已经是第一个商品。"
    if item_index >= len(order.items):
        return "已经是最后一个商品。"
    item = order.items[item_index]
    return f"第 {item_index + 1} 项，共 {len(order.items)} 项，{_item_text(item)}"


def _order_prefix(order: Order) -> str:
    """生成订单开头信息。"""
    parts = _unique_parts([
        _platform_label(order.platform),
        order.display_no,
        order.order_type or "",
        _pickup_text(order),
        _location_text(order),
        order.order_time or "",
    ])
    return "，".join(part for part in parts if part) + "。"


def _item_text(item: OrderItem) -> str:
    """生成商品完整朗读文本。"""
    parts = [item.name]
    if item.quantity:
        parts.append(f"{item.quantity}份")
    if item.item_options:
        parts.append(item.item_options)
    return "，".join(parts) + "。"


def _item_brief(item: OrderItem) -> str:
    """生成商品概览文本。"""
    if item.quantity:
        return f"{item.name}{item.quantity}份"
    return item.name


def _platform_label(platform: str) -> str:
    """返回平台中文名。"""
    return {"meituan": "美团", "yinbao": "银豹"}.get(platform, "未知平台")


def _pickup_text(order: Order) -> str:
    """返回不和订单类型重复的取件方式。"""
    pickup = (order.pickup_method or "").strip()
    order_type = (order.order_type or "").strip()
    return pickup if pickup and pickup != order_type else ""


def _service_type_text(order: Order) -> str:
    """返回切单时需要播报的堂食/外卖/外带信息。"""
    text = " ".join([
        order.order_type or "",
        order.pickup_method or "",
        order.raw_text or "",
    ])
    if "堂食" in text or "自取" in text:
        return "堂食"
    if "外卖" in text or "立即送达" in text or "骑手" in text:
        return "外卖"
    if "外带" in text:
        return "外带"
    return (order.order_type or order.pickup_method or "").strip()


def _location_text(order: Order) -> str:
    """按平台格式化位置朗读文本。"""
    location = (order.location or "").strip()
    if not location or order.platform == "meituan":
        return ""
    if re.match(r"^吧台\s*P", location, re.IGNORECASE):
        return re.sub(r"^吧台\s*", "吧台 ", location, flags=re.IGNORECASE)
    if re.match(r"^P[\w-]*$", location, re.IGNORECASE):
        return f"吧台 {location.upper()}"
    if "桌" in location:
        return f"桌号 {location}"
    return location


def _short_time_text(value: str | None) -> str:
    """把订单时间读成不含年份、不含秒的短时间。"""
    text = (value or "").strip()
    if not text:
        return ""
    date_match = re.search(
        r"(?:\d{4}[-/年])?(\d{1,2})[-/月](\d{1,2})(?:日)?[ T]+(\d{1,2}):(\d{2})",
        text,
    )
    if date_match:
        month, day, hour, minute = date_match.groups()
        return f"{int(month)}月{int(day)}日{int(hour)}点{minute}分"
    time_match = re.search(r"(\d{1,2}):(\d{2})", text)
    if time_match:
        hour, minute = time_match.groups()
        return f"{int(hour)}点{minute}分"
    return text


def _unique_parts(values: list[str]) -> list[str]:
    """去掉空值和重复朗读片段。"""
    result = []
    seen = set()
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
