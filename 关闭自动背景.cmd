@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\control-wallpaper.ps1" -Action disable %*
