param(
    [switch]$SkipCadTools,
    [switch]$SkipExtras,
    [string]$EnvName = "cad-a11y",
    [string]$ServerScript = "server_cube_slider.py"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Info($message) {
    Write-Host "[INFO] $message" -ForegroundColor Cyan
}

function Write-WarnMsg($message) {
    Write-Host "[WARN] $message" -ForegroundColor Yellow
}

function Write-Ok($message) {
    Write-Host "[ OK ] $message" -ForegroundColor Green
}

function Command-Exists([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-Winget {
    if (-not (Command-Exists "winget")) {
        throw "winget is not installed. Install App Installer from Microsoft Store, then re-run this script."
    }
}

function Add-CondaPaths {
    $candidateRoots = @(
        "$env:USERPROFILE\miniconda3",
        "$env:USERPROFILE\Miniconda3",
        "$env:USERPROFILE\anaconda3",
        "$env:LOCALAPPDATA\miniconda3"
    )

    foreach ($root in $candidateRoots) {
        $scripts = Join-Path $root "Scripts"
        $condabin = Join-Path $root "condabin"
        if (Test-Path (Join-Path $scripts "conda.exe")) {
            $env:Path = "$scripts;$condabin;$root;$env:Path"
            return
        }
    }
}

function Ensure-Conda {
    if (Command-Exists "conda") {
        Write-Ok "Conda is already available."
        return
    }

    Write-Info "Conda not found. Installing Miniconda (user scope) via winget..."
    Ensure-Winget

    winget install -e --id Anaconda.Miniconda3 --scope user --accept-package-agreements --accept-source-agreements

    Add-CondaPaths

    if (-not (Command-Exists "conda")) {
        throw "Miniconda install completed but 'conda' is still not on PATH. Open a new PowerShell and re-run this script."
    }

    Write-Ok "Miniconda installed and conda is available."
}

function Ensure-CondaEnv {
    param(
        [string]$EnvName,
        [string]$RepoRoot
    )

    $envFile = Join-Path $RepoRoot "environment.yml"
    if (-not (Test-Path $envFile)) {
        throw "environment.yml not found at $envFile"
    }

    Write-Info "Creating/updating Conda environment '$EnvName' from environment.yml..."

    conda env list | Out-Null

    $envExists = (conda env list | Select-String -Pattern "^\s*$([regex]::Escape($EnvName))\s").Length -gt 0

    if ($envExists) {
        conda env update -n $EnvName -f $envFile --prune -y
    }
    else {
        conda env create -n $EnvName -f $envFile -y
    }

    Write-Ok "Conda environment '$EnvName' is ready."
}

function Install-ExtraPythonDeps {
    param([string]$EnvName)

    Write-Info "Installing extra codebase dependencies not listed in requirements/environment manifests..."

    # Needed by converter modules across src/converter:
    # scipy, networkx, cv2(opencv), PyPDF2, reportlab
    conda install -n $EnvName -c conda-forge -y scipy networkx opencv pypdf2 reportlab

    Write-Ok "Extra dependencies installed."
}

function Try-InstallCadTool {
    param(
        [string]$WingetId,
        [string]$DisplayName,
        [string]$CommandName
    )

    if (Command-Exists $CommandName) {
        Write-Ok "$DisplayName is already available ($CommandName found)."
        return
    }

    if (-not (Command-Exists "winget")) {
        Write-WarnMsg "winget not available, skipping $DisplayName install."
        return
    }

    Write-Info "Installing $DisplayName via winget (optional tool for SCAD conversion scripts)..."
    try {
        winget install -e --id $WingetId --accept-package-agreements --accept-source-agreements
        if (Command-Exists $CommandName) {
            Write-Ok "$DisplayName installed successfully."
        }
        else {
            Write-WarnMsg "$DisplayName installation attempted, but command '$CommandName' is not on PATH yet. A reboot/log out may be required."
        }
    }
    catch {
        Write-WarnMsg "Could not install $DisplayName automatically: $($_.Exception.Message)"
    }
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

Write-Info "Repository root: $repoRoot"

Ensure-Conda
Ensure-CondaEnv -EnvName $EnvName -RepoRoot $repoRoot

if (-not $SkipExtras) {
    Install-ExtraPythonDeps -EnvName $EnvName
}
else {
    Write-WarnMsg "Skipping extra Python dependencies by request (--SkipExtras)."
}

if (-not $SkipCadTools) {
    Try-InstallCadTool -WingetId "OpenSCAD.OpenSCAD" -DisplayName "OpenSCAD" -CommandName "openscad"
    Try-InstallCadTool -WingetId "FreeCAD.FreeCAD" -DisplayName "FreeCAD" -CommandName "freecad"
}
else {
    Write-WarnMsg "Skipping OpenSCAD/FreeCAD install by request (--SkipCadTools)."
}

Write-Host ""
Write-Ok "Setup complete."
Write-Host ""

$serverPath = Join-Path $repoRoot $ServerScript
if (-not (Test-Path $serverPath)) {
    Write-WarnMsg "Server script '$ServerScript' was not found at repo root. Falling back to 'server.py'."
    $ServerScript = "server.py"
}

Write-Host "Run the server with:" -ForegroundColor White
Write-Host "  conda run -n $EnvName python $ServerScript" -ForegroundColor Green
Write-Host ""
Write-Host "Then open:" -ForegroundColor White
Write-Host "  accessible-3d-viewer.html" -ForegroundColor Green
Write-Host ""
Write-Host "Optional flags:" -ForegroundColor White
Write-Host "  .\setup_windows.ps1 -SkipCadTools" -ForegroundColor Gray
Write-Host "  .\setup_windows.ps1 -SkipExtras" -ForegroundColor Gray
Write-Host "  .\setup_windows.ps1 -ServerScript server.py" -ForegroundColor Gray
