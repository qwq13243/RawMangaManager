@echo off
chcp 65001 >nul
title 漫画下载器
cd /d "%~dp0"
python main.py
pause