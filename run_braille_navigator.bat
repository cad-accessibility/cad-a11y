@echo off
REM Accessible Braille Array Navigator Launcher for Windows
REM Installs dependencies and runs the navigator script. No arguments required.

REM Upgrade pip (recommended)
pip install --upgrade pip


REM Install required dependencies
pip install numpy hidapi
if exist requirements.txt (
    pip install -r requirements.txt
)

REM Run the Python script
python navigate_braille_array.py

REM Pause so user can see any messages
pause
