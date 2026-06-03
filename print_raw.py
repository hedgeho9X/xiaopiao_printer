import argparse
import socket
import sys


def sample_receipt_bytes() -> bytes:
    lines = [
        "测试小票",
        "------------------------",
        "拿铁 x1",
        "少冰",
        "谢谢",
        "",
        "",
    ]
    text = "\n".join(lines)

    # ESC/POS: initialize, center title, left body, feed, cut.
    payload = bytearray()
    payload.extend(b"\x1b@")
    payload.extend(b"\x1ba\x01")
    payload.extend("测试小票\n".encode("gbk"))
    payload.extend(b"\x1ba\x00")
    payload.extend("------------------------\n".encode("ascii"))
    payload.extend("拿铁 x1\n少冰\n谢谢\n\n\n".encode("gbk"))
    payload.extend(b"\x1dV\x42\x00")
    return bytes(payload)


def require_win32print():
    try:
        import win32print  # type: ignore

        return win32print
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: pywin32. Run `pip install -r requirements.txt` first."
        ) from exc


def list_printers() -> None:
    win32print = require_win32print()
    printers = win32print.EnumPrinters(
        win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    )
    if not printers:
        print("No printers found.")
        return

    for index, printer in enumerate(printers, start=1):
        name = printer[2]
        print(f"{index}. {name}")


def send_raw_to_printer(printer_name: str, data: bytes) -> None:
    win32print = require_win32print()
    handle = win32print.OpenPrinter(printer_name)
    try:
        job = win32print.StartDocPrinter(
            handle,
            1,
            ("Receipt Voice PoC Test", None, "RAW"),
        )
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, data)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def send_tcp(host: str, port: int, data: bytes) -> None:
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send RAW ESC/POS test receipts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List Windows printers.")

    printer_parser = subparsers.add_parser(
        "test-printer", help="Send a test receipt to a Windows printer."
    )
    printer_parser.add_argument("printer", help="Windows printer name.")

    tcp_parser = subparsers.add_parser(
        "test-tcp", help="Send a test receipt to a TCP host/port."
    )
    tcp_parser.add_argument("host", help="TCP host, usually 127.0.0.1.")
    tcp_parser.add_argument("port", type=int, help="TCP port, usually 9100.")

    args = parser.parse_args(argv)

    if args.command == "list":
        list_printers()
        return 0

    data = sample_receipt_bytes()

    if args.command == "test-printer":
        send_raw_to_printer(args.printer, data)
        print(f"Sent {len(data)} bytes to printer: {args.printer}")
        return 0

    if args.command == "test-tcp":
        send_tcp(args.host, args.port, data)
        print(f"Sent {len(data)} bytes to tcp://{args.host}:{args.port}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

