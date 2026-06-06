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
from inspect_capture import make_preview, parse_capture
from print_raw import sample_receipt_bytes, send_raw_to_printer as print_to_queue


HOST = "127.0.0.1"
PORT = 9100
DEFAULT_PROXY_PRINTER = "Receipt Voice Proxy"
DEFAULT_TARGET_PRINTER = "GP-5850II"
VIRTUAL_PRINTER_KEYWORDS = ("pdf", "wps", "onenote", "xps", "fax", "microsoft print")
RECEIPT_PRINTER_KEYWORDS = ("gp", "pos", "58", "80", "thermal", "receipt", "esc", "cla", "tech")


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_or_fix_proxy_printer(proxy_name: str, target_printer: str) -> str:
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
    def __init__(self, host: str, port: int, event_queue: queue.Queue[dict]):
        self.host = host
        self.port = port
        self.event_queue = event_queue
        self.target_printer = DEFAULT_TARGET_PRINTER
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.server_socket: socket.socket | None = None

    def start(self, target_printer: str) -> None:
        if self.thread and self.thread.is_alive():
            self.log("Forwarder is already running.")
            return
        self.target_printer = target_printer
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="receipt-forwarder", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
        self.log("Stopping forwarder...")

    def log(self, message: str) -> None:
        self.event_queue.put({"type": "log", "message": message})

    def status(self, running: bool, message: str) -> None:
        self.event_queue.put({"type": "status", "running": running, "message": message})

    def _run(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                self.server_socket = server
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.host, self.port))
                server.listen()
                server.settimeout(0.5)
                self.status(True, f"Listening on {self.host}:{self.port}")
                self.log(f"Forwarding to printer: {self.target_printer}")

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
                        self.log(f"{address}: empty connection, skipped")
                        continue

                    self._handle_data(address, data)
        except OSError as exc:
            self.status(False, "Stopped")
            if "10048" in str(exc) or "address" in str(exc).lower():
                self.log(f"Cannot listen on {self.host}:{self.port}; another forwarder may be running.")
            else:
                self.log(f"Forwarder error: {exc}")
        finally:
            self.server_socket = None
            self.status(False, "Stopped")

    def _read_connection(self, conn: socket.socket) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _handle_data(self, address: tuple[str, int], data: bytes) -> None:
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
            make_preview(parsed.preview_lines, preview_path)
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
            "parsed_text": parsed_text,
            "escpos_markers": markers,
        }
        json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        self.event_queue.put({"type": "json", "path": str(json_path)})


