#!/usr/bin/env python3
"""
测试豆包深度思考模型调用
严格按照官方文档和示例代码
"""
import os
import sys
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

try:
    from volcenginesdkarkruntime import Ark
    ARK_SDK_AVAILABLE = True
    print("✅ volcenginesdkarkruntime SDK 已安装")
except ImportError:
    ARK_SDK_AVAILABLE = False
    print("❌ volcenginesdkarkruntime SDK 未安装")
    print("请运行: pip install -U 'volcengine-python-sdk[ark]'")
    sys.exit(1)

def test_doubao_thinking():
    """测试豆包深度思考模型"""
    
    # 获取 API Key
    api_key = os.environ.get("ARK_API_KEY") or os.environ.get("DOUBAO_API_KEY")
    if not api_key:
        print("❌ 未找到 ARK_API_KEY 或 DOUBAO_API_KEY 环境变量")
        return False
    
    print(f"✅ API Key 已找到（长度: {len(api_key)}）")
    
    # 初始化客户端（严格按照官方示例）
    print("\n📡 初始化 Ark 客户端...")
    client = Ark(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=api_key,
        timeout=1800,  # 30分钟超时
    )
    print("✅ 客户端初始化成功")
    
    # 测试用的简单提示词
    test_prompt = "请用一句话解释什么是人工智能"
    
    print(f"\n📤 发送请求...")
    print(f"   模型: doubao-seed-1-6-thinking-250715")
    print(f"   提示词: {test_prompt}")
    print(f"   thinking: {{'type': 'enabled'}}")
    
    try:
        # 严格按照官方示例代码调用
        response = client.chat.completions.create(
            model="doubao-seed-1-6-thinking-250715",
            messages=[
                {
                    "role": "user",
                    "content": test_prompt
                }
            ],
            thinking={
                "type": "enabled"  # 启用深度思考能力
            },
        )
        
        print("✅ 请求成功，收到响应")
        
        # 分析响应结构
        print("\n📥 分析响应结构...")
        print(f"   响应类型: {type(response)}")
        print(f"   响应属性: {[attr for attr in dir(response) if not attr.startswith('_')]}")
        
        # 提取内容（严格按照官方示例）
        print("\n📥 提取响应内容...")
        choice = response.choices[0]
        print(f"   choice 类型: {type(choice)}")
        print(f"   choice 属性: {[attr for attr in dir(choice) if not attr.startswith('_')]}")
        
        message = choice.message
        print(f"   message 类型: {type(message)}")
        print(f"   message 属性: {[attr for attr in dir(message) if not attr.startswith('_')]}")
        
        content_text = message.content
        print(f"   content 类型: {type(content_text)}")
        print(f"   content 长度: {len(content_text) if content_text else 0}")
        
        # 检查是否有 thinking 或 reasoning_content 字段
        thinking_content = None
        if hasattr(choice, 'thinking') and choice.thinking:
            thinking_content = choice.thinking
            print(f"\n✅ 在 choice 中找到 thinking 字段")
            print(f"   thinking 类型: {type(thinking_content)}")
        elif hasattr(message, 'thinking') and message.thinking:
            thinking_content = message.thinking
            print(f"\n✅ 在 message 中找到 thinking 字段")
            print(f"   thinking 类型: {type(thinking_content)}")
        elif hasattr(message, 'reasoning_content') and message.reasoning_content:
            thinking_content = message.reasoning_content
            print(f"\n✅ 在 message 中找到 reasoning_content 字段（深度思考内容）")
            print(f"   reasoning_content 类型: {type(thinking_content)}")
            print(f"   reasoning_content 长度: {len(thinking_content) if isinstance(thinking_content, str) else 'N/A'}")
        else:
            print(f"\n⚠️ 未找到 thinking 或 reasoning_content 字段")
            # 检查 content 中是否包含思考过程
            if content_text and ('<thinking>' in content_text or '思考' in content_text):
                print(f"   ✅ 但在 content 中检测到思考过程标记")
        
        # 显示完整响应
        print("\n" + "="*80)
        print("完整响应内容:")
        print("="*80)
        if thinking_content:
            print("\n【思考过程】")
            if isinstance(thinking_content, str):
                print(thinking_content)
            else:
                print(str(thinking_content))
            print("\n" + "-"*80)
        
        print("\n【最终回答】")
        print(content_text)
        print("="*80)
        
        # 返回成功
        return True
        
    except Exception as e:
        print(f"\n❌ 请求失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("="*80)
    print("豆包深度思考模型测试")
    print("="*80)
    
    success = test_doubao_thinking()
    
    print("\n" + "="*80)
    if success:
        print("✅ 测试完成")
    else:
        print("❌ 测试失败")
    print("="*80)

