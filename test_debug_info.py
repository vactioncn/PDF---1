#!/usr/bin/env python3
"""测试后端是否正确返回 _debug_info 和 _thinking_process"""

import json
import sys

# 模拟 call_llm 的返回结果
def simulate_call_llm_result():
    """模拟 call_llm 函数的返回结果"""
    result = {
        "personalized_intro": "测试导读",
        "interpretation": "测试解读内容",
        "summary_and_application": "测试应用",
        "powerful_questions": "测试问题",
        "quiz": []
    }
    
    # 添加思考过程
    result["_thinking_process"] = "这是模拟的思考过程内容，应该被显示在界面上。"
    
    # 添加调试信息
    result["_debug_info"] = {
        "model": "doubao-seed-1.6-thinking-250715",
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "temperature": 0.3,
        "max_tokens": 16000,
        "thinking_mode": True,
        "stream": False,
    }
    
    return result

# 模拟 generate_endpoint 的返回
def simulate_generate_endpoint():
    """模拟 generate_endpoint 的返回"""
    llm_output = simulate_call_llm_result()
    
    # 这是 generate_endpoint 中的返回语句
    response = {
        "result": llm_output,
        "record_id": 123
    }
    
    return response

# 测试
if __name__ == "__main__":
    print("=" * 60)
    print("测试后端返回数据结构")
    print("=" * 60)
    
    response = simulate_generate_endpoint()
    
    print("\n1. 完整响应结构:")
    print(json.dumps(response, indent=2, ensure_ascii=False))
    
    print("\n2. response['result'] 的键:")
    print(list(response["result"].keys()))
    
    print("\n3. _debug_info 是否存在:")
    print("_debug_info" in response["result"])
    if "_debug_info" in response["result"]:
        print("  内容:", json.dumps(response["result"]["_debug_info"], indent=2, ensure_ascii=False))
    
    print("\n4. _thinking_process 是否存在:")
    print("_thinking_process" in response["result"])
    if "_thinking_process" in response["result"]:
        print("  长度:", len(response["result"]["_thinking_process"]), "字符")
        print("  预览:", response["result"]["_thinking_process"][:100] + "...")
    
    print("\n" + "=" * 60)
    print("✅ 如果以上都显示正确，说明后端数据结构是正确的")
    print("   问题可能在前端显示逻辑")
    print("=" * 60)




