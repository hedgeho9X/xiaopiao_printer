# 启动 GUI 监控程序的源码版。
#
# 作用：
# - 调用 app_gui.py，并默认自动开始监听。
# - 开发调试时使用；现场交付优先使用 ReceiptVoiceMonitor.exe。
param(
    [string]$TargetPrinter = "GP-5850II"
)

python app_gui.py --auto-start --target $TargetPrinter
