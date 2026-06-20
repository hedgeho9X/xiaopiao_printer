"""命令行版小票抓包转发服务。

这个文件是 GUI 之前的最小可用入口，适合排查或无界面运行：
1. 监听本机 TCP 端口，接收小票打印 bytes。
2. 保存原始 .bin 文件。
3. 可选地把原始 bytes 转发到真实 Windows 打印机。

正式现场更推荐使用 app_gui.py / ReceiptVoiceMonitor.exe。
"""

import argparse
import datetime as dt
import socket
import sys
from pathlib import Path


def app_base_dir() -> Path:
    """返回程序运行目录。

    PyInstaller 打包后 ``__file__`` 会指向临时解压目录，所以 exe 场景下
    要以 ``sys.executable`` 所在目录作为 captures 的保存位置。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CAPTURE_DIR = app_base_dir() / "captures"


def require_win32print():
    """延迟导入 pywin32 的 win32print 模块。"""
    try:
        import win32print  # type: ignore

        return win32print
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: pywin32. Run `pip install -r requirements.txt` first."
        ) from exc


def save_capture(data: bytes) -> Path:
    """把原始打印 bytes 保存为带时间戳的 .bin 文件。"""
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = CAPTURE_DIR / f"{stamp}.bin"
    path.write_bytes(data)
    return path


def send_raw_to_printer(printer_name: str, data: bytes) -> None:
    """通过 Windows RAW 打印接口原样发送 bytes。

    Args:
        printer_name: Windows 打印机队列名称。
        data: 原始 ESC/POS、XPS 或其他打印 bytes。
    """
    win32print = require_win32print()
    handle = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(
            handle,
            1,
            ("Receipt Voice PoC Capture", None, "RAW"),
        )
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, data)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def read_connection(conn: socket.socket) -> bytes:
    """读取一个 TCP 连接中的全部打印数据。"""
    chunks: list[bytes] = []
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def serve(host: str, port: int, printer: str | None, no_forward: bool) -> None:
    """启动命令行版抓包服务。

    Args:
        host: 监听地址。
        port: 监听端口。
        printer: 真实小票机名称；no_forward 为 False 时必须提供。
        no_forward: 是否只抓包不转发。
    """
    if not no_forward and not printer:
        raise SystemExit("Either pass --printer or use --no-forward for capture-only mode.")

    mode = "capture-only" if no_forward else f"forward to printer: {printer}"
    print(f"Listening on {host}:{port} ({mode})")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen()

        while True:
            conn, address = server.accept()
            with conn:
                data = read_connection(conn)

            if not data:
                print(f"{address}: empty connection, skipped")
                continue

            capture_path = save_capture(data)
            print(f"{address}: received {len(data)} bytes -> {capture_path}")

            if no_forward:
                print("Forward skipped (--no-forward)")
                continue

            try:
                assert printer is not None
                send_raw_to_printer(printer, data)
                print(f"Forwarded {len(data)} bytes to printer: {printer}")
            except Exception as exc:
                print(f"Forward failed: {exc}")


def main() -> int:
    """解析命令行参数并启动服务。"""
    parser = argparse.ArgumentParser(
        description="Capture TCP print bytes and optionally forward them to a printer."
    )
    parser.add_argument("--printer", help="Windows printer name to forward RAW bytes to.")
    parser.add_argument("--host", default="127.0.0.1", help="Listen host.")
    parser.add_argument("--port", default=9100, type=int, help="Listen port.")
    parser.add_argument(
        "--no-forward",
        action="store_true",
        help="Only save captures; do not forward to a printer.",
    )
    args = parser.parse_args()

    serve(args.host, args.port, args.printer, args.no_forward)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
