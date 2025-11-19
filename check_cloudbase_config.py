#!/usr/bin/env python3
"""
CloudBase 配置检查脚本
用于验证环境变量和数据库配置是否正确
"""

import os
import sys
from urllib.parse import urlparse

def check_env_vars():
    """检查必需的环境变量"""
    print("=" * 60)
    print("🔍 检查环境变量配置")
    print("=" * 60)
    
    required_vars = {
        "DATABASE_URL": "数据库连接字符串（必需）",
        "DOUBAO_API_KEY": "豆包 API 密钥（必需）",
    }
    
    optional_vars = {
        "DOUBAO_API_BASE": "豆包 API 地址（可选）",
        "DEEPSEEK_API_KEY": "DeepSeek API 密钥（可选）",
        "VOLCENGINE_TTS_ACCESS_KEY": "火山引擎 AccessKey（可选）",
        "VOLCENGINE_TTS_SECRET_KEY": "火山引擎 SecretKey（可选）",
        "VOLCENGINE_TTS_APP_ID": "火山引擎 AppID（可选）",
    }
    
    all_ok = True
    
    # 检查必需变量
    print("\n📋 必需的环境变量：")
    for var, desc in required_vars.items():
        value = os.environ.get(var)
        if value:
            # 隐藏敏感信息
            if "KEY" in var or "SECRET" in var or "PASSWORD" in var:
                display_value = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
            else:
                display_value = value
            print(f"  ✅ {var}: {display_value}")
        else:
            print(f"  ❌ {var}: 未设置 - {desc}")
            all_ok = False
    
    # 检查可选变量
    print("\n📋 可选的环境变量：")
    for var, desc in optional_vars.items():
        value = os.environ.get(var)
        if value:
            if "KEY" in var or "SECRET" in var:
                display_value = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
            else:
                display_value = value
            print(f"  ✅ {var}: {display_value}")
        else:
            print(f"  ⚠️  {var}: 未设置 - {desc}")
    
    return all_ok

def check_database_url():
    """检查数据库连接字符串格式"""
    print("\n" + "=" * 60)
    print("🔍 检查数据库配置")
    print("=" * 60)
    
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL 未设置")
        return False
    
    try:
        parsed = urlparse(db_url)
        
        # 检查协议
        if not db_url.startswith(("mysql://", "mysql+pymysql://")):
            print(f"❌ 数据库协议错误：应该是 mysql:// 或 mysql+pymysql://")
            print(f"   当前值：{db_url[:50]}...")
            return False
        
        # 检查是否有用户名和密码
        if not parsed.username:
            print("❌ 数据库连接字符串缺少用户名")
            return False
        
        # 检查是否有主机和端口
        if not parsed.hostname:
            print("❌ 数据库连接字符串缺少主机地址")
            return False
        
        # 检查是否有数据库名
        if not parsed.path or parsed.path == "/":
            print("❌ 数据库连接字符串缺少数据库名")
            return False
        
        print(f"✅ 数据库连接字符串格式正确")
        print(f"   协议: {parsed.scheme}")
        print(f"   主机: {parsed.hostname}")
        print(f"   端口: {parsed.port or 3306}")
        print(f"   数据库: {parsed.path[1:]}")
        print(f"   用户: {parsed.username}")
        
        # 检查字符集
        if "charset=utf8mb4" in db_url:
            print(f"✅ 字符集配置正确 (utf8mb4)")
        else:
            print(f"⚠️  建议添加 charset=utf8mb4 到连接字符串")
        
        return True
        
    except Exception as e:
        print(f"❌ 解析数据库连接字符串失败: {e}")
        return False

def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("🚀 CloudBase 配置检查工具")
    print("=" * 60)
    print("\n提示：此脚本检查环境变量配置")
    print("在容器环境中运行时，会读取实际的环境变量\n")
    
    # 检查环境变量
    env_ok = check_env_vars()
    
    # 检查数据库配置
    db_ok = check_database_url()
    
    # 总结
    print("\n" + "=" * 60)
    print("📊 检查结果总结")
    print("=" * 60)
    
    if env_ok and db_ok:
        print("✅ 所有必需配置检查通过！")
        print("\n💡 下一步：")
        print("   1. 访问你的服务地址测试功能")
        print("   2. 在 CloudBase 控制台查看数据库表是否已创建")
        return 0
    else:
        print("❌ 部分配置缺失或错误")
        print("\n💡 请按照 CLOUDBASE_FULL_SETUP.md 文档配置：")
        if not env_ok:
            print("   - 配置必需的环境变量")
        if not db_ok:
            print("   - 检查 DATABASE_URL 格式")
        return 1

if __name__ == "__main__":
    sys.exit(main())

