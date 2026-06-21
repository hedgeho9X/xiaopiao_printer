# Receipt Voice PoC

最小验证目标：

1. Python 能向 Windows 小票打印机发送 RAW ESC/POS 数据。
2. 本地中间件能监听 `127.0.0.1:9100`，抓到打印 bytes 并保存。
3. 中间件能把原始 bytes 原样转发给真实小票机。
4. 抓到的 `.bin` 能做文本提取和简单预览图。

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 1. List Printers

```powershell
python print_raw.py list
```

## 2. Direct Print Test

```powershell
python print_raw.py test-printer "真实小票机名称"
```

## 3. Desktop App

新桌面版入口：

```powershell
python app_desktop.py
```

新版本使用：

```text
Python 后端 + SQLite + pywebview Web UI
```

相关设计文档：

```text
docs\architecture.md
docs\database-schema.md
docs\shortcuts.md
docs\ui-layout.md
```

打包主程序：

```powershell
pip install pyinstaller
.\build_monitor.ps1
```

`build_monitor.ps1` 会默认调用 `prepare_webview2_runtime.ps1`，把微软 WebView2
Fixed Version Runtime 下载到 `vendor\WebView2Runtime` 并打包进 exe。这样店里电脑
即使没有单独安装 WebView2，也能正常显示 UI。代价是 exe 体积会明显变大。

生成文件：

```text
dist\ReceiptVoiceMonitor.exe
```

## 4. Legacy Capture And Forward

以下是旧 Tkinter PoC，保留用于现场回归和排查，不再作为主开发入口。

图形界面版本：

```powershell
python app_gui.py
```

界面里可以启动/停止转发、选择目标打印机、发送测试小票、查看抓包路径、转发计数、实时日志和最新解析文本。

现场使用可以直接自动启动转发：

```powershell
.\run_gui.ps1
```

或者指定真实小票机：

```powershell
.\run_gui.ps1 -TargetPrinter "GP-5850II"
```

创建一个 Windows 代理打印机，让其他软件可以选择它打印：

```powershell
.\setup_proxy_printer.ps1
```

默认会创建：

```text
Receipt Voice Proxy -> 127.0.0.1:9100
```

启动中间件：

```powershell
python capture_server.py --printer "真实小票机名称"
```

向中间件发送测试小票：

```powershell
python print_raw.py test-tcp 127.0.0.1 9100
```

或者模拟真实软件打印到代理打印机：

```powershell
python print_raw.py test-printer "Receipt Voice Proxy"
```

没有小票机时，可以只验证抓包落盘：

```powershell
python capture_server.py --no-forward
python print_raw.py test-tcp 127.0.0.1 9100
```

## 5. Inspect Capture

```powershell
python inspect_capture.py captures\某个文件.bin
```

会输出十六进制摘要、可读文本、识别到的 ESC/POS 标记，并生成：

```text
captures\某个文件.preview.png
```

## 6. Test Sender App

给店员试用的发送器：

```powershell
python test_sender_app.py
```

打包成单个 exe：

```powershell
pip install pyinstaller
.\build_test_sender.ps1
```

生成文件：

```text
dist\ReceiptTestSender.exe
```
