@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo   桌宠打包脚本 (Windows)
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没找到 python，请先安装 Python 3.10+ 并勾选 Add to PATH
    pause
    exit /b 1
)

if not exist assets\pet\idle.webp (
    echo [错误] 素材不存在：assets\pet\idle.webp
    echo 请先按 README 说明运行素材处理脚本
    pause
    exit /b 1
)

echo [1/2] 安装依赖（仅首次较慢）...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络
    pause
    exit /b 1
)

echo [2/2] 打包中（约 1-3 分钟）...
rem 图标可选：assets\app.ico 不在了就用 PyInstaller 默认图标
rem （占位角色生成器会自动造一个原创图标；真角色图标因版权不入库）
set ICONARG=
if exist assets\app.ico set ICONARG=--icon assets\app.ico
python -m PyInstaller --noconfirm --clean ^
  --name nailong-pet ^
  --windowed ^
  %ICONARG% ^
  --add-data "assets\pet:assets/pet" ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQuickControls2 ^
  --exclude-module PySide6.QtQuickWidgets ^
  --exclude-module PySide6.QtQuick3D ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebChannel ^
  --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtPdfWidgets ^
  --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtDataVisualization ^
  --exclude-module PySide6.QtSql ^
  --exclude-module PySide6.QtTest ^
  --exclude-module PySide6.QtBluetooth ^
  --exclude-module PySide6.QtNfc ^
  --exclude-module PySide6.QtSensors ^
  --exclude-module PySide6.QtSerialPort ^
  --exclude-module PySide6.QtPositioning ^
  --exclude-module PySide6.QtLocation ^
  --exclude-module PySide6.QtWebSockets ^
  --exclude-module PySide6.QtNetworkAuth ^
  --exclude-module PySide6.QtMultimediaWidgets ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.Qt3DRender ^
  --exclude-module PySide6.QtOpenGL ^
  --exclude-module PySide6.QtOpenGLWidgets ^
  --exclude-module PySide6.QtSpatialAudio ^
  src\main.py
if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo 清理无用 Qt 库以减小体积...
rem PyInstaller 6 把 DLL 放在 _internal\PySide6 下（老版本才是 PySide6\Qt\bin），
rem 路径写错的话这段会静默失效，所以下面每个删除都跟着体积报告。
set PSDIR=dist\nailong-pet\_internal\PySide6
for %%D in (opengl32sw.dll Qt6Quick.dll Qt6Qml.dll Qt6QmlModels.dll Qt6QmlMeta.dll Qt6QmlWorkerScript.dll Qt6QmlCompiler.dll Qt6VirtualKeyboard.dll Qt6Pdf.dll Qt6Charts.dll Qt6DataVisualization.dll Qt6WebEngineCore.dll Qt6WebEngineWidgets.dll Qt6WebChannel.dll Qt6Quick3D.dll Qt6QuickControls2.dll Qt6QuickWidgets.dll) do (
    if exist "%PSDIR%\%%D" del /q "%PSDIR%\%%D"
)
rem 程序没有多语言界面，翻译文件可以整目录删（约 7 MB）
if exist "%PSDIR%\translations" rmdir /s /q "%PSDIR%\translations"
rem 图片插件只留 webp（素材是 webp）和 ico
for %%D in (qgif.dll qjpeg.dll qsvg.dll qtga.dll qtiff.dll qwbmp.dll qicns.dll qpdf.dll) do (
    if exist "%PSDIR%\plugins\imageformats\%%D" del /q "%PSDIR%\plugins\imageformats\%%D"
)

echo.
echo 完成！可执行文件在 dist\nailong-pet\nailong-pet.exe
echo 把整个 dist\nailong-pet 文件夹压缩发给朋友即可
pause
