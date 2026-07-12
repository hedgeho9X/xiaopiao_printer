"""Windows 打印机能力封装。

本模块负责枚举打印机、向真实打印机发送 RAW bytes，以及创建/修复
Windows 代理打印机。它直接操作 Windows 打印系统。
"""

from __future__ import annotations

import subprocess
import time

from .errors import PrinterError


VIRTUAL_PRINTER_KEYWORDS = ("pdf", "wps", "onenote", "xps", "fax", "microsoft print")
OFFICE_PRINTER_KEYWORDS = (
    "all-in-one",
    "cloud",
    "deskjet",
    "inkjet",
    "laser",
    "microsoft ipp class driver",
    "officejet",
)
STALE_PORT_PREFIXES = ("WSD-", "IPP-", "IPPS-")

PRINTER_STATUS_FLAGS = {
    0x00000001: "已暂停",
    0x00000002: "错误",
    0x00000004: "正在删除",
    0x00000008: "卡纸",
    0x00000010: "缺纸",
    0x00000020: "手动进纸",
    0x00000040: "纸张问题",
    0x00000080: "离线",
    0x00000100: "正在输入输出",
    0x00000200: "忙碌",
    0x00000400: "正在打印",
    0x00000800: "出纸口满",
    0x00001000: "不可用",
    0x00002000: "等待中",
    0x00004000: "处理中",
    0x00008000: "初始化中",
    0x00010000: "预热中",
    0x00020000: "墨粉不足",
    0x00040000: "无墨粉",
    0x00100000: "需要人工处理",
    0x00200000: "内存不足",
    0x00400000: "盖子打开",
    0x00800000: "服务器未知",
    0x01000000: "省电模式",
}

JOB_ERROR_FLAGS = {
    0x00000001: "已暂停",
    0x00000002: "错误",
    0x00000020: "离线",
    0x00000040: "缺纸",
    0x00000200: "队列阻塞",
    0x00000400: "需要人工处理",
}

JOB_INFO_FLAGS = {
    0x00000004: "正在删除",
    0x00000008: "正在假脱机",
    0x00000010: "正在打印",
    0x00000080: "已打印",
    0x00000100: "已删除",
    0x00000800: "正在重启",
    0x00001000: "已完成",
    0x00004000: "本地渲染中",
}


def hidden_subprocess_kwargs() -> dict:
    """返回 Windows 子进程的隐藏窗口参数。

    PyInstaller 的 windowed exe 自己不会显示控制台，但它启动 powershell 这类
    子进程时，Windows 仍可能弹出一个空终端窗口。创建/修复代理打印机需要调用
    PowerShell，所以这里统一把子进程窗口压到后台。
    """
    if not getattr(subprocess, "_mswindows", False):
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


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
        if excluded and name.casefold() == excluded:
            continue
        if is_receipt_printer_candidate(name):
            result.append(name)
    return result


def is_receipt_printer_candidate(printer_name: str) -> bool:
    """判断打印机是否适合作为真实小票机候选。

    Windows 会长期保留以前连接过的 WSD/IPP 网络打印机、喷墨一体机和 PDF 虚拟打印机。
    这些对象即使能出现在系统打印机列表里，也不适合作为小票机 RAW 转发目标，因此这里
    只排除明显非小票机的对象，不做过强的品牌白名单。
    """
    name = printer_name.casefold()
    if any(keyword in name for keyword in VIRTUAL_PRINTER_KEYWORDS):
        return False
    info = get_printer_summary(printer_name)
    driver = str(info.get("driver_name") or "").casefold()
    port = str(info.get("port_name") or "")
    combined = f"{name} {driver}"
    if any(keyword in combined for keyword in OFFICE_PRINTER_KEYWORDS):
        return False
    if port.upper().startswith(STALE_PORT_PREFIXES):
        return False
    return True


def get_printer_summary(printer_name: str) -> dict:
    """读取打印机的基础驱动和端口信息；失败时返回空字段。"""
    try:
        import win32print  # type: ignore
    except ImportError:
        return {"driver_name": "", "port_name": "", "status_code": 0}
    try:
        handle = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(handle, 2)
            return {
                "driver_name": info.get("pDriverName") or "",
                "port_name": info.get("pPortName") or "",
                "status_code": int(info.get("Status") or 0),
            }
        finally:
            win32print.ClosePrinter(handle)
    except Exception:
        return {"driver_name": "", "port_name": "", "status_code": 0}


