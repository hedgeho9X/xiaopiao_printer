python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onefile `
  --name ReceiptVoiceMonitor `
  --hidden-import win32print `
  --hidden-import pywintypes `
  app_gui.py

