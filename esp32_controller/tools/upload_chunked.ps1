<#
Uploads build\esp32_controller.ino.bin to the ESP32 in small verified chunks.

Use this when the normal Arduino IDE upload fails partway with
"No more data to read from the serial port" or "Possible serial noise".
Each 64 KB chunk is a short, separately verified session; a failed chunk is retried.

Steps:
  1. Compile first (Arduino IDE: Sketch > Export Compiled Binary, or arduino-cli
     compile --output-dir build).
  2. Run:  powershell -ExecutionPolicy Bypass -File tools\upload_chunked.ps1 -Port COM23
  3. When "Connecting..." waits, HOLD the BOOT button, tap EN once, and keep holding
     BOOT until the script prints "ALL CHUNKS WRITTEN AND VERIFIED".

Add -Full to also write the bootloader, partition table and boot_app0 (needed on a
board that has never run this firmware, or after changing the ESP32 core version).
#>
param(
    [Parameter(Mandatory = $true)][string]$Port,
    [int]$Baud = 115200,
    [int]$ChunkKB = 64,
    [int]$Retries = 5,
    [switch]$Full
)

# Not 'Stop': Windows PowerShell treats esptool's stderr as a terminating error,
# which would skip the retry. Failures are detected from exit codes instead.
$ErrorActionPreference = 'Continue'
$sketchDir = Split-Path -Parent $PSScriptRoot
$build = Join-Path $sketchDir 'build'
$app = Join-Path $build 'esp32_controller.ino.bin'
if (-not (Test-Path $app)) { throw "Not found: $app. Compile with --output-dir build first." }

$espRoot = Join-Path $env:LOCALAPPDATA 'Arduino15\packages\esp32'
$esptool = Get-ChildItem (Join-Path $espRoot 'tools\esptool_py') -Recurse -Filter esptool.exe |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $esptool) { throw "esptool.exe not found under $espRoot. Install the esp32 board package." }

function Write-Region([string]$address, [string]$file, [string]$label) {
    for ($try = 1; $try -le $Retries; $try++) {
        $out = & $esptool --chip esp32 --port $Port --baud $Baud --connect-attempts 0 `
            --before default-reset --after no-reset write-flash -z $address $file 2>&1
        $ok = ($LASTEXITCODE -eq 0) -and ($out -match 'Hash of data verified')
        $err = $out | Select-String 'fatal error' | Select-Object -First 1
        Write-Host ("{0} @ {1} try {2}: {3}" -f $label, $address, $try, $(if ($ok) { 'OK' } else { "FAIL $err" }))
        if ($ok) { return }
    }
    throw "Giving up on $label after $Retries tries."
}

if ($Full) {
    $core = Get-ChildItem (Join-Path $espRoot 'hardware\esp32') -Directory | Sort-Object Name -Descending | Select-Object -First 1
    $bootApp0 = Join-Path $core.FullName 'tools\partitions\boot_app0.bin'
    Write-Region '0x1000' (Join-Path $build 'esp32_controller.ino.bootloader.bin') 'bootloader'
    Write-Region '0x8000' (Join-Path $build 'esp32_controller.ino.partitions.bin') 'partitions'
    Write-Region '0xe000' $bootApp0 'boot_app0'
}

$bytes = [IO.File]::ReadAllBytes($app)
$chunk = $ChunkKB * 1024
$chunkDir = Join-Path $build 'chunks'
New-Item -ItemType Directory -Force $chunkDir | Out-Null
$count = [Math]::Ceiling($bytes.Length / $chunk)
Write-Host "app: $($bytes.Length) bytes in $count chunks of $ChunkKB KB (esptool: $esptool)"

for ($i = 0; $i -lt $count; $i++) {
    $len = [Math]::Min($chunk, $bytes.Length - $i * $chunk)
    $part = New-Object byte[] $len
    [Array]::Copy($bytes, $i * $chunk, $part, 0, $len)
    $file = Join-Path $chunkDir "c$i.bin"
    [IO.File]::WriteAllBytes($file, $part)
    Write-Region ('0x{0:x}' -f (0x10000 + $i * $chunk)) $file ("chunk {0}/{1}" -f ($i + 1), $count)
}

Write-Host 'ALL CHUNKS WRITTEN AND VERIFIED. Release BOOT and press EN to start the firmware.'
