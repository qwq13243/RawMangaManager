@echo off
chcp 65001 >nul
title 漫画下载器 - 快速初始化

:: 自动寻找 Python
set "PY=python"
python --version >nul 2>&1 || set "PY=py"
%PY% --version >nul 2>&1 || (echo [错误] 未找到 Python & pause & exit /b 1)

echo [1/2] 配置镜像源并安装核心库...
set "PIP=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PW_HOST=https://npmmirror.com/mirrors/playwright/"

:: 安装依赖 (从 requirements.txt 读取)
if exist requirements.txt (
    echo 正在从 requirements.txt 安装依赖...
    %PY% -m pip install -r requirements.txt -i %PIP%
) else (
    echo [警告] 未找到 requirements.txt，将安装默认核心依赖...
    %PY% -m pip install playwright==1.48.0 PySide6 requests Pillow numpy opencv-python torch einops manga-ocr protobuf openai httpx -i %PIP%
)

if %errorlevel% neq 0 (echo [错误] 库安装失败 & pause & exit /b 1)

echo [2/2] 下载浏览器内核 (Chromium)...
set "PLAYWRIGHT_DOWNLOAD_HOST=%PW_HOST%"
%PY% -m playwright install chromium

if %errorlevel% equ 0 (
    echo.
    echo [成功] 初始化完成！可直接运行程序。
) else (
    echo.
    echo [失败] 浏览器下载失败，请检查网络后重试。
)
pause
