"""LLM 小票结构化解析。

本模块使用 OpenAI Python SDK 调用 OpenAI-compatible Chat Completions API，
通过 DeepSeek JSON Mode 把 raw_text 解析成 OrderDraft。v1 正式链路只使用
LLM 解析：模型或配置异常会显式报错，不再静默回退到规则解析。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .errors import ConfigError, ParseError
from .models import ItemDraft, OrderDraft
from .settings import DEFAULT_LLM_BASE_URL, SettingsStore


ORDER_JSON_EXAMPLE = {
    "platform_order_no": "202606071052125990004",
    "display_no": "银豹 大桌子 D1",
    "order_time": "2026-06-07 10:52:12",
    "order_type": "堂食",
    "pickup_method": "堂食",
    "location": "大桌子 D1",
    "order_note": None,
    "items": [
        {
            "name": "填好肚子 瑞士卷",
            "quantity": "1",
            "price": "15",
            "item_options": "原味瑞士卷",
            "raw_text": "[填好肚子] 15 1 15\n瑞士卷\n原味瑞士卷￥0/",
        }
    ],
}

TEST_JSON_EXAMPLE = {"ok": True, "message": "连接成功"}


def parse_order_text_with_llm(settings: SettingsStore, platform: str, raw_text: str) -> OrderDraft:
    """使用 LLM 解析小票文本。

    Args:
        settings: 应用配置仓库。
        platform: 当前打印入口映射的平台提示。
        raw_text: 已清洗后的可读小票文本。
    """
    payload = _request_structured_json(
        settings=_settings_from_store(settings),
        system_prompt=_order_system_prompt(),
        user_prompt=_order_user_prompt(platform, raw_text),
    )
    return _payload_to_draft(payload)


def test_llm_connection(settings: SettingsStore, overrides: dict[str, Any] | None = None) -> str:
    """测试当前或临时 LLM 配置是否能完成结构化输出。"""
    provider_settings = _settings_from_store(settings)
    if overrides:
        provider_settings.update({key: str(value).strip() for key, value in overrides.items()})

    payload = _request_structured_json(
        settings=provider_settings,
        system_prompt=_test_system_prompt(),
        user_prompt="请返回 JSON：ok=true，message=连接成功。",
    )
    if payload.get("ok") is not True:
        raise ParseError(str(payload.get("message") or "模型没有返回 ok=true。"))
    return str(payload.get("message") or "连接成功")


def get_deepseek_balance(settings: SettingsStore, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """查询 DeepSeek 账号余额。

    Args:
        settings: 应用配置仓库。
        overrides: UI 表单里的临时配置；未保存时也可以用来测试余额。
    """
    provider_settings = _settings_from_store(settings)
    if overrides:
        provider_settings.update({key: str(value).strip() for key, value in overrides.items()})

    api_key = provider_settings.get("llm_api_key", "").strip()
    if not api_key:
        raise ConfigError("未配置 LLM API Key。请在设置里填写 API Key 后再接单。")

    url = _deepseek_balance_url(provider_settings.get("llm_base_url", ""))
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise ConfigError(f"余额查询失败：HTTP {exc.code}", detail=detail) from exc
    except urllib.error.URLError as exc:
        raise ConfigError("余额查询失败。请检查网络或 Base API。", detail=str(exc.reason)) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError("余额接口返回的 JSON 无法解析。", detail=str(exc)) from exc
    if not isinstance(payload, dict):
        raise ParseError("余额接口返回结果不是 JSON 对象。")
    return _normalize_balance_payload(payload)


def _request_structured_json(
    *,
    settings: dict[str, str],
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    """调用 DeepSeek JSON Mode，并返回 JSON 对象。"""
    api_key = settings.get("llm_api_key", "").strip()
    model = settings.get("llm_model", "").strip()
    base_url = settings.get("llm_base_url", "").strip()
    if not api_key:
        raise ConfigError("未配置 LLM API Key。请在设置里填写 API Key。")
    if not model:
        raise ConfigError("未配置 LLM 模型名。请在设置里填写 Model。")

    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise ConfigError("缺少 openai SDK，请先安装 requirements.txt。") from exc

    client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": 20.0}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=2048,
            temperature=0,
        )
    except Exception as exc:
        raise ParseError("LLM 请求失败。真实小票已尝试转发，但订单无法入队。", detail=str(exc)) from exc
    message = completion.choices[0].message
    refusal = getattr(message, "refusal", None)
    if refusal:
        raise ParseError("模型拒绝解析小票。", detail=str(refusal))
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ParseError("模型没有返回可解析 JSON。")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ParseError("模型返回的 JSON 无法解析。", detail=str(exc)) from exc
    if not isinstance(payload, dict):
        raise ParseError("模型返回结果不是 JSON 对象。")
    return payload


def _payload_to_draft(payload: dict[str, Any]) -> OrderDraft:
    """把 LLM JSON 对象转换成 OrderDraft。"""
    display_no = _string_or_none(payload.get("display_no")) or "未解析订单"
    items = [
        ItemDraft(
            name=_string_or_none(item.get("name")) or "未命名商品",
            quantity=_string_or_none(item.get("quantity")),
            price=_string_or_none(item.get("price")),
            item_options=_string_or_none(item.get("item_options")),
            raw_text=_string_or_none(item.get("raw_text")),
        )
        for item in payload.get("items", [])
        if isinstance(item, dict)
    ]
    return OrderDraft(
        platform_order_no=_normalize_platform_order_no(payload.get("platform_order_no")),
        display_no=display_no,
        order_time=_string_or_none(payload.get("order_time")),
        order_type=_string_or_none(payload.get("order_type")),
        pickup_method=_string_or_none(payload.get("pickup_method")),
        location=_string_or_none(payload.get("location")),
        order_note=_string_or_none(payload.get("order_note")),
        items=items,
    )


def _settings_from_store(settings: SettingsStore) -> dict[str, str]:
    """从 app_settings 读取 LLM provider 配置。"""
    return {
        "llm_provider_name": settings.get("llm_provider_name", "DeepSeek"),
        "llm_base_url": settings.get("llm_base_url", DEFAULT_LLM_BASE_URL),
        "llm_api_key": settings.get("llm_api_key", ""),
        "llm_model": settings.get("llm_model", ""),
    }


def _deepseek_balance_url(base_url: str) -> str:
    """根据 Base API 拼出 DeepSeek 余额接口 URL。"""
    base = (base_url or DEFAULT_LLM_BASE_URL).strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/user/balance"


def _normalize_balance_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """只保留 UI 需要展示的余额字段。"""
    infos = payload.get("balance_infos", [])
    if not isinstance(infos, list):
        infos = []
    return {
        "is_available": bool(payload.get("is_available")),
        "balance_infos": [
            {
                "currency": _string_or_empty(item.get("currency")),
                "total_balance": _string_or_empty(item.get("total_balance")),
                "granted_balance": _string_or_empty(item.get("granted_balance")),
                "topped_up_balance": _string_or_empty(item.get("topped_up_balance")),
            }
            for item in infos
            if isinstance(item, dict)
        ],
    }


def _order_system_prompt() -> str:
    """返回小票解析系统提示词。"""
    example = json.dumps(ORDER_JSON_EXAMPLE, ensure_ascii=False, indent=2)
    return (
        "你是咖啡店小票结构化解析器。你必须只输出一个 JSON 对象，不能输出 Markdown，"
        "不能输出解释文字。只从小票原文提取字段，不要编造。"
        "业务情况只有这些："
        "1. 美团外卖：order_type=外卖，location 必须为 null。"
        "2. 美团堂食或到店自取：order_type=堂食，location 必须为 null，不要提取口袋号。"
        "3. 银豹桌上下单：牌号是桌号或包含桌字，order_type 只表示堂食/外带，location 写桌号。"
        "4. 银豹吧台点单：牌号以 P 开头，order_type 只表示堂食/外带，location 写吧台 Pxxxx。"
        "如果原文出现 #数字、**#数字美团、单据号或消费流水，必须提取到 platform_order_no。"
        "美团 display_no 使用“美团 #数字”；银豹 display_no 优先使用“银豹 牌号/桌号”。"
        "美团的手机号尾号、口袋号、条形码、取餐口都不是 location。"
        "银豹的牌号、桌号、桌子属于 location，不要放入 order_note。"
        "pickup_method 只在原文有额外取件说明时填写；如果和 order_type 相同就写 null。"
        "商品列表只包含真正需要制作的商品，不要把应收、实收、总计、支付方式、地址、电话当作商品。"
        "银豹商品表头通常是“商品名称 单价 数量 小计”：单价写入 price，数量写入 quantity，小计不要当成数量。"
        "如果银豹商品名下一行只有两个数字，例如“2 68”，通常第一个数字是数量，第二个数字是小计或价格。"
        "商品属性包括温度、甜度、规格、口味、备注等；商品属性要去掉无意义符号，例如【默认】写成默认。"
        "只有明确写着备注、整单备注、顾客备注的内容才放入 order_note。"
        "所有金额、数量、时间都保留原文字符串。缺失字段使用 null，items 缺失时使用空数组。"
        f"JSON 输出格式示例：\n{example}"
    )


def _order_user_prompt(platform: str, raw_text: str) -> str:
    """拼接小票解析用户提示词。"""
    return f"请把下面小票解析成 JSON。\n平台提示：{platform}\n\n小票原文：\n{raw_text}"


def _test_system_prompt() -> str:
    """返回连接测试系统提示词。"""
    example = json.dumps(TEST_JSON_EXAMPLE, ensure_ascii=False, indent=2)
    return f"你只负责测试 JSON 输出能力。必须只输出 JSON 对象，格式示例：\n{example}"


def _string_or_none(value: Any) -> str | None:
    """把空字符串和 None 统一成 None。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_or_empty(value: Any) -> str:
    """把余额接口字段转换成字符串，缺失时返回空字符串。"""
    if value is None:
        return ""
    return str(value).strip()


def _normalize_platform_order_no(value: Any) -> str | None:
    """规范平台单号，避免 #1 和 1 形成不同去重键。"""
    text = _string_or_none(value)
    if text and text.startswith("#"):
        return text.lstrip("#").strip() or None
    return text
