@echo off
setlocal

if "%PREFIX%"=="" exit /b 0

set "PYTHON_EXE=%PREFIX%\python.exe"
if not exist "%PYTHON_EXE%" exit /b 0

set "INSTALL_LOG=%PREFIX%\cad_a11y_post_install.log"
echo [INFO] CAD A11y post-install started > "%INSTALL_LOG%"
echo [INFO] PREFIX=%PREFIX% >> "%INSTALL_LOG%"

set "TARGET=%PREFIX%\launch_cad_a11y.bat"
if not exist "%TARGET%" (
	echo [ERROR] Launcher not found at "%TARGET%" >> "%INSTALL_LOG%"
	exit /b 0
)

set "DESKTOP=%USERPROFILE%\Desktop\CAD A11y Viewer.lnk"
set "PROGRAMS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\CAD A11y Viewer.lnk"

REM Install pip-only runtime dependencies that may be unavailable via conda.
"%PYTHON_EXE%" -m pip install --disable-pip-version-check --no-input godice >nul 2>&1
if %ERRORLEVEL% EQU 0 (
	echo [OK] Installed Python package: godice >> "%INSTALL_LOG%"
) else (
	echo [WARN] Failed to install Python package: godice >> "%INSTALL_LOG%"
)

REM Try to install optional CAD desktop tools, matching setup_windows.ps1 behavior.
where winget >nul 2>&1
if %ERRORLEVEL% EQU 0 (
	winget install -e --id OpenSCAD.OpenSCAD --scope user --accept-package-agreements --accept-source-agreements >nul 2>&1
	if %ERRORLEVEL% EQU 0 (
		echo [OK] OpenSCAD install command succeeded. >> "%INSTALL_LOG%"
	) else (
		echo [WARN] OpenSCAD install command failed (or already installed). >> "%INSTALL_LOG%"
	)

	winget install -e --id FreeCAD.FreeCAD --scope user --accept-package-agreements --accept-source-agreements >nul 2>&1
	if %ERRORLEVEL% EQU 0 (
		echo [OK] FreeCAD install command succeeded. >> "%INSTALL_LOG%"
	) else (
		echo [WARN] FreeCAD install command failed (or already installed). >> "%INSTALL_LOG%"
	)
) else (
	echo [WARN] winget not found; skipping OpenSCAD/FreeCAD installation. >> "%INSTALL_LOG%"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($env:DESKTOP); $s.TargetPath = $env:TARGET; $s.WorkingDirectory = $env:PREFIX; $s.IconLocation = $env:SystemRoot + '\\System32\\shell32.dll,13'; $s.Save()" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($env:PROGRAMS); $s.TargetPath = $env:TARGET; $s.WorkingDirectory = $env:PREFIX; $s.IconLocation = $env:SystemRoot + '\\System32\\shell32.dll,13'; $s.Save()" >nul 2>&1

if exist "%DESKTOP%" (
	echo [OK] Desktop shortcut created: "%DESKTOP%" >> "%INSTALL_LOG%"
) else (
	echo [WARN] Desktop shortcut not found after creation attempt. >> "%INSTALL_LOG%"
)

if exist "%PROGRAMS%" (
	echo [OK] Start Menu shortcut created: "%PROGRAMS%" >> "%INSTALL_LOG%"
) else (
	echo [WARN] Start Menu shortcut not found after creation attempt. >> "%INSTALL_LOG%"
)

echo [INFO] CAD A11y post-install finished >> "%INSTALL_LOG%"

exit /b 0
