#!/usr/bin/env python3
"""
翻译功能测试脚本
用于独立测试翻译API，找出超时问题的根本原因
"""
import json
import os
import sys
import time
import requests
from typing import Optional, Dict, Any

# 从环境变量或直接设置API密钥
API_KEY = os.environ.get("DOUBAO_API_KEY", "ceb65a7e-d7df-4bab-bb62-8026473ad236")
API_BASE = os.environ.get("DOUBAO_API_BASE", "").strip()
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-2a870e378cb94696ab3a957a84ee5514")
DEEPSEEK_BASE = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

def estimate_tokens(text: str) -> int:
    """估算文本的token数量，区分ASCII和非ASCII字符"""
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii_chars = len(text) - ascii_chars
    # ASCII字符假设平均4字符≈1个token，非ASCII（中文）假设1字符≈1个token
    estimated = (ascii_chars / 4.0) + non_ascii_chars
    return int(estimated) + 1

def deepseek_translate(text: str, target_language: str = "zh", timeout: int = 600) -> str:
    if not text:
        return ""
    if not DEEPSEEK_KEY:
        raise RuntimeError("缺少 DeepSeek API Key")

    if any('\u4e00' <= ch <= '\u9fff' for ch in text):
        source_language = "zh"
    else:
        ascii_chars = sum(1 for ch in text if ord(ch) < 128)
        ratio = ascii_chars / max(len(text), 1)
        source_language = "en" if ratio > 0.6 else "auto"

    system_prompt = (
        "You are a professional translator. Translate the user provided text into {target_language} with high fidelity,"
        " preserving structure, numbering and formatting."
    ).format(target_language=target_language)

    endpoint = f"{DEEPSEEK_BASE.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_KEY}",
    }
    payload = {
        "model": "deepseek-reasoner",
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Source language: {source_language}. Target language: {target_language}.\n"
                    "Translate the following text precisely and output only the translated text:\n\n"
                    f"{text}"
                ),
            },
        ],
        "temperature": 0.2,
        "stream": False,
    }

    resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
    if resp.status_code != 200:
        try:
            error_body = resp.json()
            error_msg = error_body.get("error", {}).get("message") or resp.text[:200]
        except Exception:
            error_msg = resp.text[:200]
        raise RuntimeError(f"DeepSeek翻译失败 (HTTP {resp.status_code}): {error_msg}")

    data = resp.json()
    choices = data.get("choices", []) if isinstance(data, dict) else []
    if choices:
        message = choices[0].get("message", {})
        translated = str(message.get("content", "")).strip()
        if translated:
            return translated
    raise RuntimeError("DeepSeek翻译返回内容为空")


def deepseek_translate_chunked(text: str, target_language: str = "zh", timeout: int = 600) -> str:
    max_chars = 2800
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) > max_chars:
                for i in range(0, len(para), max_chars):
                    chunks.append(para[i : i + max_chars])
                current = ""
            else:
                current = para
    if current:
        chunks.append(current)

    translated = []
    for idx, chunk in enumerate(chunks):
        try:
            translated.append(deepseek_translate(chunk, target_language=target_language, timeout=timeout))
            print(f"DeepSeek chunk {idx+1}/{len(chunks)} 成功")
        except Exception as exc:
            print(f"DeepSeek chunk {idx+1} 失败: {exc}")
            translated.append(chunk)
    return "\n\n".join(translated)

