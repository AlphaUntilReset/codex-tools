@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\control-wallpaper.ps1" -Action native %*
if not errorlevel 1 exit /b 0
start "" explorer.exe "shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App"
