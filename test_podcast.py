#!/usr/bin/env python3
"""
播客功能测试脚本
用于独立测试火山引擎TTS API，验证连通性和配置是否正确
"""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse
from typing import Dict, Any, Optional

import requests
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 从环境变量读取配置
VOLCENGINE_TTS_ACCESS_KEY = os.environ.get("VOLCENGINE_TTS_ACCESS_KEY", "")
VOLCENGINE_TTS_SECRET_KEY = os.environ.get("VOLCENGINE_TTS_SECRET_KEY", "")
VOLCENGINE_TTS_APP_ID = os.environ.get("VOLCENGINE_TTS_APP_ID", "")


def generate_volcengine_tts_signature(
    access_key: str, secret_key: str, method: str, host: str, path: str, params: Dict[str, Any]
) -> str:
    """生成火山引擎TTS API签名（根据火山引擎文档：https://www.volcengine.com/docs/6561/1668014）"""
    # 排除signature参数
    params_for_sign = {k: v for k, v in params.items() if k != "signature"}
    
    # 按参数名排序
    sorted_params = sorted(params_for_sign.items())
    query_string = "&".join([f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted_params])
    
    # 构建待签名字符串：Method + Host + Path + QueryString
    string_to_sign = f"{method}\n{host}\n{path}\n{query_string}"
    
    print(f"待签名字符串:\n{string_to_sign}\n", flush=True)
    
    # 使用HMAC-SHA256签名
    signature = hmac.new(
        secret_key.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha256
    ).digest()
    
    # Base64编码
    return base64.b64encode(signature).decode('utf-8')


