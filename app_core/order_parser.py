"""订单结构化解析。

本模块把 raw_text 转换成 OrderDraft。第一版使用规则解析覆盖当前美团、
银豹 text 小票；后续 LLM structured output 可以接在同一个接口后面。
"""

from __future__ import annotations

import re

from .ids import now_iso
from .models import ItemDraft, OrderDraft


def parse_order_text(platform: str, raw_text: str) -> OrderDraft:
    """根据平台把小票文本解析成订单草稿。"""
    lines = _clean_lines(raw_text)
    if platform == "meituan":
        return _parse_meituan(lines, raw_text)
    if platform == "yinbao":
        return _parse_yinbao(lines, raw_text)
    return _fallback_draft(raw_text)


def detect_platform_from_text(raw_text: str, fallback: str) -> str:
    """根据小票文本特征纠正平台。

    这层用于防止代理打印机端口配错时，美团文本从网口入口进来后被记成银豹。
    """
    text = raw_text.replace(" ", "")
    if "美团" in text or "顾客到店自取" in text or "立即送达" in text:
        return "meituan"
    if "单据号" in text or "牌号" in text or "收银员" in text:
        return "yinbao"
    return fallback


def normalize_order_draft(platform: str, raw_text: str, draft: OrderDraft) -> OrderDraft:
    """用确定性规则规范 LLM 或规则解析出的订单草稿。"""
    lines = _clean_lines(raw_text)
    if platform == "meituan":
        first_no = _first_match(lines, r"#\s*(\d+)") or _digits_or_none(draft.platform_order_no)
        return OrderDraft(
            platform_order_no=first_no,
            display_no=f"美团 #{first_no}" if first_no else draft.display_no,
            order_time=draft.order_time or _value_after(lines, "下单时间"),
            order_type=_parse_meituan_order_type(lines),
            pickup_method=None,
            location=None,
            order_note=_value_after(lines, "备注") or draft.order_note,
            items=draft.items,
        )
    if platform == "yinbao":
        business_no = _value_after(lines, "单据号") or _value_after(lines, "消费流水") or draft.platform_order_no
        location = _format_yinbao_location(_extract_yinbao_location(lines) or draft.location)
        order_type = _line_exact(lines, "堂食") or _line_exact(lines, "外带") or _value_after(lines, "点送方式") or draft.order_type
        return OrderDraft(
            platform_order_no=business_no,
            display_no=f"银豹 {location}" if location else draft.display_no,
            order_time=draft.order_time or _value_after(lines, "下单时间") or _value_after(lines, "打印时间"),
            order_type=order_type,
            pickup_method=None if draft.pickup_method == order_type else draft.pickup_method,
            location=location,
            order_note=draft.order_note,
            items=_normalize_yinbao_items_from_raw(draft.items),
        )
    return draft


def _parse_meituan(lines: list[str], raw_text: str) -> OrderDraft:
    """解析美团小票。"""
    if not _looks_like_meituan(lines):
        return _fallback_draft(raw_text)
    first_no = _first_match(lines, r"#\s*(\d+)")
    display_no = f"美团 #{first_no}" if first_no else _fallback_display_no()
    order_time = _value_after(lines, "下单时间")
    order_type = _parse_meituan_order_type(lines)
    order_note = _value_after(lines, "备注")
    item = _parse_meituan_item(lines)
    return OrderDraft(
        platform_order_no=first_no,
        display_no=display_no,
        order_time=order_time,
        order_type=order_type,
        pickup_method=None,
        location=None,
        order_note=order_note,
        items=[item] if item else [],
    )


def _parse_yinbao(lines: list[str], raw_text: str) -> OrderDraft:
    """解析银豹小票。"""
    business_no = _value_after(lines, "单据号") or _value_after(lines, "消费流水")
    order_time = _value_after(lines, "下单时间") or _value_after(lines, "打印时间")
    location = _format_yinbao_location(_extract_yinbao_location(lines))
    order_type = _line_exact(lines, "堂食") or _line_exact(lines, "外带") or _value_after(lines, "点送方式")
    display_no = f"银豹 {location}" if location else "银豹订单"
    items = _parse_yinbao_items(lines)
    return OrderDraft(
        platform_order_no=business_no,
        display_no=display_no,
        order_time=order_time,
        order_type=order_type,
        pickup_method=None,
        location=location,
        order_note=None,
        items=items,
    )


def _parse_meituan_item(lines: list[str]) -> ItemDraft | None:
    """解析美团当前已知的单商品区域。"""
    start_index = _index_of_line_matching(lines, r".*号口袋.*")
    if start_index is None:
        return None
    chunk = lines[start_index + 1 :]
    name = None
    options: list[str] = []
    quantity = None
    price = None
    raw: list[str] = []
    for line in chunk:
        if "****" in line or "条形码" in line:
            break
        raw.append(line)
        if line.startswith("-"):
            options.append(_clean_option(line))
            continue
        qty_price = re.search(r"\*?\s*(\d+)\s+([\d.]+)", line)
        if qty_price:
            quantity, price = qty_price.group(1), qty_price.group(2)
            continue
        if not name and _looks_like_item_name(line):
            name = line
    if not name:
        return None
    return ItemDraft(
        name=name,
        quantity=quantity,
        price=price,
        item_options="，".join(options) or None,
        raw_text="\n".join(raw),
    )


