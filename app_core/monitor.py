"""打印监听服务。

本模块负责监听本机 TCP 端口，接收 POS 软件发来的打印 bytes，
保存 print_jobs、原样转发到真实打印机，并将文本解析成订单。
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass

from .llm_parser import parse_order_text_with_llm
from .order_parser import detect_platform_from_text, normalize_order_draft, parse_order_text
from .order_store import OrderStore
from .printer import send_raw_to_printer
from .receipt_parser import parse_receipt_text
from .settings import PRINT_METHOD_LABELS, SettingsStore


HOST = "127.0.0.1"
PORT = 9100


@dataclass(slots=True)
class MonitorStatus:
    """监听服务状态快照。"""

    running: bool
    host: str
    port: int
    print_method: str
    platform: str
    received_count: int
    forwarded_count: int
    failed_count: int
    last_error: str | None = None

    def to_dict(self) -> dict:
        """转换为 bridge 返回的 JSON 字典。"""
        return {
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "printMethod": self.print_method,
            "printMethodLabel": PRINT_METHOD_LABELS.get(self.print_method, self.print_method),
            "platform": self.platform,
            "receivedCount": self.received_count,
            "forwardedCount": self.forwarded_count,
            "failedCount": self.failed_count,
            "lastError": self.last_error or "",
        }


class ReceiptMonitor:
    """后台 TCP 打印监听器。"""

    def __init__(
        self,
        settings: SettingsStore,
        store: OrderStore,
        host: str = HOST,
        port: int = PORT,
        print_method: str = "usb_print",
    ):
        """初始化监听器。"""
        self.settings = settings
        self.store = store
        self.host = host
        self.port = port
        self.print_method = print_method
        self.platform = self.settings.platform_for_method(self.print_method)
        self.received_count = 0
        self.forwarded_count = 0
        self.failed_count = 0
        self.last_error: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self, print_method: str | None = None) -> None:
        """启动监听线程。"""
        if self.is_running():
            return
        self.print_method = print_method or self.print_method
        self.platform = self.settings.platform_for_method(self.print_method)
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="receipt-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止监听线程。"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def is_running(self) -> bool:
        """判断监听线程是否仍在运行。"""
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> MonitorStatus:
        """返回当前监听状态。"""
        return MonitorStatus(
            running=self.is_running(),
            host=self.host,
            port=self.port,
            print_method=self.print_method,
            platform=self.platform,
            received_count=self.received_count,
            forwarded_count=self.forwarded_count,
            failed_count=self.failed_count,
            last_error=self.last_error,
        )

    def handle_bytes(self, data: bytes) -> str:
        """处理一份完整打印 bytes，并返回关联订单 ID。"""
        parsed = parse_receipt_text(data)
        platform = detect_platform_from_text(parsed.text, self.platform)
        job_id = self.store.create_print_job(
            print_method=self.print_method,
            platform=platform,
            raw_bytes=data,
            raw_text=parsed.text,
        )
        self._forward(job_id, data)
        try:
            draft = self._parse_order(platform, parsed.text)
            order_id = self.store.upsert_order_from_draft(
                job_id=job_id,
                platform=platform,
                raw_text=parsed.text,
                draft=draft,
            )
        except Exception as exc:
            self.last_error = f"订单解析失败：{exc}"
            fallback = normalize_order_draft(platform, parsed.text, parse_order_text(platform, parsed.text))
            order_id = self.store.upsert_order_from_draft(
                job_id=job_id,
                platform=platform,
                raw_text=parsed.text,
                draft=fallback,
            )
        with self._lock:
            self.received_count += 1
        return order_id

    def _parse_order(self, platform: str, raw_text: str):
        """优先使用 LLM 结构化解析，失败时回退到规则解析。"""
        try:
            draft = parse_order_text_with_llm(self.settings, platform, raw_text)
            if draft is not None:
                return normalize_order_draft(platform, raw_text, draft)
        except Exception as exc:
            self.last_error = f"LLM 解析失败，已回退规则解析：{exc}"
        return normalize_order_draft(platform, raw_text, parse_order_text(platform, raw_text))

    def _run(self) -> None:
        """监听 TCP 端口并按连接读取打印数据。"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.host, self.port))
                server.listen()
                server.settimeout(0.5)
                while not self._stop_event.is_set():
                    try:
                        client, _address = server.accept()
                    except socket.timeout:
                        continue
                    with client:
                        chunks: list[bytes] = []
                        while True:
                            piece = client.recv(4096)
                            if not piece:
                                break
                            chunks.append(piece)
                    if chunks:
                        self.handle_bytes(b"".join(chunks))
        except Exception as exc:
            self.last_error = str(exc)
            self.failed_count += 1

    def _forward(self, job_id: str, data: bytes) -> None:
        """把原始 bytes 转发到真实小票机，并记录结果。"""
        target = self.settings.get("target_printer_name", "").strip()
        proxy = self.settings.get("proxy_printer_name", "Receipt Voice Proxy").strip()
        if not target:
            message = "未设置真实小票机，已跳过转发。"
            self.store.update_forward_result(job_id, forwarded=False, forward_error=message)
            self.last_error = message
            self.failed_count += 1
            return
        if proxy and target.casefold() == proxy.casefold():
            message = "转发目标不能是代理打印机，已跳过转发以避免循环打印。"
            self.store.update_forward_result(job_id, forwarded=False, forward_error=message)
            self.last_error = message
            self.failed_count += 1
            return
        try:
            send_raw_to_printer(target, data)
            self.store.update_forward_result(job_id, forwarded=True)
            self.forwarded_count += 1
        except Exception as exc:
            message = str(exc)
            self.store.update_forward_result(job_id, forwarded=False, forward_error=message)
            self.last_error = message
            self.failed_count += 1


