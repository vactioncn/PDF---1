# 📖 本地运行快速指南

## ⚡ 最快启动方式（3步）

### 1️⃣ 配置 API 密钥

```bash
# 复制配置模板
cp env.example .env

# 编辑 .env 文件，填入你的豆包 API 密钥
# DOUBAO_API_KEY=你的密钥
```

### 2️⃣ 运行启动脚本

**Mac/Linux：**
```bash
./run_local.sh
```

**Windows：**
```cmd
run_local.bat
```

### 3️⃣ 打开浏览器

访问：http://localhost:5000

---

## 📝 详细说明

查看 `LOCAL_RUN.md` 获取完整文档。

---

## ✅ 已配置完成

- ✅ 默认使用 SQLite 数据库（无需配置）
- ✅ 自动创建虚拟环境
- ✅ 自动安装依赖
- ✅ 支持热重载（修改代码自动生效）

---

## 🎯 就这么简单！

现在你可以在本地运行所有功能了，和云托管版本完全一样！

