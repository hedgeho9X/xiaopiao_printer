# 打包主监控程序。
#
# 作用：
# - 使用 PyInstaller 将 app_desktop.py 打包成 dist\ReceiptVoiceMonitor.exe。
# - 这个 exe 是现场给店里使用的新桌面主程序。
#
# 注意：
# - 优先使用项目内 .venv，避免 Anaconda 把无关依赖打进 exe。
# - 如果 exe 正在运行，打包时可能因为文件被占用而失败，需要先关闭旧程序。
$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ScriptDir)) {
  $ScriptDir = (Get-Location).Path
}

$PythonExe = Join-Path -Path $ScriptDir -ChildPath ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
  $PythonExe = "python"
}

Write-Host "Using Python: $PythonExe"

Push-Location $ScriptDir
try {
  & $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name ReceiptVoiceMonitor `
    --add-data "ui;ui" `
    --hidden-import win32print `
    --hidden-import pywintypes `
    --hidden-import win32com.client `
    --hidden-import pythoncom `
    --hidden-import openai `
    --hidden-import socksio `
    --hidden-import webview.platforms.edgechromium `
    app_desktop.py
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
