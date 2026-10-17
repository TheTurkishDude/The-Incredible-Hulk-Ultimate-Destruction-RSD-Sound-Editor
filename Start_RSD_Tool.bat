@echo off
rem Fallback launcher if double-clicking RSD_Tool.pyw does nothing on your PC.
start "" pythonw "%~dp0RSD_Tool.pyw" %*
if errorlevel 1 start "" python "%~dp0RSD_Tool.pyw" %*
