"""Windows 打印机能力封装。

本模块负责枚举打印机、向真实打印机发送 RAW bytes，以及创建/修复
Windows 代理打印机。它直接操作 Windows 打印系统。
"""

from __future__ import annotations

import subprocess


VIRTUAL_PRINTER_KEYWORDS = ("pdf", "wps", "onenote", "xps", "fax", "microsoft print")


def list_printers() -> list[str]:
    """返回当前 Windows 可见打印机名称。"""
    try:
        import win32print  # type: ignore
    except ImportError:
        return []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [printer[2] for printer in win32print.EnumPrinters(flags)]


def list_real_printers(exclude: str | None = None) -> list[str]:
    """返回过滤掉虚拟打印机和指定打印机后的打印机列表。

    Args:
        exclude: 需要额外排除的打印机名称，通常是我们创建的代理打印机。
    """
    result = []
    excluded = (exclude or "").casefold()
    for name in list_printers():
        lowered = name.lower()
        if excluded and name.casefold() == excluded:
            continue
        if not any(keyword in lowered for keyword in VIRTUAL_PRINTER_KEYWORDS):
            result.append(name)
    return result


def send_raw_to_printer(printer_name: str, data: bytes) -> None:
    """向 Windows 打印队列发送 RAW bytes。

    Args:
        printer_name: 真实小票机名称。
        data: 原始打印 bytes，不能经过重新编码。

    Raises:
        RuntimeError: 未安装 pywin32 或 Windows 打印 API 调用失败。
    """
    try:
        import win32print  # type: ignore
    except ImportError as exc:
        raise RuntimeError("当前 Python 环境缺少 pywin32，无法转发打印。") from exc

    handle = win32print.OpenPrinter(printer_name)
    try:
        job_id = win32print.StartDocPrinter(handle, 1, ("Receipt Voice Job", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, data)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
        if not job_id:
            raise RuntimeError("Windows 没有返回打印任务 ID。")
    finally:
        win32print.ClosePrinter(handle)


def create_or_fix_proxy_printer(proxy_name: str, target_printer: str, port: int = 9100) -> str:
    """创建或修复指向本机监听端口的 Windows 代理打印机。

    Args:
        proxy_name: POS 软件中选择的代理打印机名称。
        target_printer: 用于复制驱动的真实小票机名称。
        port: 本机监听端口。

    Returns:
        PowerShell 输出的打印机 JSON 文本。
    """
    script = f"""
$ProxyPrinterName = {ps_quote(proxy_name)}
$TargetPrinterName = {ps_quote(target_printer)}
$PortName = 'IP_127.0.0.1_{port}'
$Target = Get-Printer -Name $TargetPrinterName -ErrorAction Stop
$DriverName = $Target.DriverName

if (-not (Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue)) {{
    Add-PrinterPort -Name $PortName -PrinterHostAddress '127.0.0.1' -PortNumber {port}
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
        message = result.stderr.strip() or result.stdout.strip() or "创建代理打印机失败。"
        raise RuntimeError(message)
    return result.stdout.strip()


def ps_quote(value: str) -> str:
    """把 Python 字符串安全转换成 PowerShell 单引号字符串。"""
    return "'" + value.replace("'", "''") + "'"
