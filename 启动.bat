@echo off
chcp 65001 >nul
title SEES PRESS
rem 优先使用本机托管的 Python；其它机器直接回落到 PATH 里的 python
set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting SEES PRESS at http://127.0.0.1:8777 ...
"%PY%" "%~dp0server.py" --open
pause
