@echo off
title Zeno Radar OaaS
echo ========================================================
echo  Iniciando Zeno Radar Desktop (Motor Python + Interface)
echo ========================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" src\desktop\tray_app.py
) else (
    python src\desktop\tray_app.py
)
pause
