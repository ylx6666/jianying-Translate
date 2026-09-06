@echo off
chcp 65001 >nul
title 剪映全能翻译台 - 自动清理并启动
color 0A

echo ============================================================
echo   [1/4] 🧹 正在清理后台卡死的 Python/Streamlit 进程...
echo ============================================================
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im pythonw.exe >nul 2>&1
echo      ✅ 清理完毕！端口已释放。
echo.

echo ============================================================
echo   [2/4] 📦 检查依赖环境...
echo ============================================================
if exist "%~dp0libs\streamlit" (
    echo      ✅ 检测到自带依赖包，离线模式。
    set "PYTHONPATH=%~dp0libs"
) else (
    echo      ⏳ 未检测到自带包，正在联网安装到 libs/ ...
    python -m pip install --target "%~dp0libs" streamlit openai pyJianYingDraft
    set "PYTHONPATH=%~dp0libs"
)
echo.

echo ============================================================
echo   [3/4] ⚙️ 写入配置文件，强制关闭开发模式并锁定 8501 端口...
echo ============================================================
if not exist "%~dp0.streamlit" mkdir "%~dp0.streamlit"
(
echo [global]
echo developmentMode = false
echo.
echo [server]
echo port = 8501
echo headless = true
echo.
echo [browser]
echo gatherUsageStats = false
) > "%~dp0.streamlit\config.toml"
echo      ✅ 已关闭开发模式，端口死死钉在 8501。
echo.

echo ============================================================
echo   [4/4] 🚀 启动网站...
echo ============================================================
if not exist "%~dp0app.py" (
    echo [❌] 找不到 app.py！请把这个 bat 放进项目文件夹。
    pause
    exit
)
echo [🚀] 正在启动网站，请勿关闭此窗口...
python -m streamlit run "%~dp0app.py"
pause