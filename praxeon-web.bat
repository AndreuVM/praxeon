@echo off
setlocal
REM Lanzador Unificado PRAXEON para Windows

REM 1. Priorizar el entorno virtual del proyecto
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%~dp0.venv\Scripts\python.exe"
    goto :RUN
)

REM 2. Comprobar Python Launcher de Windows (py.exe)
where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3"
    goto :RUN
)

REM 3. Comprobar python en PATH
where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
    goto :RUN
)

REM Si no se encuentra ningún ejecutable de Python
echo [ERROR] No se ha encontrado ningún ejecutable de Python.
echo Asegúrate de tener el entorno virtual (.venv) o tener Python instalado en el PATH de Windows.
pause
exit /b 1

:RUN
if exist "%~dp0run_praxeon.py" (
    "%PYTHON_CMD%" "%~dp0run_praxeon.py" --mode web %*
) else if exist "%~dp0.venv\Scripts\praxeon-web.exe" (
    "%~dp0.venv\Scripts\praxeon-web.exe" %*
) else (
    "%PYTHON_CMD%" -m praxeon.server.app %*
)
endlocal