def _parse_yinbao_items(lines: list[str]) -> list[ItemDraft]:
    """解析银豹商品区，覆盖当前真实样例和测试样例。"""
    start = _index_of_line_containing(lines, "商品名称")
    if start is None:
        return []
    end = _first_index_after(lines, start, ("原价", "总计", "现价", "实收", "堂食", "外带"))
    chunk = lines[start + 1 : end]
    if not chunk:
        return []
    bracket_index = _first_index_matching(chunk, r"^\[.+\]")
    if bracket_index is not None:
        return [_parse_yinbao_bracket_item(chunk[bracket_index:])]
    return _parse_simple_yinbao_items(chunk)


def _parse_yinbao_bracket_item(chunk: list[str]) -> ItemDraft:
    """解析类似 [填好肚子] 瑞士卷 原味瑞士卷 的银豹商品。"""
    first = chunk[0]
    category = re.sub(r"^\[(.+?)\].*$", r"\1", first).strip()
    numbers = re.findall(r"[\d.]+", first)
    price = numbers[0] if numbers else None
    quantity = numbers[1] if len(numbers) > 1 else None
    name_line = chunk[1] if len(chunk) > 1 else ""
    option_line = chunk[2] if len(chunk) > 2 else ""
    option_line = re.sub(r"￥.*$", "", option_line).strip()
    name = " ".join(part for part in (category, name_line) if part).strip()
    return ItemDraft(
        name=name or category or "未命名商品",
        quantity=quantity,
        price=price,
        item_options=option_line or None,
        raw_text="\n".join(chunk),
    )


def _parse_simple_yinbao_items(chunk: list[str]) -> list[ItemDraft]:
    """解析银豹测试小票中商品名和数量金额分行的情况。"""
    items: list[ItemDraft] = []
    index = 0
    while index < len(chunk):
        line = chunk[index]
        if not _looks_like_item_name(line):
            index += 1
            continue
        name = line
        quantity = None
        price = None
        note = None
        raw = [line]
        if index + 1 < len(chunk):
            numbers = re.findall(r"[\d.]+", chunk[index + 1])
            if numbers:
                quantity = numbers[0]
                price = numbers[-1]
                raw.append(chunk[index + 1])
                index += 1
        if index + 1 < len(chunk) and _looks_like_item_name(chunk[index + 1]):
            note = chunk[index + 1]
            raw.append(chunk[index + 1])
            index += 1
        items.append(
            ItemDraft(
                name=name,
                quantity=quantity,
                price=price,
                item_options=note,
                raw_text="\n".join(raw),
            )
        )
        index += 1
    return items


def _normalize_yinbao_items_from_raw(items: list[ItemDraft]) -> list[ItemDraft]:
    """根据银豹商品原文校正 LLM 可能解析反的数量和价格。"""
    return [_normalize_yinbao_item_from_raw(item) for item in items]


def _normalize_yinbao_item_from_raw(item: ItemDraft) -> ItemDraft:
    """用商品 raw_text 的表格形态修正一个银豹商品。"""
    raw_lines = _clean_lines(item.raw_text or "")
    inline = _parse_yinbao_inline_item_line(raw_lines[0]) if raw_lines else None
    if inline:
        parsed_name, price, quantity = inline
        name = item.name
        if not name or _has_yinbao_number_columns(name):
            name = _clean_yinbao_inline_name(parsed_name)
        return ItemDraft(
            name=name,
            quantity=quantity,
            price=price,
            item_options=item.item_options,
            raw_text=item.raw_text,
        )

    quantity_price = _parse_yinbao_quantity_price_line(raw_lines)
    if quantity_price:
        quantity, price = quantity_price
        return ItemDraft(
            name=item.name,
            quantity=quantity,
            price=price,
            item_options=item.item_options,
            raw_text=item.raw_text,
        )
    return item


def _has_yinbao_number_columns(value: str) -> bool:
    """判断商品名是否还带着银豹的 ``单价 数量 小计`` 数字列。"""
    return bool(re.search(r"\s+[\d.]+\s+[\d.]+\s+[\d.]+$", value))


def _clean_yinbao_inline_name(value: str) -> str:
    """清理银豹同一行商品解析出的名称。"""
    text = value.strip()
    bracket = re.fullmatch(r"\[(.+?)\]", text)
    return bracket.group(1).strip() if bracket else text


def _parse_yinbao_inline_item_line(line: str) -> tuple[str, str, str] | None:
    """解析 ``商品名 单价 数量 小计`` 在同一行的银豹商品。"""
    match = re.match(r"^(.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$", line)
    if not match:
        return None
    name = match.group(1).strip()
    price = match.group(2)
    quantity = match.group(3)
    return name, price, quantity


