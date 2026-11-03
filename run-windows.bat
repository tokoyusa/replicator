@echo off
setlocal
cd /d "%~dp0"
if exist "Loopizz.exe" (
  start "" "Loopizz.exe"
  exit /b 0
)
if exist "python\python.exe" (
  set "PATH=%~dp0bin;%~dp0python;%PATH%"
  set "QT_PLUGIN_PATH=%~dp0python\Lib\site-packages\PySide6\plugins"
  "python\python.exe" "launcher.py"
) else (
  echo Loopizz.exe or embedded Python was not found.
  pause
)
