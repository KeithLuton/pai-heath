<#
  PAi Health -> MSIX for the Microsoft Store.

  One-time on the build PC:
    winget install Python.Python.3.12 OpenJS.NodeJS.LTS UB-Mannheim.TesseractOCR Microsoft.WindowsSDK.10.0.26100
  (Windows SDK provides makeappx / makepri / signtool / appcert.)

  Store build (identity values from Partner Center > Product identity):
    .\packaging\build.ps1 -IdentityName "12345PAiLuton.PAiHealth" -Publisher "CN=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX" -Icon C:\art\paihealth-1024.png -KbPath C:\PAiHealth\psi_health_kb.db

  Local test install (Developer Mode on, no signing needed):
    .\packaging\build.ps1 ... -Register
  Run the Windows App Certification Kit on the .msix:
    .\packaging\build.ps1 ... -Wack
#>
[CmdletBinding()]
param(
  [string]$IdentityName = "PAiLuton.PAiHealth",
  [string]$Publisher = "CN=D7A83BB4-9693-4195-82DE-2A76C46F6F0F",   # Partner Center product identity, Store ID 9PNXSSJWZC9T
  [string]$PublisherDisplayName = "PAi Luton",
  [string]$Version = "",
  [string]$Icon = "",
  [string]$KbPath = "",
  [string]$FdcZip = "",
  [string]$LlamaTag = "latest",
  [ValidateSet("vulkan", "cpu")][string]$LlamaFlavor = "vulkan",
  [string]$TesseractDir = "$env:ProgramFiles\Tesseract-OCR",
  [switch]$SkipLlama,
  [switch]$SkipUi,
  [switch]$Register,
  [switch]$Sign,
  [switch]$Wack
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $Root "build"
$Res = Join-Path $Build "resources"
$Assets = Join-Path $Build "assets"
$Layout = Join-Path $Build "layout"
$Out = Join-Path $Root "out"
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------------ version
if (-not $Version) {
  $m = Select-String -Path (Join-Path $Root "engine\runtime.py") -Pattern 'APP_VERSION = "(\d+)\.(\d+)\.(\d+)"'
  if (-not $m) { Fail "APP_VERSION not found in engine\runtime.py" }
  $g = $m.Matches[0].Groups
  $Version = "$($g[1].Value).$($g[2].Value).$($g[3].Value).0"     # Store requires revision 0
}
if ($Version -notmatch '^\d+\.\d+\.\d+\.0$') { Fail "Version must be X.Y.Z.0 (got $Version)" }
if ($Publisher -like "*TEST*" -and -not $Register) {
  Write-Host "WARNING: Publisher is the TEST placeholder. Store uploads need the CN=... value from Partner Center." -ForegroundColor Yellow
}

# ------------------------------------------------------------------ tools
Step "Checking tools"
$kits = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
$sdk = Get-ChildItem $kits -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path "$($_.FullName)\x64\makeappx.exe" } |
       Sort-Object Name -Descending | Select-Object -First 1
if (-not $sdk) { Fail "Windows SDK not found. winget install Microsoft.WindowsSDK.10.0.26100" }
$MakeAppx = "$($sdk.FullName)\x64\makeappx.exe"; $MakePri = "$($sdk.FullName)\x64\makepri.exe"; $SignTool = "$($sdk.FullName)\x64\signtool.exe"
$py = (Get-Command py -ErrorAction SilentlyContinue) ; if (-not $py) { Fail "Python launcher 'py' not found. winget install Python.Python.3.12" }
if (-not $SkipUi -and -not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail "npm not found. winget install OpenJS.NodeJS.LTS" }

# ------------------------------------------------------------------ python env
Step "Python build environment"
$venv = Join-Path $Build "venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) { & py -3.12 -m venv $venv; if ($LASTEXITCODE) { Fail "venv failed" } }
$Py = "$venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install --quiet -r (Join-Path $Root "engine\requirements-dev.txt")
& $Py -m pip install --quiet -r (Join-Path $Root "engine\requirements.txt") "pywebview>=5.3,<6" "pyinstaller>=6.10" "bleak>=0.22,<2" pip-licenses
if ($LASTEXITCODE) { Fail "pip install failed" }