def test_translate_single(text: str, timeout: int = 600) -> tuple[bool, str, float]:
    """测试单次翻译"""
    # 尝试多个可能的模型名称
    candidate_models = [
        "deepseek",  # DeepSeek R1
        "doubao-seed-translation-250915",  # 最新模型，优先尝试
        "doubao-seed-1-6",  # 通用模型，已经验证可用
        "doubao-seed-translation",
        "Doubao-Seed-Translation", 
        "doubao-pro-4k",  # 可能的其他模型
        "doubao-lite-4k",  # 可能的其他模型
    ]

    def call_responses_api(model_name: str, text_to_translate: str) -> str:
        configured_base = API_BASE
        if configured_base:
            base_url = configured_base.rstrip("/")
            if base_url.endswith("/responses"):
                endpoint = base_url
            elif base_url.endswith("/api/v3"):
                endpoint = f"{base_url}/responses"
            else:
                endpoint = f"{base_url}/api/v3/responses"
        else:
            endpoint = "https://ark.cn-beijing.volces.com/api/v3/responses"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        }

        # 猜测源语言
        if any('\u4e00' <= ch <= '\u9fff' for ch in text_to_translate):
            source_lang = "zh"
        else:
            ascii_chars = sum(1 for ch in text_to_translate if ord(ch) < 128)
            ratio = ascii_chars / max(len(text_to_translate), 1)
            source_lang = "en" if ratio > 0.6 else "auto"

        payload = {
            "model": model_name,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": text_to_translate,
                            "translation_options": {
                                "target_language": "zh",
                                "source_language": source_lang,
                            },
                        }
                    ],
                }
            ],
        }

        resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        texts: list[str] = []
        if isinstance(data, dict) and "output" in data and isinstance(data["output"], list):
            for message in data["output"]:
                if isinstance(message, dict):
                    content_list = message.get("content")
                    if isinstance(content_list, list):
                        for content_item in content_list:
                            if (
                                isinstance(content_item, dict)
                                and content_item.get("type") in {"output_text", "text"}
                            ):
                                texts.append(str(content_item.get("text", "")))
        translated = "".join(texts).strip()
        if not translated:
            print(f"Responses原始返回: {json.dumps(data, ensure_ascii=False)[:500]}")
            raise RuntimeError("Responses API 未返回文本内容")
        return translated
    
    system_prompt = "你是一个专业的翻译助手，请将用户提供的文本准确翻译成中文。保持原文的格式和结构，只翻译内容。"
    user_prompt = text
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    
    candidate_endpoints = []
    if API_BASE:
        api_base = API_BASE.rstrip("/")
        if api_base.endswith("/chat/completions"):
            candidate_endpoints.append(api_base)
        else:
            candidate_endpoints.append(f"{api_base}/chat/completions")
    candidate_endpoints.append("https://ark.cn-beijing.volces.com/api/v3/chat/completions")
    
    estimated_tokens = estimate_tokens(text)
    print(f"\n{'='*60}")
    print(f"测试单次翻译")
    print(f"文本长度: {len(text)} 字符")
    print(f"估算tokens: {estimated_tokens}")
    print(f"超时设置: {timeout} 秒")
    print(f"{'='*60}")
    
    start_time = time.time()
    last_error = None
    
    # 尝试不同的模型和端点组合
    for model in candidate_models:
        if model == "deepseek":
            try:
                if estimate_tokens(text) > 2500:
                    translated = deepseek_translate_chunked(text, target_language="zh", timeout=timeout)
                else:
                    translated = deepseek_translate(text, target_language="zh", timeout=timeout)
                elapsed = time.time() - start_time
                print(f"响应时间: {elapsed:.2f} 秒")
                print("翻译成功!")
                print(f"原文长度: {len(text)} 字符")
                print(f"译文长度: {len(translated)} 字符")
                print(f"译文预览: {translated[:100]}...")
                return True, translated, elapsed
            except Exception as exc:
                last_error = str(exc)
                print(f"DeepSeek 调用失败: {exc}")
                continue
        if model == "doubao-seed-translation-250915":
            try:
                print("使用Responses API", flush=True)
                translated = call_responses_api(model, text)
                elapsed = time.time() - start_time
                print(f"响应时间: {elapsed:.2f} 秒")
                print("翻译成功!")
                print(f"原文长度: {len(text)} 字符")
                print(f"译文长度: {len(translated)} 字符")
                print(f"译文预览: {translated[:100]}...")
                return True, translated, elapsed
            except Exception as exc:
                last_error = str(exc)
                print(f"Responses API 调用失败: {exc}")
                continue

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        
        for endpoint in candidate_endpoints:
            try:
                print(f"\n尝试端点: {endpoint}")
                print(f"模型: {model}")
                print(f"开始时间: {time.strftime('%H:%M:%S')}")
                
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                
                elapsed = time.time() - start_time
                print(f"响应时间: {elapsed:.2f} 秒")
                print(f"状态码: {response.status_code}")
                
                if response.status_code == 200:
                    response_data = response.json()
                    choices = response_data.get("choices", [])
                    if choices and len(choices) > 0:
                        message = choices[0].get("message", {})
                        content = message.get("content", "")
                        if isinstance(content, list):
                            text_content = ""
                            for item in content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text_content += item.get("text", "")
                            content = text_content
                        
                        translated = content.strip()
                        print(f"翻译成功!")
                        print(f"原文长度: {len(text)} 字符")
                        print(f"译文长度: {len(translated)} 字符")
                        print(f"译文预览: {translated[:100]}...")
                        return True, translated, elapsed
                    else:
                        print(f"错误: 响应中没有choices")
                        last_error = "响应中没有choices"
                else:
                    try:
                        error_body = response.json()
                        error_msg = error_body.get("error", {}).get("message", response.text[:200])
                    except:
                        error_msg = response.text[:200]
                    print(f"错误: HTTP {response.status_code} - {error_msg}")
                    last_error = f"HTTP {response.status_code}: {error_msg}"
            except requests.exceptions.Timeout:
                elapsed = time.time() - start_time
                print(f"❌ 超时! 耗时: {elapsed:.2f} 秒")
                last_error = f"请求超时 (>{timeout}秒)"
            except requests.exceptions.RequestException as exc:
                elapsed = time.time() - start_time
                print(f"❌ 请求异常: {exc}")
                last_error = str(exc)
            except Exception as exc:
                elapsed = time.time() - start_time
                print(f"❌ 其他异常: {exc}")
                last_error = str(exc)
        
        # 如果这个模型成功了，跳出外层循环
        if response.status_code == 200 and 'translated' in locals():
            break
    
    elapsed = time.time() - start_time
    if 'translated' not in locals():
        return False, f"所有模型和端点都失败: {last_error}", elapsed
    return True, translated, elapsed

