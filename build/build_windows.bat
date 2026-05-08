@echo off
REM ============================================================
REM  Krystal Data Vision — one-click Windows build
REM  Run by double-clicking from Explorer, or from cmd:
REM      build\build_windows.bat
REM  Requires Python 3.11+ already installed (developer machine).
REM  End-users do NOT need Python; they just get dist\kdv.zip.
REM ============================================================

setlocal ENABLEDELAYEDEXPANSION
cd /d "%~dp0\.."

echo [1/5] Creating virtual environment if missing...
if not exist .venv (
    python -m venv .venv || goto :error
)

echo [2/5] Activating venv and installing dependencies...
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip >nul
python -m pip install -e ".[dev]" || goto :error
python -m pip install pyinstaller >nul

echo [3/5] Running tests...
python -m pytest -q || goto :error

echo [4/5] Building exe with PyInstaller...
pyinstaller build\kdv.spec --clean --noconfirm || goto :error

echo [5/5] Zipping dist\kdv into dist\kdv.zip for distribution...
powershell -NoProfile -Command "Compress-Archive -Path dist\kdv\* -DestinationPath dist\kdv.zip -Force" || goto :error

echo.
echo =====================================================
echo  Build success!
echo  - Run locally :  dist\kdv\kdv.exe   (double-click)
echo  - Distribute  :  dist\kdv.zip       (send to users)
echo =====================================================
echo.
pause
exit /b 0

:error
echo.
echo *** Build failed. See log above. ***
pause
exit /b 1
