#!/usr/bin/env python3
"""
火山引擎TTS API诊断脚本
尝试多种参数组合，找出正确的配置
"""
import os
import json
from dotenv import load_dotenv

load_dotenv()

print("=" * 60)
print("火山引擎TTS API诊断报告")
print("=" * 60)

# 检查配置
access_key = os.getenv("VOLCENGINE_TTS_ACCESS_KEY")
secret_key = os.getenv("VOLCENGINE_TTS_SECRET_KEY")
app_id = os.getenv("VOLCENGINE_TTS_APP_ID")

print("\n【配置检查】")
print(f"Access Key: {'已配置' if access_key else '❌ 未配置'} ({access_key[:10] + '...' if access_key else 'N/A'})")
print(f"Secret Key: {'已配置' if secret_key else '❌ 未配置'} ({secret_key[:10] + '...' if secret_key else 'N/A'})")
print(f"App ID: {'已配置' if app_id else '❌ 未配置'} ({app_id if app_id else 'N/A'})")

print("\n【V1 HTTP API 问题分析】")
print("错误: 'Unsupported operation: synthesis'")
print("\n可能的原因:")
print("1. operation值不正确 - 可能需要尝试:")
print("   - 'tts'")
print("   - 'text_to_speech'")
print("   - 'synthesize'")
print("   - 或其他值")
print("2. API版本不匹配 - 可能需要使用不同的端点")
print("3. 请求格式不正确 - 可能需要调整JSON结构")

print("\n【V3 WebSocket API 问题分析】")
print("错误: '[resource_id=volc.seedtts.default] requested resource not granted'")
print("\n关键发现:")
print("- 我们传入的resource_id: seed-tts-2.0")
print("- API转换后的resource_id: volc.seedtts.default")
print("- 说明: API将我们的resource_id映射到了默认值，但该资源未授权")

print("\n【需要确认的信息】")
print("1. 在火山引擎控制台「应用管理」中:")
print("   - 查看您的应用是否有TTS服务权限")
print("   - 查看正确的resource_id值（可能是 volc.service_type.10029 格式）")
print("   - 确认服务是否已开通并激活")

print("\n2. 可能的resource_id格式:")
print("   - seed-tts")
print("   - seed-tts-2.0")
print("   - volc.seedtts.default")
print("   - volc.service_type.10029")
print("   - 或其他控制台显示的值")

print("\n3. V1 API的operation参数:")
print("   - 请查看火山引擎TTS API文档，确认正确的operation/action值")
print("   - 可能需要使用不同的API端点或版本")

print("\n【建议的下一步操作】")
print("1. 登录火山引擎控制台: https://console.volcengine.com/")
print("2. 进入「应用管理」或「服务管理」")
print("3. 查看TTS服务的:")
print("   - 服务状态（是否已开通）")
print("   - Resource ID（正确的值）")
print("   - API文档（确认正确的参数格式）")
print("4. 将正确的resource_id添加到.env文件:")
print("   VOLCENGINE_TTS_RESOURCE_ID=你的resource_id")
print("5. 如果V1 API的operation值不对，请查看API文档确认正确的值")

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)

