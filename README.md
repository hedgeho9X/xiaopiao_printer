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

## 3. Capture And Forward

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

## 4. Inspect Capture

```powershell
python inspect_capture.py captures\某个文件.bin
```

会输出十六进制摘要、可读文本、识别到的 ESC/POS 标记，并生成：

```text
captures\某个文件.preview.png
```
