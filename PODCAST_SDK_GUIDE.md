# 火山引擎播客 SDK 使用指南

## 根据 DEMO 描述

### 1. 下载 SDK
从火山引擎文档或控制台下载：
- `volcengine.speech.volc_speech_python_sdk_1.0.0.25.tar.gz`

### 2. 安装步骤
```bash
mkdir -p volcengine_podcasts_demo
tar xvzf volcengine_podcasts_demo.tar.gz -C ./volcengine_podcasts_demo
cd volcengine_podcasts_demo
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip3 install -e .
```

### 3. 调用方式
```bash
python3 examples/volcengine/podcasts.py \
  --appid <appid> \
  --access_token <access_token> \
  --text "介绍下火山引擎"
```

### 4. 关键信息
- 使用 `appid` 和 `access_token`（不是 `access_key` 和 `secret_key`）
- `access_token` 可能需要从控制台单独获取，或通过某种方式生成
- 查看 `examples/volcengine/podcasts.py` 了解实际实现

## 建议
1. 下载 SDK 包并查看 `podcasts.py` 的实际代码
2. 确认 `access_token` 的获取方式
3. 根据实际代码更新我们的实现
