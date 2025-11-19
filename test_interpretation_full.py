#!/usr/bin/env python3
"""
完整测试解读生成流程
模拟实际调用，检查所有步骤
"""
import os
import sys
import json
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入app中的函数
from app import call_llm, build_generation_prompt

def test_full_interpretation():
    """完整测试解读生成"""
    
    print("="*80)
    print("完整解读生成流程测试")
    print("="*80)
    
    # 模拟提示词部分
    prompt_parts = {
        "intro_prompt": "请生成个性化导读",
        "body_prompt": "请详细解读以下内容：\n{chapter_fulltext}",
        "quiz_prompt": "请生成选择题",
        "question_prompt": "请生成思考问题"
    }
    
    # 模拟用户数据
    payload = {
        "user_profile": {
            "profession": "CEO",
            "reading_goal": "提升管理技能",
            "focus_preference": "可落地的应用案例",
            "explanation_density": "30% 核心"
        },
        "chapter_summary": "这是测试章节摘要",
        "chapter_fulltext": "这是测试章节的完整内容，用于测试深度思考模型是否能正确返回思考过程。"
    }
    
    print("\n📤 调用 call_llm...")
    print(f"   模型: doubao-seed-1-6-thinking-250715")
    print(f"   thinking: {{'type': 'enabled'}}")
    
    try:
        result = call_llm(prompt_parts, payload)
        
        print("\n✅ call_llm 调用成功")
        print(f"   返回结果类型: {type(result)}")
        print(f"   返回结果键: {list(result.keys()) if isinstance(result, dict) else 'N/A'}")
        
        # 检查 _debug_info
        if "_debug_info" in result:
            debug_info = result["_debug_info"]
            print(f"\n📊 调试信息:")
            print(f"   模型: {debug_info.get('model')}")
            print(f"   调用方式: {debug_info.get('method')}")
            print(f"   Base URL: {debug_info.get('base_url')}")
            print(f"   Thinking: {debug_info.get('thinking')}")
        else:
            print("\n❌ 未找到 _debug_info")
        
        # 检查 _thinking_process
        if "_thinking_process" in result:
            thinking = result["_thinking_process"]
            print(f"\n🧠 思考过程:")
            print(f"   类型: {type(thinking)}")
            print(f"   长度: {len(thinking) if isinstance(thinking, str) else 'N/A'}")
            if isinstance(thinking, str):
                print(f"   前500字符: {thinking[:500]}...")
        else:
            print("\n❌ 未找到 _thinking_process")
        
        # 检查主要结果字段
        print(f"\n📄 主要结果字段:")
        for key in ["personalized_intro", "interpretation", "quiz", "question"]:
            if key in result:
                content = result[key]
                length = len(content) if isinstance(content, str) else 0
                print(f"   {key}: {length} 字符")
            else:
                print(f"   {key}: ❌ 不存在")
        
        # 检查原始内容中是否包含思考过程
        print(f"\n🔍 检查原始内容:")
        # 我们需要查看 call_llm 内部的 content_text
        # 但由于 content_text 在函数内部，我们需要通过日志来判断
        
        print("\n" + "="*80)
        print("测试完成")
        print("="*80)
        
        return True
        
    except Exception as e:
        print(f"\n❌ 测试失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_full_interpretation()
    sys.exit(0 if success else 1)




