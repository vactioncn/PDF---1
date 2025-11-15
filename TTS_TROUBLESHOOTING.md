# 火山引擎 TTS API 故障排查

## 已尝试的 resource_id

1. `volc.tts_async.emotion` - ❌ is not allowed
2. `volc.tts_async.default` - ❌ is not allowed  
3. `volc.service_type.10050` - ❌ is not allowed
4. `volc.service_type.10028` - ❌ is not allowed

## 问题分析

所有尝试的 resource_id 都返回 "is not allowed"，说明：
- 这些 resource_id 都不适用于 V3 WebSocket `/api/v3/tts/bidirection` 接口
- 可能需要不同的 resource_id 格式
- 或者需要不同的 API 端点

## 建议的解决方案

### 1. 联系火山引擎技术支持（推荐）

提供以下信息：
- 错误信息：`"resourceId volc.service_type.10050 is not allowed"`
- 尝试的接口：`wss://openspeech.bytedance.com/api/v3/tts/bidirection`
- 请求头：`X-Api-Resource-Id: volc.service_type.10050`
- 询问：该 resource_id 是否支持 WebSocket 接口？如果不支持，正确的 resource_id 是什么？

### 2. 检查控制台

在火山引擎控制台「语音合成大模型」页面：
- 查看是否有明确标注「支持 WebSocket」或「双向流式」的 resource_id
- 查看 API 文档或示例代码，确认正确的 resource_id 格式

### 3. 尝试其他 API 端点

如果当前 resource_id 不支持 WebSocket，可能需要：
- 使用异步 TTS API（如果 resource_id 是 `tts_async` 类型）
- 使用其他同步接口
- 确认正确的 API 端点和调用方式

## 当前配置

- Access Key: 已配置
- Secret Key: 已配置
- App ID: 9506742782
- Resource ID: volc.service_type.10050（当前）

## 参考文档

- Resource ID 获取：https://www.volcengine.com/docs/6561/1105162
- V3 WebSocket 接口：https://www.volcengine.com/docs/6561/1668014

## 总结

所有尝试的 resource_id 都返回 'is not allowed'，强烈建议：

1. **直接联系火山引擎技术支持**，提供：
   - 错误信息：'resourceId volc.service_type.10028 is not allowed'
   - 接口：wss://openspeech.bytedance.com/api/v3/tts/bidirection
   - 询问：这些 resource_id 是否支持 WebSocket？如果不支持，正确的 resource_id 是什么？

2. **检查控制台 API 文档**，查看是否有：
   - WebSocket 接口的示例代码
   - 明确标注支持 WebSocket 的 resource_id
   - 不同的 API 端点或调用方式

3. **确认服务类型**：
   - 是否开通了支持 WebSocket 的 TTS 服务？
   - 还是只有异步 TTS 服务？