def test_volcengine_tts(
    text: str,
    voice_type: str = "BV700_streaming",
    language: str = "zh",
    speed_ratio: float = 1.0,
    volume_ratio: float = 1.0,
    pitch_ratio: float = 1.0,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    app_id: Optional[str] = None,
) -> Dict[str, Any]:
    """测试火山引擎TTS API"""
    if not access_key:
        access_key = VOLCENGINE_TTS_ACCESS_KEY
    if not secret_key:
        secret_key = VOLCENGINE_TTS_SECRET_KEY
    if not app_id:
        app_id = VOLCENGINE_TTS_APP_ID
    
    if not access_key or not secret_key or not app_id:
        raise RuntimeError("缺少火山引擎TTS配置：需要 ACCESS_KEY、SECRET_KEY 和 APP_ID")
    
    print("=" * 60)
    print("火山引擎TTS API 测试")
    print("=" * 60)
    print(f"Access Key: {access_key[:10]}...")
    print(f"Secret Key: {secret_key[:10]}...")
    print(f"App ID: {app_id}")
    print(f"测试文本: {text[:50]}...")
    print(f"音色类型: {voice_type}")
    print(f"语言: {language}")
    print(f"语速: {speed_ratio}, 音量: {volume_ratio}, 音调: {pitch_ratio}")
    print("=" * 60)
    
    # 火山引擎TTS API端点
    host = "openspeech.bytedance.com"
    path = "/api/v1/tts"
    method = "POST"
    timestamp = int(time.time())
    
    # 构建请求参数（按文档要求）
    params = {
        "appid": app_id,
        "text": text,
        "text_type": "plain",
        "voice_type": voice_type,
        "language": language,
        "speed_ratio": str(speed_ratio),
        "volume_ratio": str(volume_ratio),
        "pitch_ratio": str(pitch_ratio),
        "encoding": "mp3",
        "rate": "24000",
        "timestamp": str(timestamp),
    }
    
    print("\n请求参数（用于签名）:")
    for k, v in params.items():
        if k == "text":
            print(f"  {k}: {v[:50]}... (长度: {len(v)})")
        else:
            print(f"  {k}: {v}")
    
    # 直接使用JSON格式（根据技术支持建议）
    import uuid
    reqid = str(uuid.uuid4())
    url_base = f"https://{host}{path}"
    
    # 构建JSON参数（根据技术支持提供的格式）
    # 尝试使用常见的cluster值：seed-tts 或 seed-tts-2.0
    # 先尝试 seed-tts-2.0，如果失败再尝试 seed-tts
    cluster_value = os.environ.get("VOLCENGINE_TTS_CLUSTER", "seed-tts-2.0")
    
    json_params = {
        "app": {
            "appid": app_id,
            "token": access_key,  # 使用access_key作为token
            "cluster": cluster_value,  # 从控制台获取，通常是 seed-tts 或 seed-tts-2.0
        },
        "user": {
            "uid": access_key,  # 使用access_key作为uid
        },
        "audio": {
            "voice_type": voice_type,
            "encoding": "mp3",
            "rate": 24000,
        },
        "request": {
            # 尝试不同的operation/action值
            # 根据技术支持，应该是 "synthesis"，但如果API不支持，可能需要其他值
            "operation": "synthesis",  # 或尝试 "tts", "text_to_speech" 等
            "text": text,
            "reqid": reqid,
        },
    }
    
    # 对于JSON格式，签名可能需要基于JSON字符串生成
    # 但根据技术支持说签名方式无需调整，先尝试将JSON参数扁平化后生成签名
    print("\n生成签名（基于JSON参数扁平化）...")
    # 尝试将JSON参数扁平化用于签名
    flat_params = {
        "appid": app_id,
        "token": access_key,
        "cluster": cluster_value,
        "uid": access_key,
        "voice_type": voice_type,
        "encoding": "mp3",
        "rate": "24000",
        "operation": "synthesis",
        "text": text,
        "reqid": reqid,
        "timestamp": str(timestamp),
    }
    signature = generate_volcengine_tts_signature(access_key, secret_key, method, host, path, flat_params)
    json_params["signature"] = signature
    json_params["timestamp"] = timestamp
    
    print(f"签名: {signature[:50]}...")
    print(f"\n发送请求到: {url_base}")
    print(f"JSON参数结构: {json.dumps(json_params, ensure_ascii=False, indent=2)[:500]}")
    
    headers_json = {
        "Content-Type": "application/json",
    }
    
    start_time = time.time()
    try:
        response = requests.post(url_base, json=json_params, headers=headers_json, timeout=60)
        elapsed = time.time() - start_time
        
        print(f"\n响应状态码: {response.status_code}")
        print(f"响应时间: {elapsed:.2f}秒")
        print(f"响应头: {dict(response.headers)}")
        
        if response.status_code != 200:
            error_text = response.text[:500] if response.text else "无响应内容"
            print(f"\n❌ 错误响应: {error_text}")
            raise RuntimeError(f"火山引擎TTS API返回错误 {response.status_code}: {error_text}")
        
        # 尝试解析JSON响应
        try:
            data = response.json()
            print(f"\n响应JSON: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")
            
            # 检查响应中的错误码
            if data.get("code") != 0 and data.get("code") is not None:
                error_msg = data.get("message") or data.get("msg") or "未知错误"
                print(f"\n❌ API错误：{error_msg} (code: {data.get('code')})")
                raise RuntimeError(f"火山引擎TTS API错误：{error_msg} (code: {data.get('code')})")
            
            # 返回音频数据
            audio_data = data.get("data", {}).get("audio", "")
            if not audio_data:
                # 尝试其他可能的字段名
                audio_data = data.get("audio", "")
                if not audio_data:
                    print(f"\n❌ 音频数据为空")
                    raise RuntimeError(f"火山引擎TTS API返回的音频数据为空。响应: {json.dumps(data, ensure_ascii=False)[:500]}")
            
            duration = data.get("data", {}).get("duration", 0)
            print(f"\n✅ 成功！音频数据长度: {len(audio_data)} 字符 (Base64)")
            print(f"   音频时长: {duration} 秒")
            
            return {
                "audio_base64": audio_data,
                "format": "mp3",
                "duration": duration,
            }
        except json.JSONDecodeError:
            # 如果不是JSON，可能是二进制音频数据
            if response.content and len(response.content) > 0:
                print(f"\n✅ 成功！收到二进制音频数据，长度: {len(response.content)} 字节")
                # 直接返回音频数据
                audio_base64 = base64.b64encode(response.content).decode('utf-8')
                return {
                    "audio_base64": audio_base64,
                    "format": "mp3",
                    "duration": 0,
                }
            else:
                print(f"\n❌ 响应格式错误")
                raise RuntimeError(f"火山引擎TTS API返回格式错误: {response.text[:200]}")
                
    except requests.exceptions.RequestException as exc:
        elapsed = time.time() - start_time
        print(f"\n❌ 请求异常 (耗时: {elapsed:.2f}秒): {exc}")
        raise RuntimeError(f"火山引擎TTS API请求失败：{exc}")


