# 准备 WebView2 固定版运行时。
#
# 作用：
# - 从微软官方 WebView2 页面解析最新 Fixed Version Runtime cab 下载地址。
# - 下载并解压到 vendor\WebView2Runtime，供 PyInstaller 默认打包进 exe。
#
# 注意：
# - vendor\WebView2Runtime 是大二进制目录，不提交到 git。
# - 如果下载失败，可以手动下载 Fixed Version Runtime x64 cab 后解压到同名目录。

param(
  [ValidateSet("x64", "x86", "arm64")]
  [string]$Arch = "x64",

  [switch]$Force
)

$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ScriptDir)) {
  $ScriptDir = (Get-Location).Path
}

$VendorDir = Join-Path -Path $ScriptDir -ChildPath "vendor"
$RuntimeDir = Join-Path -Path $VendorDir -ChildPath "WebView2Runtime"
$RuntimeExe = Join-Path -Path $RuntimeDir -ChildPath "msedgewebview2.exe"
$DownloadPage = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"

if ((Test-Path -LiteralPath $RuntimeExe) -and -not $Force) {
  Write-Host "WebView2 fixed runtime already exists: $RuntimeDir"
  exit 0
}

New-Item -ItemType Directory -Force -Path $VendorDir | Out-Null

Write-Host "Fetching WebView2 fixed runtime metadata..."
$Page = Invoke-WebRequest -UseBasicParsing -Uri $DownloadPage
$Pattern = "https:\\u002F\\u002F[^`"']+?Microsoft\.WebView2\.FixedVersionRuntime\.([0-9.]+)\.$Arch\.cab"
$Matches = [regex]::Matches($Page.Content, $Pattern)
if ($Matches.Count -eq 0) {
  throw "Cannot find WebView2 Fixed Version Runtime $Arch download URL from $DownloadPage"
}

$Version = $Matches[0].Groups[1].Value
$CabUrl = $Matches[0].Value.Replace("\u002F", "/")
$CabPath = Join-Path -Path $VendorDir -ChildPath "Microsoft.WebView2.FixedVersionRuntime.$Version.$Arch.cab"
$TempDir = Join-Path -Path $VendorDir -ChildPath "_WebView2RuntimeExtract"

Write-Host "Downloading WebView2 fixed runtime $Version $Arch..."
Write-Host $CabUrl
if ((Test-Path -LiteralPath $CabPath) -and -not $Force) {
  Write-Host "Using existing cab: $CabPath"
} else {
  Invoke-WebRequest -UseBasicParsing -Uri $CabUrl -OutFile $CabPath
}

if (Test-Path -LiteralPath $TempDir) {
  Remove-Item -LiteralPath $TempDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

Write-Host "Extracting WebView2 fixed runtime..."
& expand.exe -F:* $CabPath $TempDir | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "expand.exe failed with exit code $LASTEXITCODE"
}

$ExtractedExe = Get-ChildItem -LiteralPath $TempDir -Recurse -Filter "msedgewebview2.exe" |
  Select-Object -First 1
if (-not $ExtractedExe) {
  throw "Cannot find msedgewebview2.exe after extracting $CabPath"
}

if (Test-Path -LiteralPath $RuntimeDir) {
  Remove-Item -LiteralPath $RuntimeDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Copy-Item -Path (Join-Path -Path $ExtractedExe.DirectoryName -ChildPath "*") `
  -Destination $RuntimeDir `
  -Recurse `
  -Force

Remove-Item -LiteralPath $TempDir -Recurse -Force

Write-Host "WebView2 fixed runtime is ready: $RuntimeDir"
