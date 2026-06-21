"""Receipt Voice 桌面版入口。

本文件负责初始化 SQLite、后端服务和 pywebview 窗口。它是最终
all-in-one exe 的主入口，不再使用旧 Tkinter PoC 界面。
"""

from __future__ import annotations

import sys
from pathlib import Path

from app_core.bridge_api import BridgeApi
from app_core.database import app_base_dir, connect, initialize
from app_core.monitor import MultiReceiptMonitor
from app_core.order_repair import repair_legacy_orders
from app_core.order_store import OrderStore
from app_core.settings import SettingsStore
from app_core.speech import SpeechService


def ui_index_path() -> Path:
    """返回内置 Web UI 的 index.html 路径。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # PyInstaller onefile 会把 --add-data 的 ui/ 解压到 _MEIPASS。
        return Path(sys._MEIPASS) / "ui" / "index.html"  # type: ignore[attr-defined]
    return app_base_dir() / "ui" / "index.html"


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
