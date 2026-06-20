# 打包测试发送器。
#
# 作用：
# - 使用 PyInstaller 将 test_sender_app.py 打包成 dist\ReceiptTestSender.exe。
# - 这个工具只用于没有美团/银豹时手动发一张测试小票。
python -m PyInstaller --noconfirm --clean --windowed --onefile --name ReceiptTestSender test_sender_app.py
