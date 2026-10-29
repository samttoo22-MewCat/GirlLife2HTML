@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 build_girllife_html.py
) else (
  python build_girllife_html.py
)
if errorlevel 1 pause
