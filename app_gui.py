"""语音小票主监控程序。

这个模块提供图形界面版的打印代理：
1. 创建/修复 Windows 代理打印机 ``Receipt Voice Proxy``。
2. 监听本机 TCP 端口，把美团、银豹等系统发来的小票数据抓下来。
3. 保存原始打印数据、解析结果和预览图，方便后续排查。
4. 将原始 bytes 原样转发到用户选择的真实小票机。

代码约定：
- 原始打印数据永远不做重编码后再转发，只复制一份用于解析。
- 界面状态通过事件队列从后台线程传回主线程，避免 Tkinter 跨线程更新 UI。
"""

import argparse
import datetime as dt
import json
import os
import queue
import socket
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from capture_server import CAPTURE_DIR, save_capture, send_raw_to_printer
from inspect_capture import make_capture_preview, parse_capture
from print_raw import sample_receipt_bytes, send_raw_to_printer as print_to_queue


HOST = "127.0.0.1"
PORT = 9100
DEFAULT_PROXY_PRINTER = "Receipt Voice Proxy"
DEFAULT_TARGET_PRINTER = "GP-5850II"
VIRTUAL_PRINTER_KEYWORDS = ("pdf", "wps", "onenote", "xps", "fax", "microsoft print")
RECEIPT_PRINTER_KEYWORDS = ("gp", "pos", "58", "80", "thermal", "receipt", "esc", "cla", "tech")


def ps_quote(value: str) -> str:
    """把字符串包装成 PowerShell 单引号字面量。

    PowerShell 中单引号内部如果还要表示单引号，需要写成两个单引号。
    这里用于拼接打印机名称，避免名称里有特殊字符时破坏命令。
    """
    return "'" + value.replace("'", "''") + "'"