def test_translate_chunked(text: str, max_chars_per_chunk: int = 3000, timeout: int = 600) -> tuple[bool, str, float]:
    """测试分段翻译"""
    print(f"\n{'='*60}")
    print(f"测试分段翻译")
    print(f"总文本长度: {len(text)} 字符")
    print(f"单段上限: {max_chars_per_chunk} 字符")
    print(f"超时设置: {timeout} 秒")
    print(f"{'='*60}")
    
    # 分段逻辑
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        if len(current_chunk) + len(para) + 2 <= max_chars_per_chunk:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
        else:
            if current_chunk:
                chunks.append(current_chunk)
            
            if len(para) > max_chars_per_chunk:
                # 按句子分割
                import re
                sentences = re.split(r'([.!?。！？]\s+)', para)
                current_sentence = ""
                for i in range(0, len(sentences), 2):
                    sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else "")
                    if len(current_sentence) + len(sentence) <= max_chars_per_chunk:
                        current_sentence += sentence
                    else:
                        if current_sentence:
                            chunks.append(current_sentence)
                        if len(sentence) > max_chars_per_chunk:
                            for j in range(0, len(sentence), max_chars_per_chunk):
                                chunks.append(sentence[j:j+max_chars_per_chunk])
                            current_sentence = ""
                        else:
                            current_sentence = sentence
                if current_sentence:
                    current_chunk = current_sentence
            else:
                current_chunk = para
    
    if current_chunk:
        chunks.append(current_chunk)
    
    print(f"\n分段结果: 共 {len(chunks)} 段")
    for i, chunk in enumerate(chunks):
        print(f"  段 {i+1}: {len(chunk)} 字符")
    
    # 逐段翻译
    start_time = time.time()
    translated_chunks = []
    
    for i, chunk in enumerate(chunks):
        print(f"\n--- 翻译第 {i+1}/{len(chunks)} 段 (长度: {len(chunk)} 字符) ---")
        success, translated, elapsed = test_translate_single(chunk, timeout)
        
        if success:
            translated_chunks.append(translated)
            print(f"✓ 第 {i+1} 段翻译成功，耗时: {elapsed:.2f} 秒")
        else:
            print(f"✗ 第 {i+1} 段翻译失败: {translated}")
            translated_chunks.append(chunk)  # 使用原文
    
    total_elapsed = time.time() - start_time
    result = "\n\n".join(translated_chunks)
    
    print(f"\n{'='*60}")
    print(f"分段翻译完成")
    print(f"总耗时: {total_elapsed:.2f} 秒")
    print(f"平均每段: {total_elapsed/len(chunks):.2f} 秒")
    print(f"译文总长度: {len(result)} 字符")
    print(f"{'='*60}")
    
    return True, result, total_elapsed