# ------------------------------------------------------------------ tests
Step "Engine tests"
Push-Location (Join-Path $Root "engine")
& $Py -m unittest discover -s tests -q
$t = $LASTEXITCODE
Pop-Location
if ($t) { Fail "Tests failed - not packaging a failing build" }
& $Py (Join-Path $Root "tools\lock.py") verify
if ($LASTEXITCODE) { Fail "Locked files changed (tools\lock.py verify)" }

# ------------------------------------------------------------------ UI
if (-not $SkipUi) {
  Step "Building the UI"
  Push-Location $Root
  npm ci --no-audit --no-fund; if ($LASTEXITCODE) { Pop-Location; Fail "npm ci failed" }
  npm run build; if ($LASTEXITCODE) { Pop-Location; Fail "UI build failed" }
  Pop-Location
}
if (-not (Test-Path (Join-Path $Root "dist\index.html"))) { Fail "dist\index.html missing" }

# ------------------------------------------------------------------ bundled tools
Step "Bundled tools"
New-Item -ItemType Directory -Force $Res | Out-Null

# Tesseract OCR (Apache-2.0): only the engine, its DLLs, and English + orientation data.
$tes = Join-Path $Res "tesseract"
if (-not (Test-Path "$TesseractDir\tesseract.exe")) { Fail "Tesseract not found at $TesseractDir. winget install UB-Mannheim.TesseractOCR" }
Remove-Item $tes -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$tes\tessdata" | Out-Null
Copy-Item "$TesseractDir\tesseract.exe", "$TesseractDir\*.dll" $tes
Copy-Item "$TesseractDir\tessdata\eng.traineddata", "$TesseractDir\tessdata\osd.traineddata" "$tes\tessdata"
Get-ChildItem $TesseractDir -Filter "*LICENSE*" -ErrorAction SilentlyContinue | Copy-Item -Destination $tes
Write-Host "  tesseract: $((Get-ChildItem $tes -Recurse | Measure-Object Length -Sum).Sum / 1MB -as [int]) MB"

