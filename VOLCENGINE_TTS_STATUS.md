# 火山引擎 TTS API 当前状态

## Resource ID
- **播客用 Resource ID**: `volc.service_type.10050`
- **状态**: API 返回 "resourceId volc.service_type.10050 is not allowed"

## 问题分析
`volc.service_type.10050` 是播客专用的 resource_id，但 V3 WebSocket 接口返回 "is not allowed"。

## 可能的原因
1. **需要授权**: 该 resource_id 可能需要特殊授权才能使用
2. **API 端点不匹配**: 可能需要使用不同的 API 端点（非 WebSocket）
3. **调用方式不同**: 可能需要使用异步 API 或其他调用方式

## 建议
1. 联系火山引擎技术支持，确认 `volc.service_type.10050` 的正确使用方式
2. 确认是否需要授权该 resource_id
3. 查看是否有专门的播客 API 文档或示例代码

## 当前配置
- Access Key: 已配置
- Secret Key: 已配置  
- App ID: 9506742782
- Resource ID: volc.service_type.10050