def create_or_fix_proxy_printer(proxy_name: str, target_printer: str) -> str:
    """创建或修复代理打印机，并返回 PowerShell 输出的 JSON 文本。

    Args:
        proxy_name: 给美团/银豹选择的代理打印机名称。
        target_printer: 当前真实小票机名称，用它的驱动创建代理队列。

    Returns:
        PowerShell 输出的打印机信息 JSON，便于写入界面日志。

    Raises:
        RuntimeError: PowerShell 创建端口或打印机失败时抛出。
    """
    # 代理打印机只负责把 Windows 打印任务投递到本机 9100 端口。
    # 真正打印仍由 Python 收到 bytes 后转发给 target_printer。
    script = f"""
$ProxyPrinterName = {ps_quote(proxy_name)}
$TargetPrinterName = {ps_quote(target_printer)}
$PortName = 'IP_127.0.0.1'
$Target = Get-Printer -Name $TargetPrinterName -ErrorAction Stop
$DriverName = $Target.DriverName

if (-not (Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue)) {{
    Add-PrinterPort -Name $PortName -PrinterHostAddress '127.0.0.1' -PortNumber 9100
}}

$Existing = Get-Printer -Name $ProxyPrinterName -ErrorAction SilentlyContinue
if ($Existing) {{
    Set-Printer -Name $ProxyPrinterName -PortName $PortName
}} else {{
    Add-Printer -Name $ProxyPrinterName -DriverName $DriverName -PortName $PortName
}}

Get-Printer -Name $ProxyPrinterName |
    Select-Object Name,DriverName,PortName,PrinterStatus |
    ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "PowerShell failed").strip())
    return result.stdout.strip()


class Forwarder:
    """后台转发服务。

    这个类运行在独立线程里，负责监听 TCP 端口、保存抓包、解析预览、
    转发真实小票机，并把所有状态通过 event_queue 发回 GUI 主线程。
    """

    def __init__(self, host: str, port: int, event_queue: queue.Queue[dict]):
        """初始化后台转发器。

        Args:
            host: 监听地址，当前固定为 127.0.0.1。
            port: 监听端口，当前固定为 9100。
            event_queue: 线程安全队列，用于向界面发送日志和状态事件。
        """
        self.host = host
        self.port = port
        self.event_queue = event_queue
        self.target_printer = DEFAULT_TARGET_PRINTER
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.server_socket: socket.socket | None = None

    def start(self, target_printer: str) -> None:
        """启动监听线程。

        Args:
            target_printer: 收到小票后要转发到的真实 Windows 打印机名称。
        """
        if self.thread and self.thread.is_alive():
            self.log("Forwarder is already running.")
            return
        self.target_printer = target_printer
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="receipt-forwarder", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """请求停止监听线程。

        关闭 socket 可以打断 accept() 阻塞，让后台线程尽快退出。
        """
        self.stop_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
        self.log("正在停止转发服务...")

    def log(self, message: str) -> None:
        """向 GUI 投递一条日志事件。"""
        self.event_queue.put({"type": "log", "message": message})

    def status(self, running: bool, message: str) -> None:
        """向 GUI 投递监听状态事件。"""
        self.event_queue.put({"type": "status", "running": running, "message": message})

    def _run(self) -> None:
        """后台线程主循环：监听 TCP 连接并处理每次打印任务。"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                self.server_socket = server
                # SO_REUSEADDR 让程序异常退出后可以更快重新绑定端口。
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.host, self.port))
                server.listen()
                server.settimeout(0.5)
                self.status(True, f"监听中：{self.host}:{self.port}")
                self.log(f"转发目标：{self.target_printer}")

                while not self.stop_event.is_set():
                    try:
                        conn, address = server.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break

                    with conn:
                        data = self._read_connection(conn)

                    if not data:
                        self.log(f"{address}: 空连接，已忽略")
                        continue

                    self._handle_data(address, data)
        except OSError as exc:
            self.status(False, "已停止")
            if "10048" in str(exc) or "address" in str(exc).lower():
                self.log(f"无法监听 {self.host}:{self.port}，可能已经有一个转发程序在运行。")
            else:
                self.log(f"转发服务错误：{exc}")
        finally:
            self.server_socket = None
            self.status(False, "已停止")

    def _read_connection(self, conn: socket.socket) -> bytes:
        """读取一次打印连接中的全部 bytes。

        银豹/美团会在发送完打印数据后关闭连接；读到空 chunk 表示本次任务结束。
        """
        chunks: list[bytes] = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _handle_data(self, address: tuple[str, int], data: bytes) -> None:
        """处理一张小票的完整数据流。

        Args:
            address: 发起连接的客户端地址。
            data: 本次打印任务的原始 bytes。

        处理顺序刻意保持为“先保存，再解析，再转发，再写 JSON”：
        即使解析或转发失败，也尽量保留原始 .bin 用于事后排查。
        """
        capture_path = save_capture(data)
        json_path = capture_path.with_suffix(".json")
        preview_path: Path | None = None
        encoding = None
        parsed_text = ""
        markers: list[str] = []
        forward_ok = False
        forward_error = None

        self.event_queue.put(
            {
                "type": "capture",
                "bytes": len(data),
                "path": str(capture_path),
                "source": f"{address[0]}:{address[1]}",
            }
        )

        try:
            encoding, parsed = parse_capture(data)
            parsed_text = parsed.text
            markers = parsed.markers
            preview_path = capture_path.with_suffix(".preview.png")
            make_capture_preview(data, parsed.preview_lines, preview_path)
            self.event_queue.put(
                {
                    "type": "preview",
                    "encoding": encoding,
                    "text": parsed.text,
                    "markers": parsed.markers,
                    "preview": str(preview_path),
                }
            )
        except Exception as exc:
            self.log(f"Preview failed: {exc}")

        try:
            # 转发必须使用原始 data，不能用 parsed_text 重新编码。
            send_raw_to_printer(self.target_printer, data)
            forward_ok = True
            self.event_queue.put(
                {
                    "type": "forwarded",
                    "bytes": len(data),
                    "printer": self.target_printer,
                }
            )
        except Exception as exc:
            forward_error = str(exc)
            self.event_queue.put({"type": "failed", "message": str(exc)})

        metadata = {
            "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
            "source": f"{address[0]}:{address[1]}",
            "byte_count": len(data),
            "raw_bin_path": str(capture_path),
            "json_path": str(json_path),
            "preview_path": str(preview_path) if preview_path else None,
            "target_printer": self.target_printer,
            "forwarded": forward_ok,
            "forward_error": forward_error,
            "decoded_encoding": encoding,
            "content_kind": parsed.content_kind if encoding else None,
            "bitmap_count": parsed.bitmap_count if encoding else 0,
            "bitmap_images": parsed.bitmap_images if encoding else [],
            "package_entries": parsed.package_entries if encoding else [],
            "parsed_text": parsed_text,
            "escpos_markers": markers,
        }
        json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        self.event_queue.put({"type": "json", "path": str(json_path)})


