"""Receipt Voice 桌面版入口。

本文件负责初始化 SQLite、后端服务和 pywebview 窗口。它是最终
all-in-one exe 的主入口，不再使用旧 Tkinter PoC 界面。
"""

from __future__ import annotations

import os
import sys
import tkinter.messagebox
import webbrowser
from pathlib import Path

from app_core.bridge_api import BridgeApi
from app_core.database import app_base_dir, connect, initialize
from app_core.monitor import MultiReceiptMonitor
from app_core.order_repair import repair_legacy_orders
from app_core.order_store import OrderStore
from app_core.settings import SettingsStore
from app_core.speech import SpeechService

WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def ui_index_path() -> Path:
    """返回内置 Web UI 的 index.html 路径。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # PyInstaller onefile 会把 --add-data 的 ui/ 解压到 _MEIPASS。
        return Path(sys._MEIPASS) / "ui" / "index.html"  # type: ignore[attr-defined]
    return app_base_dir() / "ui" / "index.html"


def fixed_webview2_runtime_path() -> Path | None:
    """返回随程序携带的 WebView2 Fixed Version Runtime 目录。"""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "WebView2Runtime")  # type: ignore[attr-defined]
    candidates.append(app_base_dir() / "vendor" / "WebView2Runtime")
    for path in candidates:
        if (path / "msedgewebview2.exe").exists():
            return path
    return None


def has_system_webview2_runtime() -> bool:
    """检查 Windows 是否已经安装 Microsoft Edge WebView2 Runtime。"""
    if os.name != "nt":
        return True
    try:
        import winreg
    except ImportError:
        return False

    if not has_dotnet_462_or_newer(winreg):
        return False

    client_id = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    key_paths = [
        rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{client_id}",
        rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{client_id}",
    ]
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key_path in key_paths:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    version, _ = winreg.QueryValueEx(key, "pv")
                    if str(version).strip():
                        return True
            except OSError:
                continue
    return False


def has_dotnet_462_or_newer(winreg_module) -> bool:
    """检查 pywebview 的 WebView2 后端所需的 .NET 版本。"""
    try:
        with winreg_module.OpenKey(
            winreg_module.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
        ) as key:
            release, _ = winreg_module.QueryValueEx(key, "Release")
            return int(release) >= 394802
    except OSError:
        return False


def configure_webview_runtime(webview_module) -> bool:
    """配置 WebView2 运行时，并返回当前电脑是否能使用现代 WebView。"""
    if os.name == "nt":
        try:
            import winreg
        except ImportError:
            return False
        if not has_dotnet_462_or_newer(winreg):
            return False

    fixed_runtime = fixed_webview2_runtime_path()
    if fixed_runtime:
        webview_module.settings["WEBVIEW2_RUNTIME_PATH"] = str(fixed_runtime)
        return True
    return has_system_webview2_runtime()


def show_webview2_missing_message() -> None:
    """提示用户安装 WebView2，避免进入半坏的旧 IE 界面。"""
    message = (
        "这台电脑缺少 Microsoft Edge WebView2 Runtime，或系统 .NET 版本过旧，"
        "Receipt Voice 无法正常显示界面。\n\n"
        "处理办法：\n"
        "1. 安装 Microsoft Edge WebView2 Runtime 后重新打开程序。\n"
        "2. 如果仍打不开，请升级 Windows/.NET，或让开发者提供包含 "
        "WebView2 Fixed Version Runtime 的离线版安装包。\n\n"
        f"下载地址：{WEBVIEW2_DOWNLOAD_URL}"
    )
    tkinter.messagebox.showerror("Receipt Voice 需要 WebView2", message)
    try:
        webbrowser.open(WEBVIEW2_DOWNLOAD_URL)
    except Exception:
        pass


def build_api() -> tuple[BridgeApi, MultiReceiptMonitor]:
    """初始化后端服务并返回 bridge API。"""
    conn = connect()
    initialize(conn)
    settings = SettingsStore(conn)
    settings.initialize_defaults()
    store = OrderStore(conn)
    repair_legacy_orders(store)
    monitor = MultiReceiptMonitor(settings, store)
    speech = SpeechService(settings)
    api = BridgeApi(store=store, settings=settings, monitor=monitor, speech=speech)
    return api, monitor


def main() -> int:
    """启动桌面窗口。"""
    try:
        import webview  # type: ignore
    except ImportError:
        print("缺少 pywebview，请先运行：python -m pip install pywebview")
        return 1

    if not configure_webview_runtime(webview):
        show_webview2_missing_message()
        return 1

    index_path = ui_index_path()
    if not index_path.exists():
        print(f"找不到 UI 文件：{index_path}")
        return 1

    api, monitor = build_api()
    window = webview.create_window(
        title="Receipt Voice",
        url=index_path.as_uri(),
        js_api=api,
        width=1280,
        height=820,
        min_size=(960, 640),
    )

    def on_closed() -> None:
        """窗口关闭时停止后台监听。"""
        monitor.stop()

    window.events.closed += on_closed
    # 明确使用 Windows WebView2，避免本地 HTML 被系统文件关联交给 WPS/浏览器。
    webview.start(debug=False, gui="edgechromium")
    return 0


if __name__ == "__main__":
    sys.exit(main())
