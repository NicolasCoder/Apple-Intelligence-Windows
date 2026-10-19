@echo off
chcp 65001 >nul
title Instalador - Apple Intelligence Writing Tools
echo ==================================================================
echo   Apple Intelligence Writing Tools - Instalacao Automatica
echo ==================================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado no PATH! Instale o Python 3.10+ em python.org MARCANDO "Add Python to PATH".
    pause
    exit /b 1
)

python "%~dp0install.py"
pause