def save_audio_file(audio_base64: str, filename: str = "test_podcast.mp3"):
    """保存音频文件"""
    try:
        audio_bytes = base64.b64decode(audio_base64)
        with open(filename, "wb") as f:
            f.write(audio_bytes)
        print(f"\n💾 音频文件已保存: {filename} ({len(audio_bytes)} 字节)")
        return filename
    except Exception as e:
        print(f"\n❌ 保存音频文件失败: {e}")
        raise


def test_volcengine_tts_v3_websocket(
    text: str,
    voice_type: str = "BV700_streaming",
    language: str = "zh",
    speed_ratio: float = 1.0,
    volume_ratio: float = 1.0,
    pitch_ratio: float = 1.0,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    app_id: Optional[str] = None,
    resource_id: str = "seed-tts-2.0",
) -> Dict[str, Any]:
    """使用V3 WebSocket接口测试火山引擎TTS API"""
    try:
        import websocket
    except ImportError:
        raise RuntimeError("需要安装 websocket-client: pip3 install websocket-client")
    
    if not access_key:
        access_key = VOLCENGINE_TTS_ACCESS_KEY
    if not secret_key:
        secret_key = VOLCENGINE_TTS_SECRET_KEY
    if not app_id:
        app_id = VOLCENGINE_TTS_APP_ID
    
    if not access_key or not secret_key or not app_id:
        raise RuntimeError("缺少火山引擎TTS配置：需要 ACCESS_KEY、SECRET_KEY 和 APP_ID")
    
    # 检查 resource_id
    if not resource_id or resource_id == "seed-tts-2.0":
        print("\n⚠️  警告: 使用默认 resource_id，可能不正确！")
        print("请从火山引擎控制台「语音合成大模型」页面获取正确的 resource_id")
        print("参考文档: https://www.volcengine.com/docs/6561/1105162")
        print("配置方法: 在 .env 文件中添加 VOLCENGINE_TTS_RESOURCE_ID=你的resource_id\n")
    
    print("=" * 60)
    print("火山引擎TTS API V3 WebSocket 测试")
    print("=" * 60)
    print(f"Access Key: {access_key[:10]}...")
    print(f"Secret Key: {secret_key[:10]}...")
    print(f"App ID: {app_id}")
    print(f"Resource ID: {resource_id}")
    if resource_id == "seed-tts-2.0":
        print("  ⚠️  这是默认值，请从控制台获取正确的 resource_id")
    print(f"测试文本: {text[:50]}...")
    print("=" * 60)
    
    # V3 WebSocket端点
    ws_url = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    
    # 构建请求消息
    import uuid
    reqid = str(uuid.uuid4())
    
    # 根据文档，尝试使用 resource_id 作为 cluster，或者使用常见的 cluster 值
    # 先尝试将 resource_id 作为 cluster，如果失败再尝试其他值
    cluster_value = os.environ.get("VOLCENGINE_TTS_CLUSTER")
    if not cluster_value:
        # 尝试使用 resource_id 作为 cluster（某些情况下可能相同）
        # 或者尝试常见的值
        if resource_id and not resource_id.startswith("volc.service_type"):
            cluster_value = resource_id
        else:
            cluster_value = "seed-tts-2.0"
    
    message = {
        "app": {
            "appid": app_id,
            "token": access_key,
            "cluster": cluster_value,  # 根据文档，可能需要 cluster 参数
        },
        "user": {
            "uid": access_key,
        },
        "audio": {
            "voice_type": voice_type,
            "encoding": "mp3",
            "rate": 24000,
            "language": language,
            "speed_ratio": speed_ratio,
            "volume_ratio": volume_ratio,
            "pitch_ratio": pitch_ratio,
        },
        "request": {
            "reqid": reqid,
            "text": text,
        },
    }
    
    print(f"Cluster: {cluster_value}")
    
    print(f"\nWebSocket URL: {ws_url}")
    print(f"请求消息: {json.dumps(message, ensure_ascii=False, indent=2)[:500]}")
    
    # 收集音频数据
    audio_chunks = []
    error_message = None
    received_response = False
    
    def on_message(ws, message_data):
        nonlocal audio_chunks, error_message, received_response
        received_response = True
        try:
            if isinstance(message_data, bytes):
                # 二进制音频数据
                audio_chunks.append(message_data)
                print(f"✅ 收到音频数据块: {len(message_data)} 字节")
            else:
                # JSON响应（可能是文本格式）
                try:
                    data = json.loads(message_data)
                    print(f"收到JSON响应: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")
                    if data.get("code") == 0:
                        # 成功响应，可能包含音频数据
                        if "data" in data and "audio" in data["data"]:
                            audio_base64 = data["data"]["audio"]
                            audio_bytes = base64.b64decode(audio_base64)
                            audio_chunks.append(audio_bytes)
                            print(f"✅ 从JSON中提取音频数据: {len(audio_bytes)} 字节")
                    else:
                        error_message = data.get("message") or data.get("error") or "未知错误"
                except json.JSONDecodeError:
                    # 如果不是JSON，可能是纯文本错误信息
                    error_message = message_data.decode('utf-8', errors='ignore') if isinstance(message_data, bytes) else str(message_data)
                    print(f"收到非JSON响应: {error_message[:200]}")
        except Exception as e:
            print(f"处理消息时出错: {e}")
            import traceback
            traceback.print_exc()
    
    def on_error(ws, error):
        nonlocal error_message
        error_message = str(error)
        print(f"WebSocket错误: {error}")
    
    def on_close(ws, close_status_code, close_msg):
        print(f"\nWebSocket连接关闭: {close_status_code}, {close_msg}")
    
    def on_open(ws):
        print("\nWebSocket连接已建立，发送请求...")
        ws.send(json.dumps(message))
    
    # 创建WebSocket连接
    # 根据文档，V3 WebSocket需要以下header
    import uuid as uuid_module
    connect_id = str(uuid_module.uuid4())
    
    # 根据文档，V3 WebSocket 需要在 header 中提供 resource_id
    headers = {
        "X-Api-Access-Key": access_key,
        "X-Api-App-Key": app_id,
        "X-Api-Connect-Id": connect_id,  # 连接唯一ID
        "X-Api-Resource-Id": resource_id,  # resource_id 必须在 header 中
    }
    
    print(f"Connect ID: {connect_id}")
    
    print(f"\n连接WebSocket...")
    print(f"请求头: {headers}")
    
    ws = websocket.WebSocketApp(
        ws_url,
        header=headers,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open,
    )
    
    # 在单独线程中运行WebSocket
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()
    
    # 等待响应（最多30秒）
    timeout = 30
    start_time = time.time()
    while time.time() - start_time < timeout:
        if received_response and (audio_chunks or error_message):
            break
        time.sleep(0.1)
    
    ws.close()
    wst.join(timeout=1)
    
    if error_message:
        # 如果是资源未授权或不允许的错误，提供更详细的帮助信息
        if "resource not granted" in error_message or "requested resource not granted" in error_message or "is not allowed" in error_message:
            error_detail = ""
            if "is not allowed" in error_message:
                error_detail = (
                    "\n⚠️  关键提示：\n"
                    "这个 resource_id 不适用于 V3 WebSocket 双向流式接口。\n\n"
                    "可能的情况：\n"
                    "1. resource_id 格式不正确 - 需要支持 WebSocket 的 resource_id\n"
                    "2. 服务类型不匹配 - 该 resource_id 可能用于异步或其他类型的 TTS 服务\n"
                    "3. 权限问题 - 该 resource_id 可能未授权用于 WebSocket 接口\n\n"
                    "请检查：\n"
                    "1. 在控制台「语音合成大模型」页面，确认是否有专门标注支持「WebSocket」或「双向流式」的 resource_id\n"
                    "2. 查看 API 文档，确认该 resource_id 是否支持 V3 WebSocket 接口\n"
                    "3. 联系火山引擎技术支持，提供 resource_id 和错误信息，确认正确的配置方式\n"
                )
            raise RuntimeError(
                f"火山引擎TTS V3 WebSocket错误: {error_message}\n"
                f"{error_detail}"
                "可能的原因：\n"
                "1. resource_id 不正确或不适用于此接口 - 请从火山引擎控制台「语音合成大模型」页面获取正确的 resource_id\n"
                "2. 服务未开通 - 请确认已在控制台开通「大模型语音合成」服务\n"
                "3. 账户权限不足 - 请确认已完成企业认证\n"
                "4. 服务延迟 - 开通服务后可能需要 5-10 分钟才能使用\n"
                "5. resource_id 类型不匹配 - 确认该 resource_id 是否支持 V3 WebSocket 双向流式接口\n\n"
                "参考文档: https://www.volcengine.com/docs/6561/1105162\n"
                "配置方法: 在 .env 文件中添加 VOLCENGINE_TTS_RESOURCE_ID=你的resource_id"
            )
        raise RuntimeError(f"火山引擎TTS V3 WebSocket错误: {error_message}")
    
    if not audio_chunks:
        raise RuntimeError("火山引擎TTS V3 WebSocket未返回音频数据")
    
    # 合并所有音频块
    audio_data = b"".join(audio_chunks)
    audio_base64 = base64.b64encode(audio_data).decode('utf-8')
    
    print(f"\n✅ 成功！收到音频数据，总长度: {len(audio_data)} 字节")
    
    return {
        "audio_base64": audio_base64,
        "format": "mp3",
        "duration": 0,  # WebSocket可能不返回时长
    }


