@echo off
REM ============================================================
REM  Build MediaHotKey into a single standalone Windows .exe
REM  Requires: pip install -r requirements.txt pyinstaller
REM  Output:   dist\MediaHotKey.exe
REM ============================================================

echo Installing build dependencies...
python -m pip install -r requirements.txt pyinstaller

echo Building MediaHotKey.exe ...
pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name MediaHotKey ^
  --icon assets\icon.ico ^
  --add-data "mediahotkey\web;mediahotkey\web" ^
  --add-data "assets\icon.ico;assets" ^
  --collect-all webview ^
  --collect-all winsdk ^
  run.py

echo.
echo Done. Find it at: dist\MediaHotKey.exe
pause