def _parse_yinbao_quantity_price_line(lines: list[str]) -> tuple[str, str] | None:
    """解析银豹商品名下一行只有 ``数量 小计/价格`` 的情况。"""
    for line in lines[1:]:
        if not re.fullmatch(r"[\d.\s]+", line):
            continue
        numbers = re.findall(r"[\d.]+", line)
        if len(numbers) >= 2:
            return numbers[0], numbers[-1]
    return None


def _fallback_draft(raw_text: str) -> OrderDraft:
    """生成未解析订单草稿。"""
    _ = raw_text
    return OrderDraft(
        platform_order_no=None,
        display_no=_fallback_display_no(),
        items=[],
    )


def _clean_lines(raw_text: str) -> list[str]:
    """清理空行和分隔线。"""
    lines = []
    for line in raw_text.splitlines():
        value = line.strip().strip("*")
        if value and not set(value) <= {"-", "—", "_"}:
            lines.append(value)
    return lines


def _value_after(lines: list[str], prefix: str) -> str | None:
    """读取 ``前缀：值`` 或 ``前缀: 值`` 的值。"""
    for line in lines:
        if line.startswith(prefix):
            value = re.sub(rf"^{re.escape(prefix)}\s*[:：]?\s*", "", line).strip()
            value = value.strip("；;，, ")
            return value or None
    return None


def _extract_yinbao_location(lines: list[str]) -> str | None:
    """从银豹牌号行中提取位置。"""
    for line in lines:
        match = re.search(r"牌号[:：]\s*(.+)$", line)
        if match:
            return match.group(1).strip()
    return None


def _format_yinbao_location(value: str | None) -> str | None:
    """把银豹牌号格式化成桌号或吧台号。"""
    if not value:
        return None
    text = value.strip()
    if re.match(r"^P[\w-]*$", text, re.IGNORECASE):
        return f"吧台 {text.upper()}"
    return text


def _parse_meituan_order_type(lines: list[str]) -> str:
    """解析美团订单类型，当前只区分外卖和堂食。"""
    joined = "\n".join(lines)
    if "立即送达" in joined or "骑手" in joined or "外卖" in joined:
        return "外卖"
    return "堂食"


def _first_match(lines: list[str], pattern: str) -> str | None:
    """返回第一个正则捕获。"""
    regex = re.compile(pattern)
    for line in lines:
        match = regex.search(line)
        if match:
            return match.group(1)
    return None


def _digits_or_none(value: str | None) -> str | None:
    """从不稳定模型输出中提取数字单号。"""
    if not value:
        return None
    match = re.search(r"\d+", value)
    return match.group(0) if match else None


def _line_contains(lines: list[str], text: str) -> str | None:
    """返回包含指定文本的第一行。"""
    for line in lines:
        if text in line:
            return line
    return None


def _line_exact(lines: list[str], text: str) -> str | None:
    """返回完全匹配的行。"""
    return text if text in lines else None


def _line_matches(lines: list[str], pattern: str) -> str | None:
    """返回匹配正则的第一行。"""
    regex = re.compile(pattern)
    for line in lines:
        if regex.match(line):
            return line
    return None


def _index_of_line_containing(lines: list[str], text: str) -> int | None:
    """返回包含指定文本的第一行索引。"""
    for index, line in enumerate(lines):
        if text in line:
            return index
    return None


def _index_of_line_matching(lines: list[str], pattern: str) -> int | None:
    """返回匹配正则的第一行索引。"""
    regex = re.compile(pattern)
    for index, line in enumerate(lines):
        if regex.match(line):
            return index
    return None


def _first_index_after(lines: list[str], start: int, prefixes: tuple[str, ...]) -> int:
    """返回 start 后第一个以指定前缀开头的索引。"""
    for index in range(start + 1, len(lines)):
        if lines[index].startswith(prefixes):
            return index
    return len(lines)


def _first_index_matching(lines: list[str], pattern: str) -> int | None:
    """返回列表内第一个正则匹配索引。"""
    regex = re.compile(pattern)
    for index, line in enumerate(lines):
        if regex.match(line):
            return index
    return None


def _clean_option(line: str) -> str:
    """清理美团商品属性前缀。"""
    return line.lstrip("-").replace("【", "").replace("】", "").strip()


def _looks_like_meituan(lines: list[str]) -> bool:
    """判断文本是否具备美团小票的基本特征。"""
    markers = ("美团", "顾客到店自取", "骑手取餐", "号口袋")
    return any(any(marker in line for marker in markers) for line in lines)


def _looks_like_item_name(line: str) -> bool:
    """粗略判断一行是否像商品名。"""
    if re.fullmatch(r"[\d.\s]+", line):
        return False
    blocked = ("商品名称", "单价", "数量", "小计", "原价", "现价", "实收", "支付", "找零")
    return not any(word in line for word in blocked)


def _fallback_display_no() -> str:
    """生成未解析订单展示名。"""
    return f"未解析订单 {now_iso()[11:16]}"
