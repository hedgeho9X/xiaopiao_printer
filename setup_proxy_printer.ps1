# 创建或修复 Windows 代理打印机。
#
# 作用：
# - 创建 Receipt Voice Proxy。
# - 让它指向本机 127.0.0.1:9100。
# - 美团/银豹选择这个打印机时，打印数据会进入我们的程序。
#
# 注意：
# - 创建打印机和端口有时需要管理员权限。
param(
    [string]$ProxyPrinterName = "Receipt Voice Proxy",
    [string]$DriverName = "GP-5850 Series",
    [string]$PortName = "IP_127.0.0.1",
    [string]$HostAddress = "127.0.0.1",
    [int]$PortNumber = 9100
)

$existingPort = Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue
if (-not $existingPort) {
    Add-PrinterPort -Name $PortName -PrinterHostAddress $HostAddress -PortNumber $PortNumber
}

$existingPrinter = Get-Printer -Name $ProxyPrinterName -ErrorAction SilentlyContinue
if (-not $existingPrinter) {
    Add-Printer -Name $ProxyPrinterName -DriverName $DriverName -PortName $PortName
}

Get-Printer -Name $ProxyPrinterName |
    Select-Object Name,DriverName,PortName,PrinterStatus |
    Format-List
