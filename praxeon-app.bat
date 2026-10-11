@echo off
setlocal
REM Lanzador de la aplicacion de escritorio PRAXEON (servidor local + ventana nativa)

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m praxeon.cli app %*
    goto :END
)

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 -m praxeon.cli app %*
    goto :END
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    python -m praxeon.cli app %*
    goto :END
)

echo [ERROR] No se ha encontrado ningun ejecutable de Python.
pause
exit /b 1

:END
endlocal
