# 🎙️ FastAPI TTS Control API

This project provides a **Text-to-Speech (TTS) Control API** built with **FastAPI**.
It connects to **Alibaba DashScope’s CosyVoice model** over **WebSocket** for **real-time streaming synthesis and playback**.

## ✨ Features

* 🚀 Start and stop TTS synthesis via REST API
* 🔊 Real-time audio playback using **PyAudio**
* 🎛️ Configurable voice, model, and playback options
* 🔄 Graceful handling of stop/interrupt events
* 📡 WebSocket streaming integration with DashScope

---

## 📦 Requirements

* Python **3.9+**
* [FastAPI](https://fastapi.tiangolo.com/)
* [Uvicorn](https://www.uvicorn.org/)
* [PyAudio](https://people.csail.mit.edu/hubert/pyaudio/)
* [websockets](https://websockets.readthedocs.io/)
* [python-dotenv](https://saurabh-kumar.com/python-dotenv/)

Install dependencies:

```bash
pip install fastapi uvicorn pyaudio websockets python-dotenv
```

---

## ⚙️ Configuration

1. Create a `.env` file in the project root:

   ```ini
   DASHSCOPE_API_KEY=your_dashscope_api_key_here
   ```

2. Default model and voice:

   * **Model**: `cosyvoice-v3`
   * **Voice**: `cosyvoice-v3-prefix-36d6a3f4cbae4cd8bd3664acba2cc891`

(You can override these per request.)

---

## ▶️ Running the Server

Start the FastAPI app with:

```bash
uvicorn main:app --reload
```

It will run at:

```
http://127.0.0.1:8000
```

---

## 📡 API Endpoints

### 1. **Start Synthesis**

```http
POST /tts/start
```

**Request Body:**

```json
{
  "text_segments": [
    "Hello, this is a test.",
    "This API streams audio in real time."
  ]
}
```

**Response:**

```json
{
  "status": "success",
  "message": "TTS synthesis started",
  "state": "running"
}
```

---

### 2. **Stop Synthesis**

```http
POST /tts/stop
```

**Response:**

```json
{
  "status": "success",
  "message": "TTS synthesis stopped",
  "state": "idle"
}
```

---

### 3. **Check Status**

```http
GET /tts/status
```

**Response:**

```json
{
  "state": "idle"
}
```

---

### 4. **Health Check**

```http
GET /health
```

**Response:**

```json
{
  "status": "healthy"
}
```

---

## 🛠 Notes

* Audio playback happens **locally** via PyAudio.
* Only **one synthesis session** can run at a time.
* Calling `/tts/stop` will **immediately stop audio playback** and reset the service state.
* If you start a new session while one is running, the API will return an error.

---

## 🖥️ Example Usage (with curl)

```bash
curl -X POST "http://127.0.0.1:8000/tts/start" \
-H "Content-Type: application/json" \
-d '{"text_segments":["你好，这是一个测试","FastAPI 和 TTS 流式合成演示"]}'
```

Stop playback:

```bash
curl -X POST "http://127.0.0.1:8000/tts/stop"
```

Check status:

```bash
curl http://127.0.0.1:8000/tts/status
```

---

## 📜 License

MIT License. Use at your own risk.
