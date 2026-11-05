@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -File "%~dp0scripts\windows-local-sync\Start-KahuaSyncFlags.ps1" %*
set "kahuaExitCode=%errorlevel%"
echo.
if not "%kahuaExitCode%"=="0" echo The run stopped. Review the message above before trying again.
pause
exit /b %kahuaExitCode%
