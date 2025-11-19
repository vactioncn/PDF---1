# CloudBase MySQL 数据库配置指南

## 当前状态

✅ **代码已支持 MySQL**：应用已修改为同时支持 SQLite（本地开发）和 MySQL（生产环境）

## 配置步骤

### 1. 在 CloudBase 控制台创建 MySQL 数据库

1. 登录 CloudBase 控制台：https://tcb.cloud.tencent.com
2. 进入你的环境：`aireadbook001-7g3su3oo81a89e2e`
3. 进入 **数据库** → **MySQL** → **新建数据库**
4. 选择配置（建议最小配置即可）
5. 创建完成后，记录以下信息：
   - 数据库地址（Host）
   - 端口（Port，通常是 3306）
   - 数据库名（Database）
   - 用户名（Username）
   - 密码（Password）

### 2. 配置环境变量

在 CloudBase 控制台的 **云托管** → **pdf-service** → **环境变量** 中添加：

```
DATABASE_URL=mysql+pymysql://用户名:密码@数据库地址:端口/数据库名?charset=utf8mb4
```

示例：
```
DATABASE_URL=mysql+pymysql://root:your_password@10.0.0.1:3306/pdf_db?charset=utf8mb4
```

### 3. 重新部署服务

配置环境变量后，服务会自动重启，应用会：
- 自动检测到 MySQL 数据库
- 自动创建所需的表结构
- 开始使用云数据库存储数据

## 优势对比

### 之前（SQLite 本地）
- ❌ 数据存储在容器内，容器重启数据丢失
- ❌ 无法持久化
- ❌ 不支持多实例共享数据
- ❌ 性能有限

### 现在（MySQL 云数据库）
- ✅ 数据持久化，永不丢失
- ✅ 支持多容器实例共享数据
- ✅ 自动备份和恢复
- ✅ 更好的性能和并发支持
- ✅ 充分利用云服务价值

## 验证

部署完成后，访问应用并执行一些操作（如保存设置、创建记录），然后：
1. 在 CloudBase 控制台的数据库管理界面查看数据
2. 确认数据已正确存储

## 回退方案

如果暂时不想使用 MySQL，只需：
- 删除或注释掉 `DATABASE_URL` 环境变量
- 应用会自动回退到 SQLite（仅用于开发测试）

