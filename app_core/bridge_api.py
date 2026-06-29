"""pywebview bridge API。

本模块暴露给 Web UI 调用的 Python 方法。所有方法返回 JSON 可序列化
字典，避免 JS 直接理解 Python 异常或数据库对象。
"""

from __future__ import annotations

import os

from .database import app_data_dir
from .errors import ReceiptVoiceError
from .llm_parser import get_deepseek_balance, test_llm_connection
from .monitor import MultiReceiptMonitor
from .order_store import OrderStore
from .printer import create_or_fix_proxy_printer, get_printer_diagnostics, list_real_printers, test_printer, test_printer_gdi
from .settings import SettingsStore
from .speech import SpeechService


class BridgeApi:
    """Web UI 可调用的本地后端 API。"""

    def __init__(
        self,
        *,
        store: OrderStore,
        settings: SettingsStore,
        monitor: MultiReceiptMonitor,
        speech: SpeechService,
    ):
        """保存各后端服务实例。"""
        self.store = store
        self.settings = settings
        self.monitor = monitor
        self.speech = speech

    def get_orders(self) -> dict:
        """返回待制作订单队列。"""
        return self._ok({"orders": [order.to_ui_dict() for order in self.store.get_pending_orders()]})

    def get_current_order(self) -> dict:
        """返回第一张待制作订单。"""
        orders = self.store.get_pending_orders()
        return self._ok({"order": orders[0].to_ui_dict() if orders else None})

    def get_history_orders(self, limit: int = 50) -> dict:
        """返回最近完成的历史订单。"""
        try:
            safe_limit = max(1, min(int(limit), 200))
            return self._ok({"orders": [order.to_ui_dict() for order in self.store.get_done_orders(safe_limit)]})
        except Exception as exc:
            return self._error(exc)

    def complete_order(self, order_id: str) -> dict:
        """完成指定订单。"""
        try:
            completed = self.store.complete_order(order_id)
            return self._ok({"completed": completed, "orders": self._ui_orders()})
        except Exception as exc:
            return self._error(exc)

    def undo_complete(self) -> dict:
        """撤销最近完成订单。"""
        try:
            restored = self.store.undo_last_done()
            return self._ok({
                "restored": restored.to_ui_dict() if restored else None,
                "orders": self._ui_orders(),
            })
        except Exception as exc:
            return self._error(exc)

    def restore_order(self, order_id: str) -> dict:
        """从历史订单恢复指定订单。"""
        try:
            restored = self.store.restore_order(order_id)
            return self._ok({
                "restored": restored.to_ui_dict() if restored else None,
                "orders": self._ui_orders(),
            })
        except Exception as exc:
            return self._error(exc)

    def speak_full(self, order_id: str) -> dict:
        """朗读订单全文。"""
        return self._speak_order(order_id, self.speech.speak_full)

    def speak_overview(self, order_id: str) -> dict:
        """朗读订单概览。"""
        return self._speak_order(order_id, self.speech.speak_overview)

    def speak_switch_order(self, order_id: str) -> dict:
        """朗读切换订单后的短提示。"""
        return self._speak_order(order_id, self.speech.speak_switch_order)

    def speak_items(self, order_id: str) -> dict:
        """朗读订单商品列表。"""
        return self._speak_order(order_id, self.speech.speak_items)

    def speak_item(self, order_id: str, item_index: int) -> dict:
        """朗读指定商品。"""
        try:
            order = self.store.get_order(order_id)
            text = self.speech.speak_item(order, int(item_index))
            return self._ok({"spokenText": text})
        except Exception as exc:
            return self._error(exc)

    def speak_text(self, text: str) -> dict:
        """朗读一段 UI 反馈文本。"""
        try:
            spoken = self.speech.speak(text)
            return self._ok({"spokenText": spoken})
        except Exception as exc:
            return self._error(exc)

    def stop_speaking(self) -> dict:
        """停止当前朗读。"""
        try:
            self.speech.stop()
            return self._ok({})
        except Exception as exc:
            return self._error(exc)

    def cycle_rate(self) -> dict:
        """循环 TTS 语速。"""
        try:
            text = self.speech.cycle_rate()
            return self._ok({"spokenText": text, "settings": self.settings.get_all()})
        except Exception as exc:
            return self._error(exc)

    def get_settings(self) -> dict:
        """返回全部设置和打印机列表。"""
        settings = self.settings.get_all()
        proxy = settings.get("proxy_printer_name", "Receipt Voice Proxy")
        return self._ok({
            "settings": settings,
            "printers": list_real_printers(exclude=proxy),
        })

    def save_settings(self, settings: dict) -> dict:
        """保存设置。"""
        try:
            settings = dict(settings or {})
            proxy = self.settings.get("proxy_printer_name", "Receipt Voice Proxy")
            target = str(settings.get("target_printer_name", "")).strip()
            if target and target.casefold() == proxy.casefold():
                settings["target_printer_name"] = ""
                self.settings.save_many(settings)
                return self._error_text("转发目标不能选择代理打印机，请选择真实小票机。")
            return self._ok({"settings": self.settings.save_many(settings)})
        except Exception as exc:
            return self._error(exc)

    def test_llm_connection(self, settings: dict | None = None) -> dict:
        """测试 LLM provider 是否支持结构化输出。"""
        try:
            message = test_llm_connection(self.settings, settings)
            return self._ok({"message": message})
        except Exception as exc:
            return self._error(exc)

    def get_llm_balance(self, settings: dict | None = None) -> dict:
        """查询 DeepSeek 账号余额。"""
        try:
            balance = get_deepseek_balance(self.settings, settings)
            return self._ok({"balance": balance})
        except Exception as exc:
            return self._error(exc)

    def get_monitor_status(self) -> dict:
        """返回监听服务状态。"""
        return self._ok({"status": self.monitor.status().to_dict()})

    def start_monitor(self, print_method: str | None = None) -> dict:
        """启动监听服务。"""
        try:
            self.monitor.start(print_method)
            return self.get_monitor_status()
        except Exception as exc:
            return self._error(exc)

    def stop_monitor(self) -> dict:
        """停止监听服务。"""
        try:
            self.monitor.stop()
            return self.get_monitor_status()
        except Exception as exc:
            return self._error(exc)

    def open_data_folder(self) -> dict:
        """打开应用数据目录。"""
        try:
            os.startfile(app_data_dir())
            return self._ok({})
        except Exception as exc:
            return self._error(exc)

    def test_printer(self, printer_name: str | None = None) -> dict:
        """向真实小票机发送测试小票。"""
        try:
            target = str(printer_name or self.settings.get("target_printer_name", "")).strip()
            result = test_printer(target)
            return self._ok({
                "message": f"{result.get('message', '测试任务已提交')} 打印机：{target}",
                "job": result,
            })
        except Exception as exc:
            return self._error(exc)

    def test_printer_gdi(self, printer_name: str | None = None) -> dict:
        """向真实小票机发送 Windows 普通打印测试页。"""
        try:
            target = str(printer_name or self.settings.get("target_printer_name", "")).strip()
            result = test_printer_gdi(target)
            return self._ok({
                "message": f"{result.get('message', '普通打印测试已提交')} 打印机：{target}",
                "job": result,
            })
        except Exception as exc:
            return self._error(exc)

    def get_printer_diagnostics(self, printer_name: str | None = None) -> dict:
        """返回真实小票机的 Windows 驱动、端口和队列诊断信息。"""
        try:
            target = str(printer_name or self.settings.get("target_printer_name", "")).strip()
            return self._ok({"diagnostics": get_printer_diagnostics(target)})
        except Exception as exc:
            return self._error(exc)

    def create_proxy_printer(self) -> dict:
        """创建或修复 Windows 代理打印机。"""
        try:
            proxy = self.settings.get("proxy_printer_name", "Receipt Voice Proxy")
            target = self.settings.get("target_printer_name", "")
            if not target:
                return self._error_text("请先选择真实小票机。")
            if target.casefold() == proxy.casefold():
                return self._error_text("代理打印机不能作为转发目标，请选择真实小票机。")
            output = create_or_fix_proxy_printer(proxy, target, port=self.monitor.usb_port())
            return self._ok({"output": output})
        except Exception as exc:
            return self._error(exc)

    def _speak_order(self, order_id: str, fn) -> dict:
        """按订单 ID 调用具体朗读函数。"""
        try:
            order = self.store.get_order(order_id)
            text = fn(order)
            return self._ok({"spokenText": text})
        except Exception as exc:
            return self._error(exc)

    def _ui_orders(self) -> list[dict]:
        """返回 UI 使用的订单列表。"""
        return [order.to_ui_dict() for order in self.store.get_pending_orders()]

    @staticmethod
    def _ok(data: dict) -> dict:
        """包装成功响应。"""
        return {"ok": True, "data": data}

    @staticmethod
    def _error(exc: Exception) -> dict:
        """包装异常响应。"""
        if isinstance(exc, ReceiptVoiceError):
            return {
                "ok": False,
                "code": exc.code,
                "error": exc.message,
                "detail": exc.detail,
            }
        return {
            "ok": False,
            "code": exc.__class__.__name__,
            "error": str(exc),
            "detail": "",
        }

    @staticmethod
    def _error_text(message: str) -> dict:
        """包装业务错误响应。"""
        return {"ok": False, "code": "business_error", "error": message, "detail": ""}
