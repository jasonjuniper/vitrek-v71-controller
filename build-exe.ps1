# build-exe.ps1 — build the Juniper HiPot Controller as a PyInstaller onefile exe.
#
# Produces dist\HiPotController.exe and a versioned copy dist\HiPotController-<Version>.exe,
# then prints its SHA256 + size (what the deployable package's install script pins).
#
# Bundles: static\ (brand assets/css/fonts/js), pvd_profiles.json (seed; the app copies
# it to ProgramData on first run), plc\rig_config.json (read-only), and the x64 Silicon
# Labs CP2110 DLLs at the exact relative path v71_driver.py loads them from.
#
#   powershell -ExecutionPolicy Bypass -File build-exe.ps1 -Version 1.1.0 [-Python <path>]
#
# -Python lets the automated build host (pc-deploy) point at its own build venv; if omitted,
# it resolves a sensible local interpreter so the recipe is portable across machines.
param([string]$Version = "1.1.0", [string]$Python = "")

$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $cands = @(
        'C:\Users\ENG2\AppData\Local\Programs\Python\Python313\python.exe',
        'C:\build\venv\Scripts\python.exe'
    )
    $Python = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Python) {
        $Python = (Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*WindowsApps*' } | Select-Object -First 1).Source
    }
}
if (-not $Python -or -not (Test-Path $Python)) { throw "No Python interpreter found; pass -Python <path to python.exe>" }
$py   = $Python
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host "Using Python: $py"

$dll = 'software\drivers\USB_DLLs_and_Headers\USB DLLs and Headers\x64'

Write-Host "Ensuring build deps..."
& $py -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller 2>&1 | Out-Null

if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist)  { Remove-Item dist  -Recurse -Force }

Write-Host "Building HiPotController.exe (onefile)..."
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name HiPotController `
  --icon "static\assets\juniper-mark.ico" `
  --add-data "static;static" `
  --add-data "pvd_profiles.json;." `
  --add-data "plc\rig_config.json;plc" `
  --add-binary "$dll\SLABHIDtoUART.dll;$dll" `
  --add-binary "$dll\SLABHIDDevice.dll;$dll" `
  --collect-submodules pymodbus `
  app.py

$out = 'dist\HiPotController.exe'
if (-not (Test-Path $out)) { throw "Build failed: $out not produced" }
$verOut = "dist\HiPotController-$Version.exe"
Copy-Item $out $verOut -Force

$h = (Get-FileHash $verOut -Algorithm SHA256).Hash
$sz = (Get-Item $verOut).Length
Write-Host ""
Write-Host "BUILT  $verOut"
Write-Host "SHA256 $h"
Write-Host "SIZE   $sz"
