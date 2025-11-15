#!/usr/bin/env python3
"""
基于火山引擎播客 DEMO 的测试脚本
使用 appid 和 access_token 的方式
"""
import argparse
import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_podcast_api(appid: str, access_token: str, text: str):
    """测试播客 API"""
    print("=" * 60)
    print("火山引擎播客 API 测试（基于 DEMO）")
    print("=" * 60)
    print(f"App ID: {appid}")
    print(f"Access Token: {access_token[:20]}...")
    print(f"文本: {text}")
    print("=" * 60)
    
    # 根据 DEMO 描述，可能需要使用 HTTP API
    # 尝试不同的端点
    endpoints = [
        "https://openspeech.bytedance.com/api/v1/podcasts",
        "https://openspeech.bytedance.com/api/v1/tts/podcasts",
        "https://openspeech.bytedance.com/api/v3/podcasts",
    ]
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    
    payload = {
        "appid": appid,
        "text": text,
        "voice_type": "BV700_streaming",
        "language": "zh",
        "encoding": "mp3",
        "rate": 24000,
    }
    
    for endpoint in endpoints:
        print(f"\n尝试端点: {endpoint}")
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=30
            )
            print(f"状态码: {response.status_code}")
            print(f"响应: {response.text[:500]}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    if "audio" in data or "data" in data:
                        print("✅ 成功获取音频数据")
                        return data
                except:
                    # 可能是二进制音频数据
                    if response.content:
                        print(f"✅ 成功获取音频数据（二进制）: {len(response.content)} 字节")
                        return {"audio_data": response.content}
        except Exception as e:
            print(f"❌ 错误: {e}")
    
    # 如果 HTTP API 都不行，可能需要使用 WebSocket
    print("\n尝试 WebSocket 方式...")
    try:
        import websocket
        import threading
        import uuid
        
        ws_url = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
        
        message = {
            "app": {
                "appid": appid,
                "token": access_token,
            },
            "user": {
                "uid": access_token,
            },
            "audio": {
                "voice_type": "BV700_streaming",
                "encoding": "mp3",
                "rate": 24000,
                "language": "zh",
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
            },
        }
        
        headers_ws = {
            "X-Api-App-Key": appid,
            "X-Api-Access-Key": access_token,
            "X-Api-Resource-Id": "volc.service_type.10050",
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        
        audio_chunks = []
        error_msg = None
        
        def on_message(ws, msg):
            nonlocal audio_chunks, error_msg
            if isinstance(msg, bytes):
                audio_chunks.append(msg)
                print(f"✅ 收到音频数据: {len(msg)} 字节")
            else:
                data = json.loads(msg)
                print(f"收到响应: {json.dumps(data, ensure_ascii=False)[:300]}")
                if data.get("code") != 0:
                    error_msg = data.get("message") or "未知错误"
        
        def on_error(ws, error):
            print(f"WebSocket错误: {error}")
        
        def on_open(ws):
            print("WebSocket连接已建立，发送请求...")
            ws.send(json.dumps(message))
        
        ws = websocket.WebSocketApp(
            ws_url,
            header=headers_ws,
            on_message=on_message,
            on_error=on_error,
            on_open=on_open,
        )
        
        wst = threading.Thread(target=ws.run_forever)
        wst.daemon = True
        wst.start()
        
        import time
        time.sleep(5)
        ws.close()
        
        if audio_chunks:
            audio_data = b"".join(audio_chunks)
            print(f"✅ 成功获取音频数据: {len(audio_data)} 字节")
            return {"audio_data": audio_data}
        elif error_msg:
            print(f"❌ 错误: {error_msg}")
    except ImportError:
        print("需要安装 websocket-client: pip3 install websocket-client")
    except Exception as e:
        print(f"❌ WebSocket 错误: {e}")
    
    return None


def main():
    parser = argparse.ArgumentParser(description="火山引擎播客 API 测试")
    parser.add_argument("--appid", required=True, help="APP ID")
    parser.add_argument("--access_token", required=True, help="Access Token")
    parser.add_argument("--text", required=True, help="播客文本")
    
    args = parser.parse_args()
    
    result = test_podcast_api(args.appid, args.access_token, args.text)
    
    if result:
        # 保存音频文件
        if "audio_data" in result:
            with open("podcast_demo.mp3", "wb") as f:
                f.write(result["audio_data"])
            print(f"\n✅ 音频已保存到: podcast_demo.mp3")
        elif "audio" in result:
            import base64
            audio_data = base64.b64decode(result["audio"])
            with open("podcast_demo.mp3", "wb") as f:
                f.write(audio_data)
            print(f"\n✅ 音频已保存到: podcast_demo.mp3")


if __name__ == "__main__":
    main()

