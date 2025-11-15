# Resource ID 问题说明

## 当前问题

您配置的 resource_id 是 `volc.tts_async.*` 格式（如 `volc.tts_async.default`），这些 resource_id **不适用于** V3 WebSocket 双向流式接口。

## 问题原因

- `tts_async` = 异步 TTS 服务
- V3 WebSocket `/api/v3/tts/bidirection` = 双向流式接口
- 两者不匹配

## 解决方案

### 方案1：获取支持 WebSocket 的 resource_id（推荐）

在火山引擎控制台「语音合成大模型」页面，查找：
- 格式可能类似：`volc.tts.streaming.*` 或 `volc.tts.bidirection.*`
- 或者没有 `async` 后缀的 resource_id
- 确认该 resource_id 支持「双向流式」或「WebSocket」接口

### 方案2：使用异步 TTS API

如果您只有 `tts_async` 类型的 resource_id，需要：
1. 使用异步 TTS API（不是 WebSocket）
2. 可能需要不同的端点，如 `/api/v1/tts/async` 或类似
3. 需要实现异步调用逻辑（提交任务 -> 轮询结果）

### 方案3：开通正确的服务

确认在控制台开通的是：
- ✅ **大模型语音合成（流式）** - 支持 WebSocket
- ❌ **大模型语音合成（异步）** - 不支持 WebSocket

## 需要确认的信息

请在控制台检查：

1. **查看所有可用的 resource_id**
   - 在「语音合成大模型」页面
   - 查看是否有非 `tts_async` 的 resource_id

2. **查看服务类型**
   - 确认开通了哪些服务
   - 是否有「流式」或「WebSocket」相关的服务

3. **查看 API 文档**
   - 确认 `volc.tts_async.*` 对应的 API 端点
   - 确认是否有支持 WebSocket 的 resource_id

## 下一步

请提供以下信息之一：
1. 控制台中所有可用的 resource_id 列表
2. 或者确认是否需要实现异步 TTS API

