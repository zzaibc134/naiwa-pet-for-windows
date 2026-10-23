@echo off
chcp 65001 >nul
cd /d %~dp0

rem 一键体验：建虚拟环境 -> 装依赖 -> 生成原创占位角色 -> 启动桌宠
rem 面向不懂命令行的朋友；懂命令行的看 README「三分钟跑起来」

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没找到 Python。先到 https://www.python.org/downloads/ 安装 3.10+，
    echo        安装时勾选 "Add python.exe to PATH"，再重新双击本文件。
    pause
    exit /b 1
)

if not exist .venv (
    echo [1/3] 创建虚拟环境（首次约 1 分钟）...
    python -m venv .venv
) else (
    echo [1/3] 虚拟环境已存在
)

echo [2/3] 安装依赖（下载 PySide6 约 100 多 MB，耐心等）...
.venv\Scripts\python.exe -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 依赖安装失败，检查网络后重试
    pause
    exit /b 1
)

if not exist assets\pet\idle.webp (
    echo 首次运行：生成原创占位角色「蛋蛋」...
    .venv\Scripts\python.exe tools\make_placeholder.py
)

echo [3/3] 启动桌宠！左键点它会大笑，右键打开菜单。
start "" .venv\Scripts\pythonw.exe src\main.py
pause
