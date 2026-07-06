@echo off
setlocal
powershell.exe -ExecutionPolicy Bypass -File "%~dp0start-docs.ps1" %*