class App(tk.Tk):
    def __init__(self, auto_start: bool = False, target_printer: str | None = None):
        super().__init__()
        self.title("Receipt Voice Printer Monitor")
        self.geometry("980x680")
        self.minsize(860, 580)

        self.events: queue.Queue[dict] = queue.Queue()
        self.forwarder = Forwarder(HOST, PORT, self.events)
        self.received_count = 0
        self.forwarded_count = 0
        self.failed_count = 0
        self.received_bytes = 0

        self.printer_names = self.load_printers()
        self.target_var = tk.StringVar(value=target_printer or self.default_printer())
        self.proxy_var = tk.StringVar(value=DEFAULT_PROXY_PRINTER)
        self.status_var = tk.StringVar(value="Stopped")
        self.flow_var = tk.StringVar(value="Receipt Voice Proxy -> 127.0.0.1:9100 -> GP-5850II")
        self.stats_var = tk.StringVar(value="Received 0 | Forwarded 0 | Failed 0 | Bytes 0")
        self.latest_capture_var = tk.StringVar(value="No capture yet")
        self.latest_preview_var = tk.StringVar(value="No preview yet")

        self.create_widgets()
        self.after(200, self.drain_events)
        if auto_start:
            self.after(500, self.start_forwarder)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def load_printers(self) -> list[str]:
        try:
            import win32print  # type: ignore

            printers = win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            )
            return [printer[2] for printer in printers]
        except Exception:
            return []

    def default_printer(self) -> str:
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
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Forwarder", font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.status_var, padding=(16, 0)).pack(side=tk.LEFT)

        button_row = ttk.Frame(top)
        button_row.pack(side=tk.RIGHT)
        ttk.Button(button_row, text="Start", command=self.start_forwarder).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Stop", command=self.stop_forwarder).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Create/Fix Proxy", command=self.create_proxy_printer).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Test Proxy Print", command=self.test_proxy_print).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Refresh Printers", command=self.refresh_printers).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Open Captures", command=self.open_captures_folder).pack(side=tk.LEFT, padx=4)

        config = ttk.LabelFrame(root, text="Routing", padding=10)
        config.pack(fill=tk.X, pady=(12, 8))
        config.columnconfigure(1, weight=1)
        config.columnconfigure(3, weight=1)

        ttk.Label(config, text="Proxy printer").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(config, textvariable=self.proxy_var).grid(row=0, column=1, sticky=tk.EW, padx=(0, 16))
        ttk.Label(config, text="Target printer").grid(row=0, column=2, sticky=tk.W, padx=(0, 8))
        self.target_combo = ttk.Combobox(
            config,
            textvariable=self.target_var,
            values=self.printer_names,
            state="readonly" if self.printer_names else "normal",
        )
        self.target_combo.grid(row=0, column=3, sticky=tk.EW)

        ttk.Label(config, text="Flow").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(10, 0))
        ttk.Label(config, textvariable=self.flow_var).grid(
            row=1, column=1, columnspan=3, sticky=tk.W, pady=(10, 0)
        )

        stats = ttk.LabelFrame(root, text="Live Status", padding=10)
        stats.pack(fill=tk.X, pady=8)
        ttk.Label(stats, textvariable=self.stats_var).pack(anchor=tk.W)
        ttk.Label(stats, textvariable=self.latest_capture_var).pack(anchor=tk.W, pady=(6, 0))
        ttk.Label(stats, textvariable=self.latest_preview_var).pack(anchor=tk.W, pady=(2, 0))

        panes = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        log_frame = ttk.LabelFrame(panes, text="Event Log", padding=8)
        self.log_box = scrolledtext.ScrolledText(log_frame, height=18, wrap=tk.WORD)
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self.log_box.configure(state=tk.DISABLED)
        panes.add(log_frame, weight=3)

        text_frame = ttk.LabelFrame(panes, text="Latest Parsed Text", padding=8)
        self.text_box = scrolledtext.ScrolledText(text_frame, height=18, wrap=tk.WORD)
        self.text_box.pack(fill=tk.BOTH, expand=True)
        self.text_box.configure(state=tk.DISABLED)
        panes.add(text_frame, weight=2)

        self.log("Ready. Click Start to listen on 127.0.0.1:9100.")
        self.update_flow()

    def start_forwarder(self) -> None:
        target = self.target_var.get().strip()
        if not target:
            messagebox.showerror("Missing printer", "Choose a target printer first.")
            return
        if target == self.proxy_var.get().strip():
            messagebox.showerror("Invalid route", "Target printer cannot be the proxy printer.")
            return
        if any(keyword in target.lower() for keyword in VIRTUAL_PRINTER_KEYWORDS):
            messagebox.showerror(
                "Invalid target",
                "Target printer looks like a virtual printer. Choose the real receipt printer.",
            )
            return
        self.update_flow()
        self.forwarder.start(target)

    def stop_forwarder(self) -> None:
        self.forwarder.stop()

    def create_proxy_printer(self) -> None:
        proxy = self.proxy_var.get().strip()
        target = self.target_var.get().strip()
        if not proxy or not target:
            messagebox.showerror("Missing printer", "Choose proxy and target printers first.")
            return
        if proxy == target:
            messagebox.showerror("Invalid route", "Proxy printer cannot be the target printer.")
            return

        try:
            result = create_or_fix_proxy_printer(proxy, target)
            self.log(f"Proxy printer ready: {result}")
            self.refresh_printers()
            messagebox.showinfo("Proxy ready", f"{proxy} is ready.\nSelect it in Meituan/Yinbao.")
        except Exception as exc:
            self.log(f"Create/fix proxy failed: {exc}")
            messagebox.showerror(
                "Proxy setup failed",
                "Could not create the proxy printer. Try running this app as administrator.\n\n"
                + str(exc),
            )

    def test_proxy_print(self) -> None:
        proxy = self.proxy_var.get().strip()
        if not proxy:
            messagebox.showerror("Missing proxy", "Proxy printer name is empty.")
            return
        try:
            print_to_queue(proxy, sample_receipt_bytes())
            self.log(f"Sent test receipt to proxy printer: {proxy}")
        except Exception as exc:
            self.log(f"Test proxy print failed: {exc}")
            messagebox.showerror("Test failed", str(exc))

    def refresh_printers(self) -> None:
        self.printer_names = self.load_printers()
        self.target_combo.configure(values=self.printer_names)
        if self.target_var.get() not in self.printer_names:
            self.target_var.set(self.default_printer())
        self.log(f"Loaded {len(self.printer_names)} printer(s).")
        self.update_flow()

    def open_captures_folder(self) -> None:
        path = CAPTURE_DIR
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def update_flow(self) -> None:
        proxy = self.proxy_var.get().strip() or DEFAULT_PROXY_PRINTER
        target = self.target_var.get().strip() or "(choose target)"
        self.flow_var.set(f"{proxy} -> {HOST}:{PORT} -> {target}")

    def drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self.handle_event(event)
        self.after(200, self.drain_events)

    def handle_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "log":
            self.log(event["message"])
        elif event_type == "status":
            self.status_var.set(event["message"])
            self.log(event["message"])
        elif event_type == "capture":
            self.received_count += 1
            self.received_bytes += int(event["bytes"])
            self.latest_capture_var.set(f"Latest capture: {event['path']}")
            self.log(f"{event['source']}: received {event['bytes']} bytes -> {event['path']}")
            self.update_stats()
        elif event_type == "preview":
            self.latest_preview_var.set(f"Latest preview: {event['preview']}")
            markers = ", ".join(event["markers"]) if event["markers"] else "none"
            self.log(f"Parsed as {event['encoding']}; markers: {markers}")
            self.set_latest_text(event["text"])
        elif event_type == "forwarded":
            self.forwarded_count += 1
            self.log(f"Forwarded {event['bytes']} bytes to printer: {event['printer']}")
            self.update_stats()
        elif event_type == "failed":
            self.failed_count += 1
            self.log(f"Forward failed: {event['message']}")
            self.update_stats()
        elif event_type == "json":
            self.log(f"Saved debug JSON: {event['path']}")

    def update_stats(self) -> None:
        self.stats_var.set(
            f"Received {self.received_count} | Forwarded {self.forwarded_count} | "
            f"Failed {self.failed_count} | Bytes {self.received_bytes}"
        )

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, f"[{stamp}] {message}\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def set_latest_text(self, text: str) -> None:
        self.text_box.configure(state=tk.NORMAL)
        self.text_box.delete("1.0", tk.END)
        self.text_box.insert(tk.END, text or "[NO READABLE TEXT]")
        self.text_box.configure(state=tk.DISABLED)

    def on_close(self) -> None:
        self.forwarder.stop()
        self.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Receipt printer forwarding monitor.")
    parser.add_argument("--auto-start", action="store_true", help="Start listening when the GUI opens.")
    parser.add_argument("--target", help="Target printer name.")
    args = parser.parse_args()

    App(auto_start=args.auto_start, target_printer=args.target).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
