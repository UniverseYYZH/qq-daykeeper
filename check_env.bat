@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
    py -3 tools\check_env.py %*
    goto done
)

where python >nul 2>nul
if not errorlevel 1 (
    python tools\check_env.py %*
    goto done
)

echo Python 3.10+ not found. Please install it with tcl/tk support.
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:done
echo.
pause
