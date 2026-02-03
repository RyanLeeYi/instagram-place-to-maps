@echo off
chcp 65001 >nul
title Instagram Place to Maps Bot
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "start.ps1"
pause
