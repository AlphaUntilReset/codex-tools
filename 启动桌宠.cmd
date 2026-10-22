@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start.ps1" -Mode pet
if errorlevel 1 pause
