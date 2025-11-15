#!/usr/bin/env python3
"""
端到端测试：模拟完整的解读生成流程
检查思考过程是否正确传递到前端
"""
import os
import sys
import json
from dotenv import load_dotenv

load_dotenv()

try:
    from volcenginesdkarkruntime import Ark
except ImportError:
    print("❌ SDK 未安装")
    sys.exit(1)

# 模拟 app.py 中的函数
import re

def _extract_thinking_and_json(content: str):
    """提取思考过程和JSON"""
    if not content:
        return "", ""
    
    thinking_process = ""
    json_content = content
    
    # 格式1: <thinking>...</thinking>
    thinking_match = re.search(r'<thinking>(.*?)</thinking>', content, re.IGNORECASE | re.DOTALL)
    if thinking_match:
        thinking_process = thinking_match.group(1).strip()
        json_content = re.sub(r'<thinking>.*?</thinking>', '', json_content, flags=re.IGNORECASE | re.DOTALL)
        print(f"✅ 提取到思考过程（格式1），长度: {len(thinking_process)}")
    
    return thinking_process, json_content

def _add_debug_info_to_result(result, debug_info, content_text):
    """添加调试信息和思考过程"""
    print(f"\n🔍 _add_debug_info_to_result 开始")
    print(f"   content_text 长度: {len(content_text)}")
    print(f"   前200字符: {content_text[:200]}...")
    
    thinking_process, _ = _extract_thinking_and_json(content_text)
    print(f"   提取结果: thinking_process 长度={len(thinking_process)}")
    
    if thinking_process:
        result["_thinking_process"] = thinking_process
        print(f"✅ 思考过程已添加，长度: {len(thinking_process)}")
    else:
        print("❌ 未提取到思考过程")
        print(f"   content_text 包含 '<thinking>': {'<thinking>' in content_text}")
    
    result["_debug_info"] = debug_info
    return result

def test_complete_flow():
    """完整流程测试"""
    api_key = os.environ.get("ARK_API_KEY") or os.environ.get("DOUBAO_API_KEY")
    if not api_key:
        print("❌ 未找到 API Key")
        return False
    
    print("="*80)
    print("端到端测试：完整解读生成流程")
    print("="*80)
    
    # 简化的提示词
    system_message = "你是一个JSON输出助手。先展示思考过程（用<thinking>标签），然后输出JSON。"
    user_prompt = """请生成以下JSON格式的解读内容：
{
  "personalized_intro": "个性化导读",
  "interpretation": "正文解读",
  "quiz": "选择题",
  "question": "思考问题"
}

输入数据：
{
  "user_profile": {"profession": "CEO"},
  "chapter_summary": "测试摘要",
  "chapter_fulltext": "测试内容"
}"""
    
    model = "doubao-seed-1-6-thinking-250715"
    base_url = "https://ark.cn-beijing.volces.com/api/v3"
    
    print(f"\n📤 调用API...")
    
    try:
        client = Ark(base_url=base_url, api_key=api_key, timeout=1800)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=16000,
            thinking={"type": "enabled"},
        )
        
        print("✅ API调用成功\n")
        
        # 提取内容（模拟 app.py 的逻辑）
        choice = response.choices[0]
        message = choice.message
        content_text = message.content.strip()
        
        print(f"📥 原始响应:")
        print(f"   content 长度: {len(content_text)}")
        
        # 检查 reasoning_content
        reasoning_content = None
        if hasattr(message, 'reasoning_content') and message.reasoning_content:
            reasoning_content = message.reasoning_content
            print(f"✅ 找到 reasoning_content，长度: {len(reasoning_content)}")
            # 添加到 content_text 前面
            content_text = f"<thinking>\n{reasoning_content}\n</thinking>\n\n{content_text}"
            print(f"✅ 合并后 content_text 长度: {len(content_text)}")
        else:
            print(f"❌ 未找到 reasoning_content")
            print(f"   message 属性: {[attr for attr in dir(message) if not attr.startswith('_')]}")
        
        # 模拟 JSON 解析
        print(f"\n📄 提取JSON...")
        thinking_process, json_content = _extract_thinking_and_json(content_text)
        
        if thinking_process:
            print(f"✅ 成功提取思考过程，长度: {len(thinking_process)}")
        else:
            print(f"❌ 未能提取思考过程")
            print(f"   content_text 前500字符:\n{content_text[:500]}")
        
        # 解析 JSON
        try:
            result = json.loads(json_content.strip())
            print(f"✅ JSON 解析成功")
        except Exception as e:
            print(f"❌ JSON 解析失败: {e}")
            return False
        
        # 添加调试信息和思考过程（模拟 app.py 的逻辑）
        debug_info = {
            "model": model,
            "method": "SDK",
            "base_url": base_url,
            "thinking": {"type": "enabled"},
        }
        
        result = _add_debug_info_to_result(result, debug_info, content_text)
        
        # 检查最终结果
        print(f"\n📊 最终结果检查:")
        print(f"   是否有 _thinking_process: {'_thinking_process' in result}")
        if '_thinking_process' in result:
            print(f"   _thinking_process 长度: {len(result['_thinking_process'])}")
            print(f"   前500字符:\n{result['_thinking_process'][:500]}...")
        else:
            print(f"   ❌ 结果中没有 _thinking_process 字段")
            print(f"   结果的所有键: {list(result.keys())}")
        
        print(f"   是否有 _debug_info: {'_debug_info' in result}")
        
        # 模拟前端接收
        print(f"\n🌐 模拟前端接收:")
        json_str = json.dumps(result, ensure_ascii=False, indent=2)
        print(f"   JSON 字符串长度: {len(json_str)}")
        print(f"   包含 '_thinking_process': {'_thinking_process' in json_str}")
        
        # 验证前端可以读取
        parsed = json.loads(json_str)
        if '_thinking_process' in parsed and parsed['_thinking_process']:
            print(f"✅ 前端可以正确读取思考过程")
            return True
        else:
            print(f"❌ 前端无法读取思考过程")
            return False
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_complete_flow()
    print("\n" + "="*80)
    print("测试结果:", "✅ 成功" if success else "❌ 失败")
    print("="*80)
    sys.exit(0 if success else 1)

