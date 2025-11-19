#!/bin/bash
# 本地运行脚本

echo "🚀 启动本地服务..."

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.8+"
    exit 1
fi

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
echo "🔧 激活虚拟环境..."
source venv/bin/activate

# 安装依赖
echo "📥 安装依赖..."
pip install -r requirements.txt

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "⚠️  未找到 .env 文件"
    echo "📝 从 env.example 创建 .env 文件..."
    if [ -f "env.example" ]; then
        cp env.example .env
        echo "✅ 已创建 .env 文件，请编辑它并填入你的 API 密钥"
        echo "   特别是 DOUBAO_API_KEY（必需）"
        read -p "按回车继续..."
    else
        echo "❌ 未找到 env.example 文件"
        exit 1
    fi
fi

# 启动服务
echo "🌟 启动 Flask 应用..."
echo "   访问地址: http://localhost:5000"
echo "   按 Ctrl+C 停止服务"
echo ""

python3 app.py

