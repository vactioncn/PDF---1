#!/usr/bin/env python3
"""
严格按照官方示例代码测试
"""
import os
from dotenv import load_dotenv

load_dotenv()

# 通过命令安装方舟SDK pip install 'volcengine-python-sdk[ark]'
from volcenginesdkarkruntime import Ark 

# 初始化Ark客户端
client = Ark(
    # The base URL for model invocation
    base_url="https://ark.cn-beijing.volces.com/api/v3",    
    api_key=os.getenv('ARK_API_KEY') or os.getenv('DOUBAO_API_KEY'), 
    # 深度思考耗时更长，请设置更大的超时限制，推荐为1800 秒及以上
    timeout=1800,
)

# 创建一个对话请求
completion = client.chat.completions.create(
    # Replace with Model ID
    model = "doubao-seed-1-6-251015",
    messages=[
        {"role": "user", "content": "我要研究深度思考模型与非深度思考模型区别的课题，怎么体现我的专业性"}
    ]
)

# 当触发深度思考时，打印思维链内容
if hasattr(completion.choices[0].message, 'reasoning_content'):
    print("="*80)
    print("✅ 找到 reasoning_content:")
    print("="*80)
    print(completion.choices[0].message.reasoning_content)
    print("="*80)
else:
    print("❌ 未找到 reasoning_content")
    print(f"message 的所有属性: {[attr for attr in dir(completion.choices[0].message) if not attr.startswith('_')]}")

print("\n" + "="*80)
print("最终回答:")
print("="*80)
print(completion.choices[0].message.content)

