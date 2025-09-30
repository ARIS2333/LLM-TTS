from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import asyncio
import pyaudio
import websockets
import json
import os
import dotenv
import threading
from enum import Enum
import uuid

dotenv.load_dotenv()

app = FastAPI(title="TTS Control API")

# Configuration
DASHSCOPE_API_KEY = os.getenv('DASHSCOPE_API_KEY')
WEBSOCKET_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
model = "cosyvoice-v3"
voice = "cosyvoice-v3-prefix-36d6a3f4cbae4cd8bd3664acba2cc891"


class SynthesisState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"


class TTSRequest(BaseModel):
    text_segments: List[str]
    model: Optional[str] = model
    voice: Optional[str] = voice


class TTSService:
    def __init__(self):
        self.state = SynthesisState.IDLE
        self.synthesis_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.websocket = None
        self.player = None
        self.stream = None

    async def _synthesis_worker(self, text_segments: List[str], model: str, voice: str):
        """Worker function to run synthesis with direct WebSocket control"""
        try:
            # Connect to WebSocket
            headers = {
                "Authorization": f"bearer {DASHSCOPE_API_KEY}",
            }
            
            async with websockets.connect(WEBSOCKET_URL, additional_headers=headers) as websocket:
                self.websocket = websocket
                
                # Initialize audio player
                self.player = pyaudio.PyAudio()
                self.stream = self.player.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=22050,
                    output=True
                )
                
                print(f"WebSocket 连接已建立")
                
                # Generate task ID
                task_id = str(uuid.uuid4())
                
                # Send run-task command
                run_task = {
                    "header": {
                        "action": "run-task",
                        "task_id": task_id,
                        "streaming": "duplex"
                    },
                    "payload": {
                        "task_group": "audio",
                        "task": "tts",
                        "function": "SpeechSynthesizer",
                        "model": model,
                        "parameters": {
                            "text_type": "PlainText",
                            "voice": voice,
                            "format": "pcm",
                            "sample_rate": 22050,
                            "volume": 50,
                            "rate": 1.0,
                            "pitch": 1.0
                        },
                        "input": {}
                    }
                }
                
                await websocket.send(json.dumps(run_task))
                print("已发送 run-task 指令")
                
                # Wait for task-started event
                task_started = False
                while not task_started and not self._stop_event.is_set():
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                        if isinstance(response, str):
                            event = json.loads(response)
                            if event.get("header", {}).get("event") == "task-started":
                                task_started = True
                                print("收到 task-started 事件")
                    except asyncio.TimeoutError:
                        continue
                
                if self._stop_event.is_set():
                    print("在任务启动前收到停止信号")
                    await websocket.close()
                    return
                
                # Create task to receive audio data
                async def receive_audio():
                    try:
                        while not self._stop_event.is_set():
                            try:
                                response = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                                
                                if isinstance(response, bytes):
                                    # Binary audio data
                                    if not self._stop_event.is_set() and self.stream:
                                        self.stream.write(response)
                                        print(f"播放音频数据: {len(response)} 字节")
                                elif isinstance(response, str):
                                    # JSON event
                                    event = json.loads(response)
                                    event_type = event.get("header", {}).get("event")
                                    
                                    if event_type == "task-finished":
                                        print("收到 task-finished 事件，合成完成")
                                        break
                                    elif event_type == "task-failed":
                                        error_msg = event.get("header", {}).get("error_message", "Unknown error")
                                        print(f"任务失败: {error_msg}")
                                        break
                                    elif event_type == "result-generated":
                                        print("收到 result-generated 事件")
                                        
                            except asyncio.TimeoutError:
                                continue
                                
                    except Exception as e:
                        print(f"接收音频时出错: {e}")
                
                # Start receiving audio in background
                receive_task = asyncio.create_task(receive_audio())
                
                # Send text segments
                for i, text in enumerate(text_segments):
                    if self._stop_event.is_set():
                        print(f"在发送第 {i+1}/{len(text_segments)} 段时收到停止信号")
                        break
                    
                    continue_task = {
                        "header": {
                            "action": "continue-task",
                            "task_id": task_id,
                            "streaming": "duplex"
                        },
                        "payload": {
                            "input": {
                                "text": text
                            }
                        }
                    }
                    
                    await websocket.send(json.dumps(continue_task))
                    print(f"已发送文本段 {i+1}/{len(text_segments)}: {text}")
                    
                    # Small delay between segments, with stop check
                    for _ in range(10):
                        if self._stop_event.is_set():
                            break
                        await asyncio.sleep(0.01)
                    
                    if self._stop_event.is_set():
                        break
                
                # Send finish-task if not stopped
                if not self._stop_event.is_set():
                    finish_task = {
                        "header": {
                            "action": "finish-task",
                            "task_id": task_id,
                            "streaming": "duplex"
                        },
                        "payload": {
                            "input": {}
                        }
                    }
                    await websocket.send(json.dumps(finish_task))
                    print("已发送 finish-task 指令")
                    
                    # Wait for receive task to complete
                    await receive_task
                else:
                    print("跳过 finish-task，因为已停止")
                    receive_task.cancel()
                    
        except Exception as e:
            print(f"合成错误: {e}")
        finally:
            # Clean up
            if self.stream:
                try:
                    self.stream.stop_stream()
                    self.stream.close()
                except:
                    pass
                self.stream = None
                
            if self.player:
                try:
                    self.player.terminate()
                except:
                    pass
                self.player = None
            
            if self.websocket:
                try:
                    await self.websocket.close()
                except:
                    pass
                self.websocket = None
                
            with self._lock:
                self.state = SynthesisState.IDLE
                print("合成工作线程已结束，资源已清理")

    def _run_async_synthesis(self, text_segments: List[str], model: str, voice: str):
        """Run async synthesis worker in a thread"""
        asyncio.run(self._synthesis_worker(text_segments, model, voice))

    def start(self, text_segments: List[str], model: str, voice: str):
        """Start TTS synthesis"""
        with self._lock:
            if self.state == SynthesisState.RUNNING:
                raise HTTPException(status_code=400, detail="Synthesis already running")

            self.state = SynthesisState.RUNNING
            self._stop_event.clear()

        # Start synthesis in a separate thread
        self.synthesis_thread = threading.Thread(
            target=self._run_async_synthesis,
            args=(text_segments, model, voice),
            daemon=True
        )
        self.synthesis_thread.start()

    def stop(self):
        """Stop TTS synthesis immediately"""
        with self._lock:
            if self.state != SynthesisState.RUNNING:
                raise HTTPException(status_code=400, detail="No synthesis running")

            print("发送停止信号...")
            self.state = SynthesisState.STOPPED
            self._stop_event.set()
            
            # Immediately stop audio playback
            if self.stream:
                try:
                    print("停止音频流...")
                    self.stream.stop_stream()
                    self.stream.close()
                    self.stream = None
                except Exception as e:
                    print(f"停止音频流时出错: {e}")
            
            if self.player:
                try:
                    self.player.terminate()
                    self.player = None
                except Exception as e:
                    print(f"终止播放器时出错: {e}")

        print("停止命令已执行，音频播放已停止")
        
        with self._lock:
            self.state = SynthesisState.IDLE
            print("TTS 服务状态已重置为 IDLE")

    def get_state(self) -> str:
        with self._lock:
            return self.state.value


# Global TTS service instance
tts_service = TTSService()


@app.post("/tts/start")
async def start_synthesis(request: TTSRequest):
    """Start TTS synthesis with the provided text segments"""
    try:
        tts_service.start(
            text_segments=request.text_segments,
            model=request.model,
            voice=request.voice
        )
        return {
            "status": "success",
            "message": "TTS synthesis started",
            "state": tts_service.get_state()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tts/stop")
async def stop_synthesis():
    """Stop the currently running TTS synthesis"""
    try:
        tts_service.stop()
        return {
            "status": "success",
            "message": "TTS synthesis stopped",
            "state": tts_service.get_state()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tts/status")
async def get_status():
    """Get current TTS synthesis status"""
    return {
        "state": tts_service.get_state()
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
