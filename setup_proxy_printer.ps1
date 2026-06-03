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