# llama.cpp server (MIT). Must ship inside the package: Store policy 10.2.2 forbids downloading code later.
$llama = Join-Path $Res "llama"
if (-not $SkipLlama) {
  # "latest" isn't always a build with Windows binaries, so walk recent releases for the first that has one.
  $hdr = @{ "User-Agent" = "paihealth-build" }
  $rels = if ($LlamaTag -eq "latest") { Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=40" -Headers $hdr }
          else { @(Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$LlamaTag" -Headers $hdr) }
  $rel = $null; $asset = $null
  foreach ($r in $rels) {
    if ($r.draft) { continue }          # llama.cpp marks every bNNNN build as a pre-release
    $asset = $r.assets | Where-Object { $_.name -match "bin-win-.*$LlamaFlavor.*x64\.zip$" -and $_.name -notmatch "cudart" } | Select-Object -First 1
    if ($asset) { $rel = $r; break }
  }
  if (-not $asset) { Fail "No Windows $LlamaFlavor x64 build found in recent llama.cpp releases. Re-run with -SkipLlama, or -LlamaTag bNNNN" }
  $zip = Join-Path $Build $asset.name
  if (-not (Test-Path $zip)) { Invoke-WebRequest $asset.browser_download_url -OutFile $zip }
  $tmp = Join-Path $Build "llama-unzip"; Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Expand-Archive $zip $tmp
  $server = Get-ChildItem $tmp -Recurse -Filter "llama-server.exe" | Select-Object -First 1
  if (-not $server) { Fail "llama-server.exe not in $($asset.name)" }
  Remove-Item $llama -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force $llama | Out-Null
  Copy-Item $server.FullName $llama
  Copy-Item (Join-Path $server.DirectoryName "*.dll") $llama
  Set-Content (Join-Path $llama "VERSION.txt") "llama.cpp $($rel.tag_name) $($asset.name)"
  Invoke-WebRequest "https://raw.githubusercontent.com/ggml-org/llama.cpp/$($rel.tag_name)/LICENSE" -OutFile (Join-Path $llama "LICENSE.txt")
  Write-Host "  llama.cpp $($rel.tag_name) ($LlamaFlavor): $((Get-ChildItem $llama | Measure-Object Length -Sum).Sum / 1MB -as [int]) MB"
} elseif (-not (Test-Path "$llama\llama-server.exe")) {
  Write-Host "  WARNING: no llama-server bundled - private AI will not work in this build" -ForegroundColor Yellow
}

# Knowledge base (NLM PubMed-derived; read-only inside the package)
$kbDir = Join-Path $Res "knowledge"
Remove-Item $kbDir -Recurse -Force -ErrorAction SilentlyContinue
if ($KbPath) {
  if (-not (Test-Path $KbPath)) { Fail "Knowledge base not found: $KbPath" }
  New-Item -ItemType Directory -Force $kbDir | Out-Null
  Copy-Item $KbPath $kbDir
  Write-Host "  knowledge base: $([math]::Round((Get-Item $KbPath).Length / 1GB, 2)) GB"
} else {
  Write-Host "  no knowledge base bundled (-KbPath); the literature panels will say none is installed" -ForegroundColor Yellow
}

# USDA FoodData Central foods (public domain) for meal logging
$fdcDir = Join-Path $Res "nutrition"
Remove-Item $fdcDir -Recurse -Force -ErrorAction SilentlyContinue
if ($FdcZip) {
  if (-not (Test-Path $FdcZip)) { Fail "FDC zip not found: $FdcZip" }
  New-Item -ItemType Directory -Force $fdcDir | Out-Null
  & $Py (Join-Path $Root "engine\nutrition\fdc_build.py") --zip $FdcZip --out (Join-Path $fdcDir "fdc_foods.db")
  if ($LASTEXITCODE) { Fail "fdc_build failed" }
} else {
  Write-Host "  no food database bundled (-FdcZip); meal search will find nothing" -ForegroundColor Yellow
}

# ------------------------------------------------------------------ notices
Step "Third-party notices"
$notices = Join-Path $Build "THIRD_PARTY_NOTICES.txt"
Copy-Item (Join-Path $PSScriptRoot "NOTICES_HEADER.txt") $notices
& $Py -m piplicenses --from=mixed --format=plain-vertical --with-license-file --no-license-path --ignore-packages pip-licenses pyinstaller pyinstaller-hooks-contrib altgraph pefile pywin32-ctypes setuptools pip | Out-File $notices -Append -Encoding utf8
foreach ($f in @("$llama\LICENSE.txt", (Get-ChildItem $tes -Filter "*LICENSE*" | Select-Object -First 1).FullName)) {
  if ($f -and (Test-Path $f)) { "`n==== $(Split-Path $f -Parent | Split-Path -Leaf)`n" | Out-File $notices -Append -Encoding utf8; Get-Content $f | Out-File $notices -Append -Encoding utf8 }
}

# ------------------------------------------------------------------ images
Step "Logos"
$iconArgs = @("--out", $Assets); if ($Icon) { $iconArgs += @("--icon", $Icon) }
& $Py (Join-Path $PSScriptRoot "make_assets.py") @iconArgs
if ($LASTEXITCODE) { Fail "make_assets failed" }

# ------------------------------------------------------------------ freeze
Step "PyInstaller"
$env:PAIH_RESOURCES = $Res; $env:PAIH_ICON = Join-Path $Assets "PAiHealth.ico"
& $Py -m PyInstaller (Join-Path $PSScriptRoot "paihealth.spec") --noconfirm --clean --distpath (Join-Path $Build "pyi") --workpath (Join-Path $Build "work")
if ($LASTEXITCODE) { Fail "PyInstaller failed" }
$exe = Join-Path $Build "pyi\PAiHealth\PAiHealth.exe"

Step "Self-test of the frozen app (no window)"
$selfHome = Join-Path $Build "selftest-home"; Remove-Item $selfHome -Recurse -Force -ErrorAction SilentlyContinue
$env:PAI_HEALTH_HOME = $selfHome
Start-Process $exe -ArgumentList "--selftest" -Wait -NoNewWindow
Remove-Item Env:\PAI_HEALTH_HOME
$st = Get-Content (Join-Path $selfHome "logs\selftest.json") -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
if (-not $st -or -not $st.ui) { Fail "Frozen app self-test failed (see $selfHome\logs)" }
$st | Format-List
if (-not $st.ocr) { Fail "OCR not available in the frozen build" }
if (-not $SkipLlama -and -not $st.llama_server) { Fail "llama-server not found by the frozen build" }

# ------------------------------------------------------------------ layout
Step "MSIX layout"
Remove-Item $Layout -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$Layout\Assets" | Out-Null
Copy-Item (Join-Path $Build "pyi\PAiHealth") $Layout -Recurse
Copy-Item "$Assets\*.png" "$Layout\Assets"
$manifest = (Get-Content (Join-Path $PSScriptRoot "AppxManifest.template.xml") -Raw).
  Replace("{{IDENTITY_NAME}}", $IdentityName).Replace("{{PUBLISHER}}", [Security.SecurityElement]::Escape($Publisher)).
  Replace("{{PUBLISHER_DISPLAY_NAME}}", [Security.SecurityElement]::Escape($PublisherDisplayName)).Replace("{{VERSION}}", $Version)
if ($manifest -match "\{\{[A-Z_]+\}\}") { Fail "Unfilled manifest placeholder: $($Matches[0])" }
[IO.File]::WriteAllText("$Layout\AppxManifest.xml", $manifest, (New-Object Text.UTF8Encoding $false))

# resources.pri so Windows picks the right logo scale (indexed from a small staging folder, not the whole app)
$pri = Join-Path $Build "pri"; Remove-Item $pri -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$pri\Assets" | Out-Null
Copy-Item "$Assets\*.png" "$pri\Assets"; Copy-Item "$Layout\AppxManifest.xml" $pri
& $MakePri createconfig /cf "$Build\priconfig.xml" /dq en-US /pv 10.0.0 /o | Out-Null
& $MakePri new /pr $pri /cf "$Build\priconfig.xml" /mn "$pri\AppxManifest.xml" /of "$Layout\resources.pri" /o | Out-Null
if ($LASTEXITCODE) { Fail "makepri failed" }

# ------------------------------------------------------------------ pack
Step "Packing"
New-Item -ItemType Directory -Force $Out | Out-Null
$msix = Join-Path $Out "PAiHealth_$($Version)_x64.msix"
$mk = & $MakeAppx pack /d $Layout /p $msix /o 2>&1
if ($LASTEXITCODE) { $mk | Select-String "error" | ForEach-Object { Write-Host $_.Line -ForegroundColor Red }; Fail "makeappx failed" }
Write-Host "  $msix  ($([math]::Round((Get-Item $msix).Length / 1MB)) MB)"

if ($Sign) {
  Step "Test-signing (local sideload only - the Store signs uploads itself)"
  $cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq $Publisher } | Select-Object -First 1
  if (-not $cert) {
    $cert = New-SelfSignedCertificate -Type Custom -Subject $Publisher -KeyUsage DigitalSignature -FriendlyName "PAi Health test" `
      -CertStoreLocation Cert:\CurrentUser\My -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
  }
  & $SignTool sign /fd SHA256 /sha1 $cert.Thumbprint $msix; if ($LASTEXITCODE) { Fail "signtool failed" }
  Write-Host "  To install the signed test package, trust the cert once (admin PowerShell):"
  Write-Host "  Export-Certificate -Cert Cert:\CurrentUser\My\$($cert.Thumbprint) -FilePath $Build\test.cer; Import-Certificate -FilePath $Build\test.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople"
}

if ($Register) {
  Step "Registering the layout for a local test (needs Developer Mode)"
  Get-AppxPackage -Name $IdentityName | Remove-AppxPackage -ErrorAction SilentlyContinue
  Add-AppxPackage -Register "$Layout\AppxManifest.xml"
  Write-Host "  Installed. Start menu > PAi Health."
}

if ($Wack) {
  Step "Windows App Certification Kit"
  $appcert = "${env:ProgramFiles(x86)}\Windows Kits\10\App Certification Kit\appcert.exe"
  if (-not (Test-Path $appcert)) { Fail "appcert.exe not found (install the Windows SDK with the App Certification Kit)" }
  $report = Join-Path $Out "wack-report.xml"
  & $appcert reset | Out-Null
  & $appcert test -appxpackagepath $msix -reportoutputpath $report
  Write-Host "  Report: $report"
}

Step "Done"
Write-Host "Upload to Partner Center: $msix"
