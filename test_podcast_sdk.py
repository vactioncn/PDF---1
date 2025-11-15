#!/usr/bin/env python3
"""
基于火山引擎 SDK 的播客 API 测试脚本
使用正确的端点和协议
"""
import asyncio
import json
import os
import sys
import time
import uuid

# 添加 protocols 模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import websockets
except ImportError:
    print("需要安装 websockets: pip3 install websockets")
    sys.exit(1)

from protocols import (
    EventType,
    MsgType,
    finish_connection,
    finish_session,
    receive_message,
    start_connection,
    start_session,
    wait_for_event,
)

from dotenv import load_dotenv

load_dotenv()

ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
APP_KEY = "aGjiRDfUWi"  # SDK 中的固定值


async def test_podcast_api(
    appid: str,
    access_token: str,
    text: str,
    resource_id: str = "volc.service_type.10050",
    encoding: str = "mp3",
):
    """测试播客 API"""
    print("=" * 60)
    print("火山引擎播客 API 测试（基于 SDK）")
    print("=" * 60)
    print(f"App ID: {appid}")
    print(f"Access Token: {access_token[:20]}...")
    print(f"Resource ID: {resource_id}")
    print(f"文本: {text[:50]}...")
    print("=" * 60)

    headers = {
        "X-Api-App-Id": appid,
        "X-Api-App-Key": APP_KEY,
        "X-Api-Access-Key": access_token,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }

    podcast_audio = bytearray()
    audio = bytearray()
    websocket = None

    try:
        # 建立 WebSocket 连接
        print(f"\n连接 WebSocket: {ENDPOINT}")
        websocket = await websockets.connect(ENDPOINT, additional_headers=headers)
        print("✅ WebSocket 连接已建立")

        # 构建请求参数
        req_params = {
            "input_id": f"test_{int(time.time())}",
            "input_text": text,
            "action": 0,  # 0: 文本播客
            "use_head_music": False,
            "use_tail_music": False,
            "input_info": {
                "input_url": "",
                "return_audio_url": False,
                "only_nlp_text": False,
            },
            "speaker_info": {"random_order": False},
            "audio_config": {
                "format": encoding,
                "sample_rate": 24000,
                "speech_rate": 0,
            },
        }

        # Start connection [event=1]
        print("\n发送 StartConnection...")
        await start_connection(websocket)

        # Connection started [event=50]
        print("等待 ConnectionStarted...")
        await wait_for_event(websocket, MsgType.FullServerResponse, EventType.ConnectionStarted)
        print("✅ 连接已启动")

        session_id = str(uuid.uuid4())
        print(f"\nSession ID: {session_id}")

        # Start session [event=100]
        print("发送 StartSession...")
        await start_session(websocket, json.dumps(req_params).encode(), session_id)

        # Session started [event=150]
        print("等待 SessionStarted...")
        await wait_for_event(websocket, MsgType.FullServerResponse, EventType.SessionStarted)
        print("✅ 会话已启动")

        # Finish session [event=102]
        print("发送 FinishSession...")
        await finish_session(websocket, session_id)

        # 接收响应
        print("\n开始接收音频数据...")
        audio_received = False

        while True:
            msg = await receive_message(websocket)

            # 音频数据块
            if msg.type == MsgType.AudioOnlyServer and msg.event == EventType.PodcastRoundResponse:
                audio.extend(msg.payload)
                if not audio_received:
                    audio_received = True
                    print("✅ 开始接收音频数据")
                print(f"  收到音频块: {len(msg.payload)} 字节")

            # 错误信息
            elif msg.type == MsgType.Error:
                error_msg = msg.payload.decode("utf-8", errors="ignore")
                # 如果是超时错误但已收到音频，继续处理
                if "RPCTimeout" in error_msg and audio_received:
                    print(f"⚠️  收到超时错误，但已收到音频数据，继续处理...")
                    # 将当前音频添加到总音频
                    if audio:
                        podcast_audio.extend(audio)
                        audio.clear()
                    # 尝试结束会话
                    try:
                        await finish_session(websocket, session_id)
                    except:
                        pass
                    break
                else:
                    raise RuntimeError(f"服务器错误: {error_msg}")

            elif msg.type == MsgType.FullServerResponse:
                # 播客 round 开始
                if msg.event == EventType.PodcastRoundStart:
                    data = json.loads(msg.payload.decode("utf-8"))
                    print(f"  播客轮次开始: round_id={data.get('round_id')}, speaker={data.get('speaker')}")

                # 播客 round 结束
                elif msg.event == EventType.PodcastRoundEnd:
                    data = json.loads(msg.payload.decode("utf-8"))
                    print(f"  播客轮次结束: round_id={data.get('round_id')}")
                    if data.get("is_error"):
                        raise RuntimeError(f"播客轮次错误: {data}")
                    if audio:
                        podcast_audio.extend(audio)
                        print(f"  累计音频: {len(podcast_audio)} 字节")
                        audio.clear()

                # 播客结束
                elif msg.event == EventType.PodcastEnd:
                    data = json.loads(msg.payload.decode("utf-8"))
                    print(f"✅ 播客结束: {data}")

            # 会话结束
            if msg.event == EventType.SessionFinished:
                print("✅ 会话已结束")
                break

        # 如果还有未处理的音频，添加到总音频
        if audio:
            podcast_audio.extend(audio)
            audio.clear()

        if not audio_received and not podcast_audio:
            raise RuntimeError("未收到音频数据")

        # 保持连接，方便下次请求
        try:
            await finish_connection(websocket)
            await wait_for_event(websocket, MsgType.FullServerResponse, EventType.ConnectionFinished)
            print("✅ 连接已关闭")
        except Exception as e:
            print(f"⚠️  关闭连接时出错（可忽略）: {e}")

        if podcast_audio:
            import base64

            audio_base64 = base64.b64encode(bytes(podcast_audio)).decode("utf-8")
            return {
                "audio_base64": audio_base64,
                "format": encoding,
                "duration": 0,
            }
        else:
            raise RuntimeError("未生成音频数据")

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback

        traceback.print_exc()
        raise
    finally:
        if websocket:
            await websocket.close()