class App(tk.Tk):
    """主窗口类。

    界面负责让店员/测试人员选择真实小票机、创建代理打印机、启动监听、
    查看抓包结果和打开调试文件夹。所有耗时网络操作都放在 Forwarder 线程中。
    """

    def __init__(self, auto_start: bool = False, target_printer: str | None = None):
        """创建主窗口并初始化界面状态。

        Args:
            auto_start: 是否打开窗口后自动开始监听。
            target_printer: 命令行指定的默认真实小票机名称。
        """
        super().__init__()
        self.title("语音小票打印监控")
        self.geometry("980x680")
        self.minsize(860, 580)

        self.events: queue.Queue[dict] = queue.Queue()
        self.forwarder = Forwarder(HOST, PORT, self.events)
        self.received_count = 0
        self.forwarded_count = 0
        self.failed_count = 0
        self.received_bytes = 0
        self.is_running = False
        self.restart_after_target_change = False

        self.printer_names = self.load_printers()
        self.target_var = tk.StringVar(value=target_printer or self.default_printer())
        self.proxy_var = tk.StringVar(value=DEFAULT_PROXY_PRINTER)
        self.status_var = tk.StringVar(value="已停止")
        self.flow_var = tk.StringVar(value="Receipt Voice Proxy -> 127.0.0.1:9100 -> GP-5850II")
        self.stats_var = tk.StringVar(value="收到 0 | 转发 0 | 失败 0 | 字节 0")
        self.latest_capture_var = tk.StringVar(value="还没有抓到小票")
        self.latest_preview_var = tk.StringVar(value="还没有生成预览")

        self.create_widgets()
        self.after(200, self.drain_events)
        if auto_start:
            self.after(500, self.start_forwarder)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def load_printers(self) -> list[str]:
        """读取 Windows 当前可见的打印机队列名称列表。

        Returns:
            打印机名称列表；读取失败时返回空列表，避免界面直接崩溃。
        """
        try:
            import win32print  # type: ignore

            printers = win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            )
            return [printer[2] for printer in printers]
        except Exception:
            return []

    def default_printer(self) -> str:
        """选择一个较安全的默认真实小票机。

        优先选择已知测试机 GP-5850II；否则选择名称像小票机的设备；
        最后才选择非 PDF/WPS/OneNote 的普通打印机。
        """
        if DEFAULT_TARGET_PRINTER in self.printer_names:
            return DEFAULT_TARGET_PRINTER
        for name in self.printer_names:
            lowered = name.lower()
            if name == DEFAULT_PROXY_PRINTER:
                continue
            if any(keyword in lowered for keyword in VIRTUAL_PRINTER_KEYWORDS):
                continue
            if any(keyword in lowered for keyword in RECEIPT_PRINTER_KEYWORDS):
                return name
        for name in self.printer_names:
            lowered = name.lower()
            if name != DEFAULT_PROXY_PRINTER and not any(
                keyword in lowered for keyword in VIRTUAL_PRINTER_KEYWORDS
            ):
                return name
        return ""

    def create_widgets(self) -> None:
        """创建并布局所有 Tkinter 控件。"""
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root)
        top.pack(fill=tk.X)

        ttk.Label(top, text="小票转发监控", font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.status_var, padding=(16, 0)).pack(side=tk.LEFT)

        button_row = ttk.Frame(top)
        button_row.pack(side=tk.RIGHT)
        self.toggle_button = ttk.Button(button_row, text="开始转发", command=self.toggle_forwarder)
        self.toggle_button.pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="创建/修复代理打印机", command=self.create_proxy_printer).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="测试打印", command=self.test_proxy_print).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="刷新打印机", command=self.refresh_printers).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="打开抓包文件夹", command=self.open_captures_folder).pack(side=tk.LEFT, padx=4)

        config = ttk.LabelFrame(root, text="转发设置", padding=10)
        config.pack(fill=tk.X, pady=(12, 8))
        config.columnconfigure(1, weight=1)
        config.columnconfigure(3, weight=1)

        ttk.Label(config, text="代理打印机").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(config, textvariable=self.proxy_var).grid(row=0, column=1, sticky=tk.EW, padx=(0, 16))
        ttk.Label(config, text="真实小票机").grid(row=0, column=2, sticky=tk.W, padx=(0, 8))
        self.target_combo = ttk.Combobox(
            config,
            textvariable=self.target_var,
            values=self.printer_names,
            state="readonly" if self.printer_names else "normal",
        )
        self.target_combo.grid(row=0, column=3, sticky=tk.EW)
        self.target_combo.bind("<<ComboboxSelected>>", self.on_target_changed)

        ttk.Label(config, text="数据流向").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(10, 0))
        ttk.Label(config, textvariable=self.flow_var).grid(
            row=1, column=1, columnspan=3, sticky=tk.W, pady=(10, 0)
        )

        stats = ttk.LabelFrame(root, text="实时状态", padding=10)
        stats.pack(fill=tk.X, pady=8)
        ttk.Label(stats, textvariable=self.stats_var).pack(anchor=tk.W)
        ttk.Label(stats, textvariable=self.latest_capture_var).pack(anchor=tk.W, pady=(6, 0))
        ttk.Label(stats, textvariable=self.latest_preview_var).pack(anchor=tk.W, pady=(2, 0))

        panes = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        log_frame = ttk.LabelFrame(panes, text="事件日志", padding=8)
        self.log_box = scrolledtext.ScrolledText(log_frame, height=18, wrap=tk.WORD)
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self.log_box.configure(state=tk.DISABLED)
        panes.add(log_frame, weight=3)

        text_frame = ttk.LabelFrame(panes, text="最新解析文本", padding=8)
        self.text_box = scrolledtext.ScrolledText(text_frame, height=18, wrap=tk.WORD)
        self.text_box.pack(fill=tk.BOTH, expand=True)
        self.text_box.configure(state=tk.DISABLED)
        panes.add(text_frame, weight=2)

        self.log("准备就绪。点击“开始转发”监听 127.0.0.1:9100。")
        self.update_flow()

    def toggle_forwarder(self) -> None:
        """根据当前状态在“开始转发”和“停止转发”之间切换。"""
        if self.is_running:
            self.stop_forwarder()
        else:
            self.start_forwarder()

    def start_forwarder(self) -> None:
        """校验当前打印机设置并启动后台转发器。"""
        target = self.target_var.get().strip()
        if not target:
            messagebox.showerror("缺少打印机", "请先选择真实小票机。")
            return
        if target == self.proxy_var.get().strip():
            messagebox.showerror("转发设置错误", "真实小票机不能和代理打印机相同。")
            return
        if any(keyword in target.lower() for keyword in VIRTUAL_PRINTER_KEYWORDS):
            messagebox.showerror(
                "转发目标错误",
                "这个目标看起来像 PDF/WPS/OneNote 等虚拟打印机，请选择真实小票机。",
            )
            return
        self.update_flow()
        self.forwarder.start(target)

    def stop_forwarder(self) -> None:
        """停止后台转发器。"""
        self.forwarder.stop()

    def create_proxy_printer(self) -> None:
        """创建或修复 Windows 代理打印机。

        这个操作可能需要管理员权限；失败时会提示用户以管理员身份运行。
        """
        proxy = self.proxy_var.get().strip()
        target = self.target_var.get().strip()
        if not proxy or not target:
            messagebox.showerror("缺少打印机", "请先选择代理打印机和真实小票机。")
            return
        if proxy == target:
            messagebox.showerror("转发设置错误", "代理打印机不能和真实小票机相同。")
            return

        try:
            result = create_or_fix_proxy_printer(proxy, target)
            self.log(f"代理打印机已就绪：{result}")
            self.refresh_printers()
            messagebox.showinfo("代理打印机已就绪", f"{proxy} 已准备好。\n请在美团/银豹里选择它。")
        except Exception as exc:
            self.log(f"创建/修复代理打印机失败：{exc}")
            messagebox.showerror(
                "代理打印机设置失败",
                "无法创建代理打印机。请右键程序，选择“以管理员身份运行”后重试。\n\n"
                + str(exc),
            )

    def test_proxy_print(self) -> None:
        """向代理打印机发送一张内置测试小票。"""
        proxy = self.proxy_var.get().strip()
        if not proxy:
            messagebox.showerror("缺少代理打印机", "代理打印机名称为空。")
            return
        try:
            print_to_queue(proxy, sample_receipt_bytes())
            self.log(f"已发送测试小票到代理打印机：{proxy}")
        except Exception as exc:
            self.log(f"测试打印失败：{exc}")
            messagebox.showerror("测试失败", str(exc))

    def refresh_printers(self) -> None:
        """重新加载打印机列表，并刷新下拉框。"""
        self.printer_names = self.load_printers()
        self.target_combo.configure(values=self.printer_names)
        if self.target_var.get() not in self.printer_names:
            self.target_var.set(self.default_printer())
        self.log(f"已加载 {len(self.printer_names)} 个打印机。")
        self.update_flow()

    def on_target_changed(self, _event=None) -> None:
        """真实小票机变更后的处理。

        如果监听已经启动，自动重启转发器，确保后续小票转发到新设备。
        """
        self.update_flow()
        target = self.target_var.get().strip()
        self.log(f"真实小票机已切换为：{target}")
        if self.is_running:
            self.restart_after_target_change = True
            self.log("正在转发中，切换设备后将自动重启监听。")
            self.stop_forwarder()
            self.after(1000, self.restart_if_needed)

    def restart_if_needed(self) -> None:
        """在切换真实小票机后延迟重启转发器。"""
        if self.restart_after_target_change:
            self.restart_after_target_change = False
            self.start_forwarder()

    def open_captures_folder(self) -> None:
        """打开抓包文件夹，方便用户发送 .bin/.json/.preview.png。"""
        path = CAPTURE_DIR
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def update_flow(self) -> None:
        """刷新界面上的数据流向说明。"""
        proxy = self.proxy_var.get().strip() or DEFAULT_PROXY_PRINTER
        target = self.target_var.get().strip() or "(请选择真实小票机)"
        self.flow_var.set(f"{proxy} -> {HOST}:{PORT} -> {target}")

    def drain_events(self) -> None:
        """周期性消费后台线程投递到队列里的事件。"""
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self.handle_event(event)
        self.after(200, self.drain_events)

    def handle_event(self, event: dict) -> None:
        """根据后台事件更新界面。

        Args:
            event: Forwarder 发送的事件字典，type 字段决定处理分支。
        """
        event_type = event.get("type")
        if event_type == "log":
            self.log(event["message"])
        elif event_type == "status":
            self.is_running = bool(event.get("running"))
            self.status_var.set(event["message"])
            self.update_toggle_button()
            self.log(event["message"])
        elif event_type == "capture":
            self.received_count += 1
            self.received_bytes += int(event["bytes"])
            self.latest_capture_var.set(f"最新抓包：{event['path']}")
            self.log(f"{event['source']}：收到 {event['bytes']} bytes -> {event['path']}")
            self.update_stats()
        elif event_type == "preview":
            self.latest_preview_var.set(f"最新预览：{event['preview']}")
            markers = ", ".join(event["markers"]) if event["markers"] else "none"
            self.log(f"解析编码：{event['encoding']}；标记：{markers}")
            self.set_latest_text(event["text"])
        elif event_type == "forwarded":
            self.forwarded_count += 1
            self.log(f"已转发 {event['bytes']} bytes 到真实小票机：{event['printer']}")
            self.update_stats()
        elif event_type == "failed":
            self.failed_count += 1
            self.log(f"转发失败：{event['message']}")
            self.update_stats()
        elif event_type == "json":
            self.log(f"已保存调试 JSON：{event['path']}")

    def update_stats(self) -> None:
        """刷新收到、转发、失败和总字节数统计。"""
        self.stats_var.set(
            f"收到 {self.received_count} | 转发 {self.forwarded_count} | "
            f"失败 {self.failed_count} | 字节 {self.received_bytes}"
        )

    def update_toggle_button(self) -> None:
        """根据监听状态刷新主按钮文案。"""
        self.toggle_button.configure(text="停止转发" if self.is_running else "开始转发")

    def log(self, message: str) -> None:
        """向界面日志框追加一行带时间戳的日志。"""
        stamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, f"[{stamp}] {message}\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def set_latest_text(self, text: str) -> None:
        """刷新右侧“最新解析文本”区域。"""
        self.text_box.configure(state=tk.NORMAL)
        self.text_box.delete("1.0", tk.END)
        self.text_box.insert(tk.END, text or "[没有可直接读取的文字，可能是位图小票]")
        self.text_box.configure(state=tk.DISABLED)

    def on_close(self) -> None:
        """窗口关闭时停止后台监听并销毁窗口。"""
        self.forwarder.stop()
        self.destroy()


def main() -> int:
    """解析命令行参数并启动 GUI。"""
    parser = argparse.ArgumentParser(description="Receipt printer forwarding monitor.")
    parser.add_argument("--auto-start", action="store_true", help="Start listening when the GUI opens.")
    parser.add_argument("--target", help="Target printer name.")
    args = parser.parse_args()

    App(auto_start=args.auto_start, target_printer=args.target).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
