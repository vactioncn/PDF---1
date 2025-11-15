# 火山引擎播客 API 问题总结

## 问题根源

### 1. **使用了错误的 API 端点**
- ❌ **错误端点**: `wss://openspeech.bytedance.com/api/v3/tts/bidirection`
- ✅ **正确端点**: `wss://openspeech.bytedance.com/api/v3/sami/podcasttts`

**原因**: 播客 API 有专门的端点，不是通用的 TTS 端点。

### 2. **使用了错误的协议**
- ❌ **错误方式**: 简单的 JSON WebSocket 消息
- ✅ **正确方式**: 自定义二进制协议（protocols 模块）

**原因**: 播客 API 使用自定义的二进制消息格式，不是简单的 JSON。

### 3. **缺少必要的请求头**
- ❌ **缺少**: `X-Api-App-Key: "aGjiRDfUWi"`（固定值）
- ✅ **需要**: 所有必需的 header，包括固定的 App-Key

**原因**: SDK 中使用了一个固定的 App-Key 值，这是必需的认证信息。

### 4. **使用了错误的 WebSocket 库**
- ❌ **错误库**: `websocket-client`（同步库）
- ✅ **正确库**: `websockets`（异步库）

**原因**: 播客 API 需要异步 WebSocket 连接，且使用 `websockets` 库的 API。

### 5. **Resource ID 的误解**
- 尝试了多个 resource_id，都返回 "is not allowed"
- **真正原因**: 不是 resource_id 的问题，而是使用了错误的端点和协议
- `volc.service_type.10050` 是正确的播客 resource_id，但需要在正确的端点使用

## 解决方案

1. **使用正确的端点**: `wss://openspeech.bytedance.com/api/v3/sami/podcasttts`
2. **使用 protocols 模块**: 实现自定义二进制协议
3. **添加固定 App-Key**: `X-Api-App-Key: "aGjiRDfUWi"`
4. **使用 websockets 库**: 异步 WebSocket 连接
5. **正确的调用流程**:
   - StartConnection (event=1)
   - Wait for ConnectionStarted (event=50)
   - StartSession (event=100)
   - Wait for SessionStarted (event=150)
   - FinishSession (event=102)
   - Receive audio data (PodcastRoundResponse, event=361)
   - Wait for SessionFinished (event=152)
   - FinishConnection (event=2)

## 关键发现

通过查看 SDK 的示例代码 (`podcasts.py`)，发现了：
- 正确的端点
- 正确的协议实现
- 必需的 header 配置
- 正确的调用流程

**教训**: 当 API 文档不够清晰时，查看官方 SDK 的示例代码是最可靠的方法。