@dataclass(slots=True)
class MultiMonitorStatus:
    """双入口监听状态快照。"""

    monitors: list[MonitorStatus]

    def to_dict(self) -> dict:
        """转换为 bridge 返回的 JSON 字典。"""
        return {
            "running": all(monitor.running for monitor in self.monitors),
            "monitors": [monitor.to_dict() for monitor in self.monitors],
            "lastError": "；".join(
                monitor.last_error or "" for monitor in self.monitors if monitor.last_error
            ),
        }


class MultiReceiptMonitor:
    """同时管理网口打印和 USB 代理打印两个监听入口。"""

    def __init__(self, settings: SettingsStore, store: OrderStore, host: str = HOST):
        """按配置创建两个独立监听器。"""
        self.settings = settings
        self.monitors = {
            "network_print": ReceiptMonitor(
                settings,
                store,
                host=host,
                port=self._port("network_print_port", 9100),
                print_method="network_print",
            ),
            "usb_print": ReceiptMonitor(
                settings,
                store,
                host=host,
                port=self._port("usb_print_port", 9101),
                print_method="usb_print",
            ),
        }

    def start(self, print_method: str | None = None) -> None:
        """启动一个入口或全部入口；默认两个入口同时启动。"""
        if print_method in self.monitors:
            self.monitors[print_method].start(print_method)
            return
        for method, monitor in self.monitors.items():
            monitor.start(method)

    def stop(self) -> None:
        """停止全部监听入口。"""
        for monitor in self.monitors.values():
            monitor.stop()

    def is_running(self) -> bool:
        """判断是否所有入口都在运行。"""
        return all(monitor.is_running() for monitor in self.monitors.values())

    def status(self) -> MultiMonitorStatus:
        """返回双入口状态。"""
        return MultiMonitorStatus(monitors=[monitor.status() for monitor in self.monitors.values()])

    def usb_port(self) -> int:
        """返回 USB 代理打印机应指向的监听端口。"""
        return self._port("usb_print_port", 9101)

    def _port(self, key: str, default: int) -> int:
        """从配置读取端口，配置异常时使用默认值。"""
        try:
            return int(self.settings.get(key, str(default)))
        except ValueError:
            return default
