# One-time setup for Bluetooth LE capture. Capture itself runs inside PAi Health:
# Ingest > Bluetooth devices > Scan > Listen. Readings are saved to your encrypted vault.
$ErrorActionPreference = "Stop"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw "Install Python 3.11+ (py launcher)" }
py -m pip install "bleak>=0.22,<2"
Write-Host "Bluetooth support installed. Restart PAi Health and open Ingest > Bluetooth devices."
