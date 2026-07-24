@echo off
chcp 65001 > nul
title Yev Steam Trading Ledger

:: ============================================================
::  Обычный запуск (консоль видна, логи Streamlit в окне)
::  Streamlit вызывается через "py -m", поэтому PATH не нужен.
:: ============================================================

set "APPDIR=C:\Users\path\to\file\location"
set "APP=ledger.py"

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

:: --- Запуск ---
:run_app
echo Python:
%PY% --version
echo Запускаю %APP% ... Чтобы остановить приложение, закрой это окно.
echo.
%PY% -m streamlit run "%APP%"

if errorlevel 1 (
    echo.
    echo [ОШИБКА] Приложение завершилось с ошибкой, смотри сообщения выше.
)
pause