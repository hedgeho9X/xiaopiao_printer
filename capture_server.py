import argparse
import datetime as dt
import socket
from pathlib import Path


CAPTURE_DIR = Path(__file__).resolve().parent / "captures"


def require_win32print():
    try:
        import win32print  # type: ignore

        return win32print
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: pywin32. Run `pip install -r requirements.txt` first."
        ) from exc


def save_capture(data: bytes) -> Path:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = CAPTURE_DIR / f"{stamp}.bin"
    path.write_bytes(data)
    return path


def send_raw_to_printer(printer_name: str, data: bytes) -> None:
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
    chunks: list[bytes] = []
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def serve(host: str, port: int, printer: str | None, no_forward: bool) -> None:
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