def main():
    """主测试函数"""
    print("\n" + "=" * 60)
    print("火山引擎 TTS API 连通性测试")
    print("=" * 60 + "\n")
    
    # 检查配置
    if not VOLCENGINE_TTS_ACCESS_KEY or not VOLCENGINE_TTS_SECRET_KEY or not VOLCENGINE_TTS_APP_ID:
        print("❌ 错误: 缺少配置信息")
        print("\n请在 .env 文件中配置以下信息:")
        print("  VOLCENGINE_TTS_ACCESS_KEY=你的AccessKey")
        print("  VOLCENGINE_TTS_SECRET_KEY=你的SecretKey")
        print("  VOLCENGINE_TTS_APP_ID=你的AppID")
        print("\n或者通过环境变量设置:")
        print("  export VOLCENGINE_TTS_ACCESS_KEY=...")
        print("  export VOLCENGINE_TTS_SECRET_KEY=...")
        print("  export VOLCENGINE_TTS_APP_ID=...")
        return 1
    
    # 测试文本
    test_text = "你好，这是一个火山引擎TTS API的测试。如果你能听到这段语音，说明API配置正确。"
    
    # 可以自定义测试文本
    if len(os.sys.argv) > 1:
        test_text = " ".join(os.sys.argv[1:])
        print(f"使用自定义测试文本: {test_text}\n")
    
    # 优先尝试V3 WebSocket接口
    use_v3 = os.environ.get("VOLCENGINE_USE_V3", "true").lower() == "true"
    
    try:
        if use_v3:
            print("使用 V3 WebSocket 接口（推荐）\n")
            # 从环境变量读取 resource_id，如果没有则使用默认值（会显示警告）
            resource_id = os.environ.get("VOLCENGINE_TTS_RESOURCE_ID", "seed-tts-2.0")
            result = test_volcengine_tts_v3_websocket(
                text=test_text,
                voice_type="BV700_streaming",
                language="zh",
                speed_ratio=1.0,
                volume_ratio=1.0,
                pitch_ratio=1.0,
                resource_id=resource_id,
            )
        else:
            print("使用 V1 HTTP 接口\n")
            result = test_volcengine_tts(
                text=test_text,
                voice_type="BV700_streaming",
                language="zh",
                speed_ratio=1.0,
                volume_ratio=1.0,
                pitch_ratio=1.0,
            )
        
        # 保存音频文件
        filename = save_audio_file(result["audio_base64"], "test_podcast.mp3")
        
        print("\n" + "=" * 60)
        print("✅ 测试成功！")
        print("=" * 60)
        print(f"音频文件: {filename}")
        print(f"格式: {result['format']}")
        print(f"时长: {result['duration']} 秒")
        print("\n你可以播放这个文件来验证音频质量。")
        return 0
        
    except Exception as e:
        print("\n" + "=" * 60)
        print("❌ 测试失败")
        print("=" * 60)
        print(f"错误: {e}")
        print("\n请检查:")
        print("1. .env 文件中的配置是否正确")
        print("2. AccessKey、SecretKey 和 AppID 是否有效")
        print("3. 网络连接是否正常")
        print("4. 火山引擎控制台中的服务是否已开通")
        print("5. 如果使用V3，需要安装: pip3 install websocket-client")
        return 1


if __name__ == "__main__":
    exit(main())

