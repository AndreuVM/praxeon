@echo off
REM Lanzador PRAXEON Web Application desde el directorio web
if exist "%~dp0..\.venv\Scripts\praxeon-web.exe" (
    "%~dp0..\.venv\Scripts\praxeon-web.exe" %*
) else (
    python -m praxeon.server.app %*
)
