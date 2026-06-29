# 打包主监控程序。
#
# 作用：
# - 使用 PyInstaller 将 app_desktop.py 打包成 dist\ReceiptVoiceMonitor.exe。
# - 这个 exe 是现场给店里使用的新桌面主程序。
#
# 注意：
# - 优先使用项目内 .venv，避免 Anaconda 把无关依赖打进 exe。
# - 默认携带 vendor\WebView2Runtime，避免店里电脑缺少 WebView2 时 UI 变形。
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

$PrepareWebView2Script = Join-Path -Path $ScriptDir -ChildPath "prepare_webview2_runtime.ps1"
if (-not (Test-Path -LiteralPath $PrepareWebView2Script)) {
  throw "Missing prepare_webview2_runtime.ps1"
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $PrepareWebView2Script
if ($LASTEXITCODE -ne 0) {
  throw "Failed to prepare WebView2 fixed runtime."
}

$FixedRuntimeExe = Join-Path -Path $ScriptDir -ChildPath "vendor\WebView2Runtime\msedgewebview2.exe"
if (-not (Test-Path -LiteralPath $FixedRuntimeExe)) {
  throw "Missing vendor\WebView2Runtime\msedgewebview2.exe"
}

$PyInstallerArgs = @(
  "--noconfirm",
  "--clean",
  "--windowed",
  "--onefile",
  "--name", "ReceiptVoiceMonitor",
  "--add-data", "ui;ui",
  "--add-data", "vendor\WebView2Runtime;WebView2Runtime",
  "--hidden-import", "win32print",
  "--hidden-import", "win32ui",
  "--hidden-import", "win32timezone",
  "--hidden-import", "pywintypes",
  "--hidden-import", "win32com.client",
  "--hidden-import", "pythoncom",
  "--hidden-import", "openai",
  "--hidden-import", "socksio",
  "--hidden-import", "webview.platforms.edgechromium"
)

Write-Host "Bundling fixed WebView2 runtime from vendor\WebView2Runtime"

$PyInstallerArgs += "app_desktop.py"

Push-Location $ScriptDir
try {
  & $PythonExe -m PyInstaller @PyInstallerArgs
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