def get_printer_diagnostics(printer_name: str) -> dict:
    """读取真实打印机的驱动、端口、状态和当前队列。

    这个函数不发送任何打印内容，只用于现场排查“Windows 队列收到任务但不出纸”的原因。
    重点看 driver_name、port_name、printer_status 和 jobs。
    """
    name = printer_name.strip()
    if not name:
        raise PrinterError("请先选择真实小票机。")
    try:
        import win32print  # type: ignore
    except ImportError as exc:
        raise PrinterError("当前 Python 环境缺少 pywin32，无法读取打印机状态。") from exc

    try:
        handle = win32print.OpenPrinter(name)
    except Exception as exc:
        raise PrinterError(f"无法打开打印机：{name}", detail=str(exc)) from exc
    try:
        info = win32print.GetPrinter(handle, 2)
        jobs = win32print.EnumJobs(handle, 0, 999, 1)
    finally:
        win32print.ClosePrinter(handle)

    status = int(info.get("Status") or 0)
    return {
        "name": info.get("pPrinterName") or name,
        "driver_name": info.get("pDriverName") or "",
        "port_name": info.get("pPortName") or "",
        "status_code": status,
        "status_text": describe_printer_status(status),
        "job_count": len(jobs),
        "jobs": [summarize_print_job(job) for job in jobs],
    }


