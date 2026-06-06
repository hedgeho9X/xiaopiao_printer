import socket
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from print_raw import send_raw_to_printer


DEFAULT_PROXY_PRINTER = "Receipt Voice Proxy"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9100


def receipt_bytes(text: str) -> bytes:
    payload = bytearray()
    payload.extend(b"\x1b@")
    payload.extend(b"\x1ba\x01")
    payload.extend("测试订单\n".encode("gbk", errors="replace"))
    payload.extend(b"\x1ba\x00")
    payload.extend("------------------------\n".encode("ascii"))
    payload.extend(text.strip().encode("gbk", errors="replace"))
    payload.extend(b"\n\n\n")
    payload.extend(b"\x1dV\x42\x00")
    return bytes(payload)


def send_tcp(host: str, port: int, data: bytes) -> None:
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(data)


class TestSenderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Receipt Test Sender")
        self.geometry("760x620")
        self.minsize(660, 520)

        self.proxy_var = tk.StringVar(value=DEFAULT_PROXY_PRINTER)
        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.bytes_var = tk.StringVar(value="Ready")

        self.create_widgets()

    def create_widgets(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Receipt Test Sender", font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.bytes_var, padding=(16, 0)).pack(side=tk.LEFT)

        route = ttk.LabelFrame(root, text="Send Target", padding=10)
        route.pack(fill=tk.X, pady=(12, 8))
        route.columnconfigure(1, weight=1)
        route.columnconfigure(3, weight=1)

        ttk.Label(route, text="Proxy printer").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(route, textvariable=self.proxy_var).grid(row=0, column=1, sticky=tk.EW, padx=(0, 16))

        ttk.Label(route, text="TCP").grid(row=0, column=2, sticky=tk.W, padx=(0, 8))
        tcp_frame = ttk.Frame(route)
        tcp_frame.grid(row=0, column=3, sticky=tk.EW)
        tcp_frame.columnconfigure(0, weight=1)
        ttk.Entry(tcp_frame, textvariable=self.host_var).grid(row=0, column=0, sticky=tk.EW)
        ttk.Label(tcp_frame, text=":").grid(row=0, column=1, padx=4)
        ttk.Entry(tcp_frame, textvariable=self.port_var, width=8).grid(row=0, column=2)

        body = ttk.LabelFrame(root, text="Receipt Content", padding=10)
        body.pack(fill=tk.BOTH, expand=True, pady=8)
        self.text_box = scrolledtext.ScrolledText(body, wrap=tk.WORD, height=16)
        self.text_box.pack(fill=tk.BOTH, expand=True)
        self.text_box.insert(
            tk.END,
            "取餐号：A018\n"
            "冰美式 x1\n"
            "少冰\n"
            "拿铁 x2\n"
            "燕麦奶\n"
            "备注：不要吸管\n"
            "------------------------\n"
            "谢谢光临",
        )

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="Send To Proxy Printer", command=self.send_to_proxy).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="Send To TCP 9100", command=self.send_to_tcp).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="Load Example", command=self.load_example).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="Clear", command=self.clear_text).pack(side=tk.LEFT)

        log_frame = ttk.LabelFrame(root, text="Send Log", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=False, pady=(12, 0))
        self.log_box = scrolledtext.ScrolledText(log_frame, height=7, wrap=tk.WORD)
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self.log_box.configure(state=tk.DISABLED)

        self.log("Open the monitor first, then send a test receipt here.")

    def load_example(self) -> None:
        self.text_box.delete("1.0", tk.END)
        self.text_box.insert(
            tk.END,
            "取餐号：B026\n"
            "热拿铁 x1\n"
            "半糖\n"
            "抹茶瑞士卷 x1\n"
            "备注：客人等候中\n"
            "支付：已支付",
        )

    def clear_text(self) -> None:
        self.text_box.delete("1.0", tk.END)

    def current_bytes(self) -> bytes:
        text = self.text_box.get("1.0", tk.END).strip()
        if not text:
            raise ValueError("Receipt content is empty.")
        data = receipt_bytes(text)
        self.bytes_var.set(f"{len(data)} bytes")
        return data

    def send_to_proxy(self) -> None:
        printer = self.proxy_var.get().strip()
        if not printer:
            messagebox.showerror("Missing printer", "Proxy printer name is empty.")
            return
        try:
            data = self.current_bytes()
            send_raw_to_printer(printer, data)
            self.log(f"Sent {len(data)} bytes to proxy printer: {printer}")
        except Exception as exc:
            self.log(f"Proxy send failed: {exc}")
            messagebox.showerror("Send failed", str(exc))

    def send_to_tcp(self) -> None:
        try:
            host = self.host_var.get().strip()
            port = int(self.port_var.get().strip())
            data = self.current_bytes()
            send_tcp(host, port, data)
            self.log(f"Sent {len(data)} bytes to tcp://{host}:{port}")
        except Exception as exc:
            self.log(f"TCP send failed: {exc}")
            messagebox.showerror("Send failed", str(exc))

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, f"[{stamp}] {message}\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)


def main() -> int:
    TestSenderApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

