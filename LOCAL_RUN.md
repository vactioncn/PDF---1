# 🖥️ 本地运行指南

## 快速开始（3步）

### 第一步：安装 Python

确保你的电脑已安装 Python 3.8 或更高版本：

```bash
python3 --version
```

如果没有安装，去 https://www.python.org/downloads/ 下载安装。

### 第二步：配置 API 密钥

1. 复制配置文件：
   ```bash
   cp env.example .env
   ```

2. 编辑 `.env` 文件，填入你的豆包 API 密钥：
   ```
   DOUBAO_API_KEY=你的豆包API密钥
   ```

   **如何获取豆包 API 密钥？**
   - 访问：https://console.volcengine.com/ark
   - 注册/登录账号
   - 创建应用并获取 API Key

### 第三步：启动服务

**Mac/Linux：**
```bash
chmod +x run_local.sh
./run_local.sh
```

**Windows：**
```cmd
run_local.bat
```

**或者手动启动：**
```bash
# 创建虚拟环境（首次运行）
python3 -m venv venv

# 激活虚拟环境
# Mac/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动服务
python app.py
```

### 访问应用

启动成功后，打开浏览器访问：
- **首页**：http://localhost:5000
- **API 文档**：http://localhost:5000/api_documentation.html
- **管理页面**：http://localhost:5000/admin.html

---

## 📋 详细说明

### 环境变量配置

`.env` 文件中的配置项：

| 变量名 | 必需 | 说明 |
|--------|------|------|
| `DOUBAO_API_KEY` | ✅ 是 | 豆包 API 密钥（核心功能必需） |
| `DOUBAO_API_BASE` | ❌ 否 | 豆包 API 地址（有默认值） |
| `DEEPSEEK_API_KEY` | ❌ 否 | DeepSeek API 密钥（可选） |
| `DATABASE_URL` | ❌ 否 | 数据库连接（不配置则使用本地 SQLite） |
| `PORT` | ❌ 否 | 服务端口（默认 5000） |

### 数据库

**默认使用 SQLite**：
- 数据存储在项目目录下的 `app.db` 文件
- 无需额外配置，开箱即用
- 适合本地开发和测试

**如果想使用 MySQL**：
1. 安装并启动 MySQL
2. 在 `.env` 中配置：
   ```
   DATABASE_URL=mysql+pymysql://root:password@localhost:3306/pdf_app?charset=utf8mb4
   ```

### 常见问题

#### 问题 1：找不到 python3 命令
- **Mac/Linux**：使用 `python3`
- **Windows**：使用 `python`
- 或者检查 Python 是否已添加到系统 PATH

#### 问题 2：pip 安装依赖失败
```bash
# 升级 pip
pip install --upgrade pip

# 使用国内镜像（如果网络慢）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

#### 问题 3：端口被占用
如果 5000 端口被占用，修改 `.env` 文件：
```
PORT=5001
```
然后访问 http://localhost:5001

#### 问题 4：API 调用失败
- 检查 `DOUBAO_API_KEY` 是否正确
- 检查网络连接
- 查看终端中的错误信息

#### 问题 5：数据库错误
- 如果使用 SQLite，确保项目目录有写入权限
- 如果使用 MySQL，确保 MySQL 服务已启动

### 停止服务

在运行服务的终端窗口按 `Ctrl + C` 即可停止。

---

## 🎯 功能说明

本地运行后，你可以：

1. **解析 PDF/EPUB**：上传文件，提取目录和内容
2. **生成解读**：根据章节内容生成个性化解读
3. **管理书籍**：查看、管理已上传的书籍
4. **配置提示词**：自定义生成内容的提示词

所有功能与云托管版本完全一致！

---

## 📝 开发模式

启动时默认开启调试模式（`debug=True`），修改代码后会自动重载。

如果想关闭调试模式，编辑 `app.py` 最后一行：
```python
app.run(host="0.0.0.0", port=port, debug=False)
```

---

## 🔒 安全提示

- `.env` 文件包含敏感信息，**不要**提交到 Git
- 如果使用 Git，确保 `.env` 在 `.gitignore` 中
- 不要分享你的 API 密钥给他人

---

## ❓ 需要帮助？

如果遇到问题：
1. 查看终端中的错误信息
2. 检查 `.env` 配置是否正确
3. 确认所有依赖已安装
4. 告诉我具体的错误信息，我会继续帮你解决