def main():
    """主函数"""
    appid = os.environ.get("VOLCENGINE_TTS_APP_ID", "")
    access_token = os.environ.get("VOLCENGINE_TTS_ACCESS_KEY", "")
    resource_id = os.environ.get("VOLCENGINE_TTS_RESOURCE_ID", "volc.service_type.10050")

    if not appid or not access_token:
        print("❌ 错误: 缺少配置信息")
        print("\n请在 .env 文件中配置:")
        print("  VOLCENGINE_TTS_APP_ID=你的AppID")
        print("  VOLCENGINE_TTS_ACCESS_KEY=你的AccessToken")
        print("  VOLCENGINE_TTS_RESOURCE_ID=你的ResourceID (可选，默认: volc.service_type.10050)")
        return 1

    test_text = "你好，这是一个火山引擎播客API的测试。如果你能听到这段语音，说明API配置正确。"
    if len(sys.argv) > 1:
        test_text = " ".join(sys.argv[1:])

    try:
        result = asyncio.run(
            test_podcast_api(
                appid=appid,
                access_token=access_token,
                text=test_text,
                resource_id=resource_id,
            )
        )

        # 保存音频文件
        import base64

        audio_data = base64.b64decode(result["audio_base64"])
        filename = f"podcast_test_{int(time.time())}.{result['format']}"
        with open(filename, "wb") as f:
            f.write(audio_data)

        print("\n" + "=" * 60)
        print("✅ 测试成功！")
        print("=" * 60)
        print(f"音频文件: {filename}")
        print(f"格式: {result['format']}")
        print(f"大小: {len(audio_data)} 字节")
        return 0

    except Exception as e:
        print("\n" + "=" * 60)
        print("❌ 测试失败")
        print("=" * 60)
        print(f"错误: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

