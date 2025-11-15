# 火山引擎 TTS API 配置指南

## 获取 resource_id 的步骤

根据[火山引擎官方文档](https://www.volcengine.com/docs/6561/1105162)，获取 `resource_id` 需要以下步骤：

### 1. 完成企业认证
- 访问 [火山引擎用户认证页面](https://console.volcengine.com/user/authentication/detail/)
- 完成企业认证（个人账户可能无法使用某些服务）

### 2. 开通语音技术产品
- 前往 [火山引擎语音技术产品页面](https://console.volcengine.com/speech/app)
- 勾选以下服务：
  - ✅ **大模型语音合成**
  - ✅ **流式语音识别大模型**（如需要）
- **注意**：语音合成大模型从开通到可使用可能有 **5-10分钟** 的延迟

### 3. 获取 resource_id
- 开通服务后，返回 **"语音合成大模型"** 页面
- 在页面中找到并复制您的 `resource_id`
- 这个 `resource_id` 是调用 API 时的必要参数

### 4. 配置到项目
将获取到的 `resource_id` 添加到 `.env` 文件中：

```bash
VOLCENGINE_TTS_RESOURCE_ID=你的resource_id值
```

## API 接口选择

### V3 WebSocket 接口（推荐）
- **端点**: `wss://openspeech.bytedance.com/api/v3/tts/bidirection`
- **优点**: 性能更优，支持流式传输
- **必需参数**:
  - `X-Api-Access-Key`: 您的 Access Key
  - `X-Api-App-Key`: 您的 App ID
  - `X-Api-Resource-Id`: 从控制台获取的 resource_id
  - `X-Api-Connect-Id`: 连接唯一ID（自动生成）

### V1 HTTP 接口
- **端点**: `https://openspeech.bytedance.com/api/v1/tts`
- **状态**: 当前遇到 "Unsupported operation: synthesis" 错误
- **建议**: 优先使用 V3 WebSocket 接口

## 测试

运行测试脚本验证配置：

```bash
python3 test_podcast.py "测试文本"
```

如果使用自定义 resource_id：

```bash
VOLCENGINE_TTS_RESOURCE_ID=你的resource_id python3 test_podcast.py "测试文本"
```

## 常见问题

### Q: 为什么返回 "requested resource not granted"？
A: 可能的原因：
1. `resource_id` 不正确 - 请从控制台重新获取
2. 服务未开通 - 请确认已在控制台开通"大模型语音合成"服务
3. 账户权限不足 - 请确认已完成企业认证

### Q: 如何确认 resource_id 是否正确？
A: 
1. 登录火山引擎控制台
2. 进入「语音技术」->「语音合成大模型」
3. 查看页面中显示的 resource_id
4. 确保与 `.env` 文件中的值一致

### Q: 服务开通后多久可以使用？
A: 通常需要 5-10 分钟的延迟，请耐心等待。

## 参考链接

- [火山引擎 TTS 产品页面](https://www.volcengine.com/product/tts)
- [resource_id 获取文档](https://www.volcengine.com/docs/6561/1105162)
- [V3 WebSocket 接口文档](https://www.volcengine.com/docs/6561/1668014)

