@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%python.exe"

if not exist "%PYTHON%" (
  echo Python runtime not found at "%PYTHON%".
  echo Reinstall the app or use the installer-generated environment.
  pause
  exit /b 1
)

if not exist "%ROOT%server_cube_slider.py" (
  echo server_cube_slider.py was not found in "%ROOT%".
  pause
  exit /b 1
)

start "" "%ROOT%accessible-3d-viewer.html"
"%PYTHON%" "%ROOT%server_cube_slider.py"
