@echo off
chcp 65001 > nul
title Yev Steam Trading Ledger

:: ============================================================
::  Запуск с открытием в Chrome
::  Streamlit вызывается через "py -m", поэтому PATH не нужен.
::  Сервер стартует без своего окна браузера, ссылка открывается
::  вручную - но только ПОСЛЕ того, как сервер реально поднялся.
:: ============================================================

set "APPDIR=C:\Users\path\to\file\location"
set "APP=ledger.py"
set "PORT=8501"
set "URL=http://localhost:8501"

:: --- Папка проекта: сначала прописанный путь, иначе папка этого .bat ---
if exist "%APPDIR%\%APP%" (
    cd /d "%APPDIR%"
) else (
    cd /d "%~dp0"
)

if not exist "%APP%" (
    echo [ОШИБКА] Не найден файл %APP%
    echo Поправь путь в строке APPDIR внутри этого .bat
    echo Текущая папка: %CD%
    echo.
    pause
    exit /b 1
)

:: --- Ищем Python: сначала лаунчер "py", потом "python" ---
where py >nul 2>nul
if not errorlevel 1 goto have_py

where python >nul 2>nul
if not errorlevel 1 goto have_python

echo [ОШИБКА] Python не найден.
echo Установи Python с python.org и отметь галочку "Add python.exe to PATH".
echo.
pause
exit /b 1

:have_py
set "PY=py"
goto check_deps

:have_python
set "PY=python"
goto check_deps

:: --- Проверяем зависимости, доустанавливаем при необходимости ---
:check_deps
%PY% -c "import streamlit, pandas" >nul 2>nul
if not errorlevel 1 goto run_app

echo [ИНФО] Для этой версии Python не установлены streamlit/pandas. Устанавливаю...
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install --upgrade streamlit pandas openpyxl
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Установка не удалась, смотри сообщения выше.
    echo Если Python очень свежий - попробуй версию 3.12 или 3.13.
    echo.
    pause
    exit /b 1
)
echo.

:: --- Запуск сервера без автооткрытия браузера ---
:run_app
echo Python:
%PY% --version
echo Запускаю %APP% на порту %PORT% ... Чтобы остановить приложение, закрой это окно.
echo.
start "" /b %PY% -m streamlit run "%APP%" --server.headless true --server.port %PORT%

:: --- Ждём, пока сервер реально ответит (вместо слепых фикс секунд) ---
set "READY="
where curl.exe >nul 2>nul
if errorlevel 1 goto plain_wait

for /l %%i in (1,1,30) do (
    if not defined READY (
        curl.exe -s -o nul --max-time 2 "%URL%" && set "READY=1"
        if not defined READY timeout /t 1 /nobreak > nul
    )
)
if not defined READY echo [ИНФО] Сервер не ответил за 30 секунд, открываю ссылку всё равно.
goto open_browser

:plain_wait
timeout /t 5 /nobreak > nul

:: --- Открываем в Chrome; если его нет - в браузере по умолчанию ---
:open_browser
set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if defined CHROME (
    start "" "%CHROME%" "%URL%"
) else (
    echo [ИНФО] Chrome не найден - открываю в браузере по умолчанию.
    start "" "%URL%"
)