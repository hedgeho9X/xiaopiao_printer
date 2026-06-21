"""发送真实链路测试订单。

本脚本读取 tests/cases 里的小票文本，并按真实入口发送：
银豹样例通过 TCP 网口发送，美团样例通过 Windows 代理打印机发送。
运行前请先启动桌面程序，并确认双入口监听已开启。
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app_core.printer import send_raw_to_printer  # noqa: E402


CASES_DIR = Path(__file__).resolve().parent / "cases"
CASES = {
    "yinbao_bar": {"path": CASES_DIR / "yinbao_bar_p0009.txt", "channel": "network"},
    "yinbao_table": {"path": CASES_DIR / "yinbao_table_d1.txt", "channel": "network"},
    "meituan_pickup": {"path": CASES_DIR / "meituan_pickup.txt", "channel": "usb"},
    "meituan_delivery": {"path": CASES_DIR / "meituan_delivery.txt", "channel": "usb"},
}


def main() -> int:
    """解析命令行参数并发送测试小票。"""
    parser = argparse.ArgumentParser(description="发送 Receipt Voice 测试订单")
    parser.add_argument("case", choices=["all", *CASES.keys()], help="要发送的测试单")
    parser.add_argument("--network-host", default="127.0.0.1", help="银豹网口入口地址")
    parser.add_argument("--network-port", type=int, default=9100, help="银豹网口入口端口")
    parser.add_argument("--proxy-printer", default="Receipt Voice Proxy", help="美团 USB 代理打印机名称")
    parser.add_argument("--meituan-mode", choices=["printer", "tcp"], default="printer", help="美团样例发送方式")
    parser.add_argument("--usb-host", default="127.0.0.1", help="USB 代理 TCP 测试地址")
    parser.add_argument("--usb-port", type=int, default=9101, help="USB 代理 TCP 测试端口")
    parser.add_argument("--interval", type=float, default=0.6, help="连续发送时的间隔秒数")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要发送的内容，不实际发送")
    args = parser.parse_args()

    names = list(CASES) if args.case == "all" else [args.case]
    for name in names:
        case = CASES[name]
        text = case["path"].read_text(encoding="utf-8")
        data = to_escpos_bytes(text)
        if args.dry_run:
            print(f"[dry-run] {name}: {len(data)} bytes -> {case['channel']}")
        elif case["channel"] == "network":
            send_tcp(args.network_host, args.network_port, data)
            print(f"[ok] {name} -> 网口 {args.network_host}:{args.network_port}")
        elif args.meituan_mode == "tcp":
            send_tcp(args.usb_host, args.usb_port, data)
            print(f"[ok] {name} -> USB TCP {args.usb_host}:{args.usb_port}")
        else:
            verify_proxy_port(args.proxy_printer, args.usb_port)
            send_raw_to_printer(args.proxy_printer, data)
            print(f"[ok] {name} -> 代理打印机 {args.proxy_printer}")
        time.sleep(args.interval)
    return 0


def to_escpos_bytes(text: str) -> bytes:
    """把小票文本转换成常见 ESC/POS RAW bytes。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n\n\n"
    return b"\x1b@" + normalized.encode("gbk", errors="replace") + b"\x1dV\x00"


def send_tcp(host: str, port: int, data: bytes) -> None:
    """通过 TCP 发送原始打印 bytes。"""
    with socket.create_connection((host, port), timeout=5) as sock:
        sock.sendall(data)


def verify_proxy_port(printer_name: str, expected_port: int) -> None:
    """确认代理打印机指向 USB 专用端口，避免误发到网口入口。"""
    script = (
        f"$p = Get-Printer -Name {ps_quote(printer_name)} -ErrorAction Stop; "
        "$p.PortName"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"找不到代理打印机：{printer_name}")
    port_name = result.stdout.strip()
    expected = f"IP_127.0.0.1_{expected_port}"
    if port_name != expected:
        raise RuntimeError(
            f"代理打印机当前端口是 {port_name}，不是 {expected}。请先在 App 里点“修复代理”。"
        )


def ps_quote(value: str) -> str:
    """把字符串转换成 PowerShell 单引号字面量。"""
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
