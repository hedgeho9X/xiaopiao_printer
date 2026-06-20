# 打包主监控程序。
#
# 作用：
# - 使用 PyInstaller 将 app_gui.py 打包成 dist\ReceiptVoiceMonitor.exe。
# - 这个 exe 是现场给店里使用的主程序。
#
# 注意：
# - 当前构建环境是 Anaconda，所以产物体积会比较大。
# - 如果 exe 正在运行，打包时可能因为文件被占用而失败，需要先关闭旧程序。
python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onefile `
  --name ReceiptVoiceMonitor `
  --hidden-import win32print `
  --hidden-import pywintypes `
  app_gui.py
