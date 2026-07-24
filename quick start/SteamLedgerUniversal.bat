@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ============================================================
REM  Yev Steam Trading Ledger - launcher
REM  Kladi etot .bat RYADOM s .py failom prilozheniya.
REM  Zapuskaet cherez "py -m streamlit", poetomu ne zavisit ot PATH.
REM ============================================================

REM --- 1. Opredelyaem imya faila prilozheniya ---
set "APP=app.py"
if not exist "%APP%" for %%f in (Ledger*.py) do set "APP=%%f"

if not exist "%APP%" (
    echo [ERROR] App file not found in this folder.
    echo Put this .bat next to app.py or Ledger*.py
    echo Current folder: %CD%
    pause
    exit /b 1
)

REM --- 2. Ishchem Python (snachala launcher "py", potom "python") ---
where py >nul 2>nul
if not errorlevel 1 goto have_py

where python >nul 2>nul
if not errorlevel 1 goto have_python

echo [ERROR] Python not found.
echo Install Python 3.12 or 3.13 from python.org
echo and CHECK the box "Add python.exe to PATH" during install.
pause
exit /b 1

:have_py
set "PY=py"
goto check_deps

:have_python
set "PY=python"
goto check_deps

REM --- 3. Proveryaem zavisimosti, stavim esli net ---
:check_deps
echo Using Python:
%PY% --version
echo.

%PY% -c "import streamlit" >nul 2>nul
if not errorlevel 1 goto run_app

echo [INFO] Streamlit not installed for this Python. Installing...
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install --upgrade streamlit pandas openpyxl
if errorlevel 1 (
    echo.
    echo [ERROR] Install failed. See messages above.
    echo Tip: if your Python is very new, try Python 3.12 or 3.13 instead.
    pause
    exit /b 1
)
echo.

REM --- 4. Zapusk ---
:run_app
echo Starting: %APP%
echo Browser will open automatically. Close this window to stop the app.
echo.
%PY% -m streamlit run "%APP%"

if errorlevel 1 (
    echo.
    echo [ERROR] App failed to start. See messages above.
)
pause