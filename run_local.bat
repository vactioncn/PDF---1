@echo off
REM Windows 本地运行脚本

echo 🚀 启动本地服务...

REM 检查 Python 环境
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

REM 检查虚拟环境
if not exist "venv" (
    echo 📦 创建虚拟环境...
    python -m venv venv
)

REM 激活虚拟环境
echo 🔧 激活虚拟环境...
call venv\Scripts\activate.bat

REM 安装依赖
echo 📥 安装依赖...
pip install -r requirements.txt

REM 检查 .env 文件
if not exist ".env" (
    echo ⚠️  未找到 .env 文件
    echo 📝 从 env.example 创建 .env 文件...
    if exist "env.example" (
        copy env.example .env
        echo ✅ 已创建 .env 文件，请编辑它并填入你的 API 密钥
        echo    特别是 DOUBAO_API_KEY（必需）
        pause
    ) else (
        echo ❌ 未找到 env.example 文件
        pause
        exit /b 1
    )
)

REM 启动服务
echo 🌟 启动 Flask 应用...
echo    访问地址: http://localhost:5000
echo    按 Ctrl+C 停止服务
echo.

python app.py

pause

