# Voice AI Assistant

一个基于 DashScope 和 FastAPI 构建的实时语音 AI 助手项目。该项目可以接收文本输入，通过大语言模型生成自然语言回复，并使用 TTS（Text-to-Speech）技术实时播放语音。

## 功能特点

- 实时 MP3 音频播放功能
- 与 DashScope 集成，支持大语言模型对话
- 支持语音合成（TTS）实时播放
- 多线程处理确保流畅体验
- RESTful API 接口设计

## 文件说明

### [RealtimeMp3Player.py](file:///Users/alice/Code/new/RealtimeMp3Player.py)
实现了实时 MP3 播放器类，使用 ffmpeg 解码 MP3 数据并通过 PyAudio 实时播放音频。

主要功能：
- 初始化音频播放环境
- 写入 MP3 数据进行解码和播放
- 安全地停止和重置播放器

### [sever.py](file:///Users/alice/Code/new/sever.py)
FastAPI 服务器实现，提供语音助手的核心功能。

主要组件：
- `SpeechSession` 类：封装会话特定的资源
- `/start` 端点：开始一个新的语音合成会话
- `/stop` 端点：立即停止当前语音合成会话
- `/status` 端点：获取当前语音合成状态
- `/health` 端点：服务健康检查

## 环境依赖

- Python 3.7+
- DashScope SDK
- FastAPI
- PyAudio
- FFmpeg
- python-dotenv
- uvicorn

## 安装步骤

1. 克隆项目到本地：
   ```bash
   git clone <repository-url>
   cd <project-directory>
   ```

2. 安装所需依赖：
   ```bash
   pip install dashscope fastapi pyaudio python-dotenv uvicorn
   ```

3. 安装系统依赖：
   - 安装 [FFmpeg](https://ffmpeg.org/download.html) 并确保可以在命令行中访问

4. 配置环境变量：
   创建 `.env` 文件并添加您的 DashScope API 密钥：
   ```
   DASHSCOPE_API_KEY=your_api_key_here
   ```

## 使用方法

1. 启动服务器：
   ```bash
   python sever.py
   ```

2. 服务器将在 `http://127.0.0.1:8001` 上运行

3. 发送请求开始对话：
   ```bash
   curl -X POST "http://127.0.0.1:8001/start" \
        -H "Content-Type: application/json" \
        -d '{"text": "你好，介绍一下你自己"}'
   ```

4. 停止当前语音播放：
   ```bash
   curl -X POST "http://127.0.0.1:8001/stop"
   ```

5. 检查服务状态：
   ```bash
   curl -X GET "http://127.0.0.1:8001/status"
   ```

6. 健康检查：
   ```bash
   curl -X GET "http://127.0.0.1:8001/health"
   ```

## API 接口文档

### POST /start
开始一个新的语音合成会话

**请求体：**
```json
{
  "text": "要转换为语音的文本"
}
```

**响应示例：**
```json
{
  "status": "started",
  "session_id": 1,
  "query": "要转换为语音的文本",
  "message": "Speech synthesis started"
}
```

### POST /stop
立即停止当前语音合成会话

**响应示例：**
```json
{
  "status": "stopped",
  "message": "Speech synthesis stopped immediately"
}
```

### GET /status
获取当前语音合成状态

**响应示例：**
```json
{
  "active": true,
  "session_id": 1,
  "stop_requested": false,
  "has_player": true,
  "has_synthesizer": true
}
```

### GET /health
服务健康检查

**响应示例：**
```json
{
  "status": "healthy",
  "service": "voice-ai-assistant"
}
```

## 工作原理

1. 用户通过 `/start` 接口发送文本查询
2. 服务器创建一个新的会话并启动线程处理请求
3. 使用 DashScope 的 Qwen 大语言模型生成回复
4. 通过 DashScope 的 TTS 服务将文本转为语音
5. 使用 [RealtimeMp3Player.py](file:///Users/alice/Code/new/RealtimeMp3Player.py) 实时播放 MP3 音频
6. 用户可随时通过 `/stop` 接口中断播放

## 注意事项

- 确保正确配置了 DashScope API 密钥
- 确保系统已安装并可访问 FFmpeg
- 项目目前配置为单会话模式，新会话会自动终止旧会话