def main():
    print("="*60)
    print("豆包翻译功能测试")
    print("="*60)
    
    # 测试1: 短文本（应该很快）
    print("\n\n【测试1: 短文本翻译】")
    short_text = "Hello, this is a test. How are you today?"
    success, result, elapsed = test_translate_single(short_text, timeout=60)
    if success:
        print(f"✓ 测试1通过: {elapsed:.2f}秒")
    else:
        print(f"✗ 测试1失败: {result}")
        return
    
    # 测试2: 中等长度文本（1000字符左右）
    print("\n\n【测试2: 中等长度文本翻译】")
    medium_text = """
    The Four Hour Work Week is a book written by Timothy Ferriss that challenges the traditional concept of work and retirement. 
    The book proposes a new approach to life design, where individuals can achieve financial freedom and time freedom by working smarter, not harder.
    Ferriss introduces the concept of the "New Rich" - people who have abandoned the deferred-life plan and instead create luxury lifestyles in the present using the currency of the new rich: time and mobility.
    The book is divided into four main sections: Definition, Elimination, Automation, and Liberation.
    In the Definition section, Ferriss challenges readers to question their assumptions about work and life.
    He argues that most people are working towards retirement, but by the time they reach it, they may be too old or unhealthy to enjoy it.
    Instead, he proposes mini-retirements throughout life, where people take extended breaks to travel, learn, and experience life.
    """.strip()
    # 扩展到约1000字符
    medium_text = medium_text * 3
    success, result, elapsed = test_translate_single(medium_text, timeout=120)
    if success:
        print(f"✓ 测试2通过: {elapsed:.2f}秒")
    else:
        print(f"✗ 测试2失败: {result}")
        print("尝试分段翻译...")
        success, result, elapsed = test_translate_chunked(medium_text, max_chars_per_chunk=3000, timeout=120)
        if success:
            print(f"✓ 测试2分段翻译通过: {elapsed:.2f}秒")
        else:
            print(f"✗ 测试2分段翻译失败: {result}")
    
    # 测试3: 长文本（5000+字符，应该触发分段）
    print("\n\n【测试3: 长文本翻译（应该分段）】")
    long_text = """
    The Four Hour Work Week is a revolutionary book that has changed the way millions of people think about work and life.
    Written by Timothy Ferriss, this book challenges the traditional 9-to-5 work model and proposes a new way of living.
    The core concept is to work smarter, not harder, and to design a lifestyle that allows for maximum freedom and flexibility.
    
    Ferriss introduces the concept of the "New Rich" - individuals who have abandoned the deferred-life plan.
    Instead of working for 40 years and then retiring, the New Rich create luxury lifestyles in the present.
    They use the currency of the new rich: time and mobility, rather than just money.
    
    The book is structured into four main sections: Definition, Elimination, Automation, and Liberation.
    Each section provides practical strategies and tools for achieving the 4-hour workweek lifestyle.
    
    In the Definition section, Ferriss challenges readers to question their assumptions about work and life.
    He argues that most people are working towards retirement, but by the time they reach it, they may be too old or unhealthy to enjoy it.
    Instead, he proposes mini-retirements throughout life, where people take extended breaks to travel, learn, and experience life.
    
    The Elimination section focuses on time management and productivity.
    Ferriss introduces the 80/20 principle, which states that 80% of results come from 20% of efforts.
    He also discusses the concept of "selective ignorance" - choosing what information to consume and what to ignore.
    The goal is to eliminate time-wasting activities and focus only on what truly matters.
    
    Automation is about creating systems that work for you, even when you're not there.
    Ferriss discusses outsourcing, delegation, and creating passive income streams.
    He provides practical advice on how to automate your business and personal life.
    
    Finally, Liberation is about breaking free from the traditional work model.
    Ferriss discusses how to negotiate remote work arrangements, how to travel while working, and how to create a lifestyle of freedom.
    """.strip()
    # 扩展到约5000字符
    long_text = long_text * 4
    
    estimated = estimate_tokens(long_text)
    print(f"文本长度: {len(long_text)} 字符")
    print(f"估算tokens: {estimated}")
    
    if estimated > 3500:
        print("文本超过3500 tokens，将使用分段翻译")
        success, result, elapsed = test_translate_chunked(long_text, max_chars_per_chunk=3000, timeout=600)
    else:
        print("文本在3500 tokens以内，将使用单次翻译")
        success, result, elapsed = test_translate_single(long_text, timeout=600)
    
    if success:
        print(f"✓ 测试3通过: {elapsed:.2f}秒")
    else:
        print(f"✗ 测试3失败: {result}")
    
    print("\n\n" + "="*60)
    print("测试完成!")
    print("="*60)

if __name__ == "__main__":
    main()

