@echo off
chcp 65001 >nul
title 剪映流水线 - 网站启动器
color 0B

echo ==================================================
echo          正在启动 剪映流水线 网站...
echo ==================================================
echo.

:: 1. 检查 app.py 是否存在
if not exist "app.py" (
    echo [❌ 错误] 找不到 app.py 文件！
    echo 请确保这个 bat 文件和 app.py 放在同一个文件夹里。
    pause
    exit
)

:: 2. 启动网站
echo [💡 提示] 浏览器即将自动打开...
echo [⚠️ 注意] 运行期间请勿关闭此黑色窗口，关闭即停止网站！
echo --------------------------------------------------
python -m streamlit run app.py

:: 3. 如果意外退出，暂停查看原因
echo.
echo [ℹ️] 网站已停止运行。
pause