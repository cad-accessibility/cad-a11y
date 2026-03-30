param(
    [string]$EnvName = "cad-a11y",
    [switch]$UseCurrentPython,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Info($message) {
    Write-Host "[INFO] $message" -ForegroundColor Cyan
}

function Write-Ok($message) {
    Write-Host "[ OK ] $message" -ForegroundColor Green
}

if ($env:OS -ne "Windows_NT") {
    throw "This script must run on Windows."
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

Write-Info "Repository root: $repoRoot"

if ($Clean) {
    Write-Info "Cleaning previous PyInstaller artifacts..."
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
}

if ($UseCurrentPython) {
    Write-Info "Using current Python interpreter for build."
    py -m pip install --upgrade pyinstaller
    py -m PyInstaller --noconfirm --clean .\server_pyinstaller.spec
}
else {
    if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
        throw "conda is required unless -UseCurrentPython is set."
    }

    Write-Info "Installing/refreshing PyInstaller in conda env '$EnvName'..."
    conda run -n $EnvName python -m pip install --upgrade pyinstaller

    Write-Info "Building executable from server_pyinstaller.spec..."
    conda run -n $EnvName pyinstaller --noconfirm --clean .\server_pyinstaller.spec
}

$exePath = Join-Path $repoRoot "dist\cad-a11y-server\cad-a11y-server.exe"
if (-not (Test-Path $exePath)) {
    throw "Build finished but executable was not found at $exePath"
}

Write-Ok "Build complete."
Write-Host "Executable:" -ForegroundColor White
Write-Host "  $exePath" -ForegroundColor Green
Write-Host ""
Write-Host "Run it from PowerShell:" -ForegroundColor White
Write-Host "  & '$exePath'" -ForegroundColor Green
