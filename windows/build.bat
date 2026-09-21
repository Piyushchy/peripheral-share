@echo off
REM Build MouseShare.exe (one file, no console window).
REM Needs Python 3.9+ ; installs PyInstaller the first time.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :fail
python -m PyInstaller --noconfirm --clean MouseShare.spec || goto :fail
echo.
echo Built: %~dp0dist\MouseShare.exe
pause
exit /b 0
:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