def send_raw_to_printer(printer_name: str, data: bytes) -> int:
    """向 Windows 打印队列发送 RAW bytes。

    Args:
        printer_name: 真实小票机名称。
        data: 原始打印 bytes，不能经过重新编码。

    Returns:
        Windows 打印任务 ID，用于后续查询队列状态。

    Raises:
        PrinterError: 未安装 pywin32、无法打开打印机或 Windows 打印 API 调用失败。
    """
    try:
        import win32print  # type: ignore
    except ImportError as exc:
        raise PrinterError("当前 Python 环境缺少 pywin32，无法转发打印。") from exc

    try:
        handle = win32print.OpenPrinter(printer_name)
    except Exception as exc:
        raise PrinterError(f"无法打开打印机：{printer_name}", detail=str(exc)) from exc
    try:
        job_id = win32print.StartDocPrinter(handle, 1, ("Receipt Voice Job", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            written = win32print.WritePrinter(handle, data)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
        if not job_id:
            raise PrinterError("Windows 没有返回打印任务 ID。")
        if isinstance(written, int) and written < len(data):
            raise PrinterError(
                "Windows 只接收了部分打印数据。",
                detail=f"已写入 {written} bytes，总计 {len(data)} bytes。",
            )
        return int(job_id)
    finally:
        win32print.ClosePrinter(handle)


def build_test_receipt_bytes() -> bytes:
    """生成一张用于排查真实打印机的 ESC/POS RAW 测试小票。"""
    lines = [
        "\x1b@",
        "\x1ba\x01",
        "Receipt Voice 测试打印",
        "\x1ba\x00",
        "如果看到这张纸，说明真实小票机可以出纸。",
        "下一步再测试 POS -> Receipt Voice -> 小票机链路。",
        "--------------------------------",
        "\n\n\n",
    ]
    return "\n".join(lines).encode("gbk", errors="replace") + b"\x1dV\x42\x00"


def test_printer(printer_name: str) -> dict:
    """向指定真实打印机发送 RAW ESC/POS 测试小票。"""
    name = printer_name.strip()
    if not name:
        raise PrinterError("请先选择真实小票机。")
    job_id = send_raw_to_printer(name, build_test_receipt_bytes())
    result = wait_for_print_job(name, job_id)
    result["test_type"] = "raw_escpos"
    return result


def test_printer_gdi(printer_name: str) -> dict:
    """使用 Windows GDI 普通打印路径发送测试页。

    如果 GDI 测试能出纸而 RAW 测试不出纸，通常说明这个 Windows 打印机驱动不透传
    ESC/POS RAW 指令；如果两者都不出纸，优先检查 Windows 打印机对象的端口和队列状态。
    """
    name = printer_name.strip()
    if not name:
        raise PrinterError("请先选择真实小票机。")
    try:
        import win32ui  # type: ignore
    except ImportError as exc:
        raise PrinterError("当前 Python 环境缺少 win32ui，无法执行 Windows 普通打印测试。") from exc

    dc = win32ui.CreateDC()
    job_id = None
    try:
        dc.CreatePrinterDC(name)
        job_id = dc.StartDoc("Receipt Voice GDI Test")
        dc.StartPage()
        for index, line in enumerate(build_gdi_test_lines()):
            dc.TextOut(120, 120 + index * 96, line)
        dc.EndPage()
        dc.EndDoc()
    except Exception as exc:
        try:
            dc.AbortDoc()
        except Exception:
            pass
        raise PrinterError(f"Windows 普通打印测试失败：{name}", detail=str(exc)) from exc
    finally:
        try:
            dc.DeleteDC()
        except Exception:
            pass

    if isinstance(job_id, int) and job_id > 0:
        result = wait_for_print_job(name, job_id)
    else:
        result = {
            "job_id": job_id,
            "status": "submitted",
            "message": "Windows 普通打印测试已提交；如果能出纸，说明 GDI 打印路径可用。",
        }
    result["test_type"] = "windows_gdi"
    return result


def build_gdi_test_lines() -> list[str]:
    """生成普通 Windows 打印测试页文本。"""
    return [
        "Receipt Voice 普通打印测试",
        "如果这张能出纸，说明 Windows/GDI 打印路径可用。",
        "如果 RAW 测试不出、普通测试能出，多半是驱动不透传 ESC/POS。",
        "请把这个结果截图发给开发者。",
    ]


def wait_for_print_job(printer_name: str, job_id: int, timeout_seconds: float = 4.0) -> dict:
    """等待打印任务进入稳定状态，并返回便于界面展示的诊断信息。

    Windows 的 RAW 打印 API 成功只代表任务进入了打印系统，不等于小票机一定出纸。
    因此测试打印后短暂轮询队列：如果任务消失，通常表示队列已完成；如果任务仍在并带
    有离线、缺纸、阻塞等状态，就把这些状态返回给用户排查。
    """
    deadline = time.monotonic() + timeout_seconds
    last_status = "队列处理中"
    while time.monotonic() < deadline:
        job = get_print_job(printer_name, job_id)
        if job is None:
            return {
                "job_id": job_id,
                "status": "completed",
                "message": f"测试任务 {job_id} 已被 Windows 队列接收并完成。",
            }
        last_status = describe_job_status(int(job.get("Status") or 0), str(job.get("pStatus") or ""))
        if has_error_job_status(int(job.get("Status") or 0)):
            raise PrinterError(
                f"测试任务 {job_id} 未能正常打印：{last_status}",
                detail=f"printer={printer_name}, job_id={job_id}, job={job}",
            )
        time.sleep(0.35)
    return {
        "job_id": job_id,
        "status": "pending",
        "message": f"测试任务 {job_id} 已提交，但队列仍显示：{last_status}。",
    }


def get_print_job(printer_name: str, job_id: int) -> dict | None:
    """读取 Windows 打印任务；任务已完成并离开队列时返回 None。"""
    try:
        import win32print  # type: ignore
    except ImportError as exc:
        raise PrinterError("当前 Python 环境缺少 pywin32，无法查询打印队列。") from exc

    handle = win32print.OpenPrinter(printer_name)
    try:
        try:
            return win32print.GetJob(handle, int(job_id), 1)
        except Exception:
            return None
    finally:
        win32print.ClosePrinter(handle)


def has_error_job_status(status: int) -> bool:
    """判断打印任务状态是否包含需要现场处理的错误。"""
    return any(status & flag for flag in JOB_ERROR_FLAGS)


def describe_job_status(status: int, status_text: str = "") -> str:
    """把 Windows 打印任务位标记转换成中文状态文本。"""
    labels = [label for flag, label in {**JOB_ERROR_FLAGS, **JOB_INFO_FLAGS}.items() if status & flag]
    if status_text.strip():
        labels.append(status_text.strip())
    return "、".join(labels) if labels else "队列中，无明确错误"


def describe_printer_status(status: int) -> str:
    """把 Windows 打印机状态位标记转换成中文状态文本。"""
    labels = [label for flag, label in PRINTER_STATUS_FLAGS.items() if status & flag]
    return "、".join(labels) if labels else "就绪或无明确错误"


def summarize_print_job(job: dict) -> dict:
    """把 Windows 打印任务对象压缩成前端容易展示的结构。"""
    status = int(job.get("Status") or 0)
    return {
        "job_id": job.get("JobId"),
        "document": job.get("pDocument") or "",
        "status_code": status,
        "status_text": describe_job_status(status, str(job.get("pStatus") or "")),
        "pages_printed": job.get("PagesPrinted") or 0,
        "total_pages": job.get("TotalPages") or 0,
    }


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
    if ($Existing.DriverName -ne $DriverName) {{
        # 已存在的代理打印机不能稳定地直接换驱动，驱动不一致时删除重建最可靠。
        Get-PrintJob -PrinterName $ProxyPrinterName -ErrorAction SilentlyContinue |
            Remove-PrintJob -ErrorAction SilentlyContinue
        Remove-Printer -Name $ProxyPrinterName -ErrorAction Stop
        Add-Printer -Name $ProxyPrinterName -DriverName $DriverName -PortName $PortName
    }} else {{
        Set-Printer -Name $ProxyPrinterName -PortName $PortName
    }}
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
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "创建代理打印机失败。"
        raise PrinterError(message)
    return result.stdout.strip()


def ps_quote(value: str) -> str:
    """把 Python 字符串安全转换成 PowerShell 单引号字符串。"""
    return "'" + value.replace("'", "''") + "'"
