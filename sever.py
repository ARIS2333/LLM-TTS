# app_improved.py
import os
import threading
import time
from http import HTTPStatus

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import dashscope
from dashscope import Generation
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback

from RealtimeMp3Player import RealtimeMp3Player
import dotenv
import uvicorn

dotenv.load_dotenv()

# ---------- Setup ----------
app = FastAPI()

# Global state with lock protection
state_lock = threading.Lock()
current_session = None  # 改为存储整个 session 对象
stop_event = threading.Event()

system_text = (
    "你是一个闲聊型语音AI助手，主要任务是和用户展开日常性的友善聊天。"
    "请不要回复使用任何格式化文本，回复要求口语化，不要使用markdown格式或者列表。"
)

# Load API key
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
if not dashscope.api_key:
    raise ValueError("DASHSCOPE_API_KEY not set in environment")


class Query(BaseModel):
    text: str


# Session class to encapsulate all session-specific resources
class SpeechSession:
    def __init__(self, session_id: int, query_text: str):
        self.session_id = session_id
        self.query_text = query_text
        self.player = None
        self.synthesizer = None
        self.thread = None
        self.stop_event = threading.Event()
        self.is_stopped = False
        
    def stop(self):
        """立即停止当前会话"""
        if self.is_stopped:
            return
        
        self.is_stopped = True
        print(f"[Session {self.session_id}] Stopping...")
        
        # 1. 设置停止标志
        self.stop_event.set()
        
        # 2. 立即停止 player
        if self.player:
            try:
                self.player.force_stop()
                print(f"[Session {self.session_id}] Player stopped")
            except Exception as e:
                print(f"[Session {self.session_id}] Error stopping player: {e}")
            self.player = None
        
        # 3. 清除 synthesizer 引用
        if self.synthesizer:
            self.synthesizer = None
            print(f"[Session {self.session_id}] Synthesizer cleared")
        
        print(f"[Session {self.session_id}] Stop completed")


# ---------- Core Function ----------
def run_speech(session: SpeechSession):
    """Main function to handle speech synthesis and playback"""
    
    print(f"\n[Session {session.session_id}] Starting speech generation...")

    try:
        # 检查是否已经被取消
        if session.stop_event.is_set():
            print(f"[Session {session.session_id}] Cancelled before start")
            return
        
        # 初始化 player
        session.player = RealtimeMp3Player(verbose=True)
        session.player.start()
        print(f"[Session {session.session_id}] Player started")

        # 定义回调类 - 关键：使用 session 对象而不是全局变量
        class Callback(ResultCallback):
            def __init__(self, sess):
                super().__init__()
                self.session = sess
                
            def on_open(self):
                print(f"[Session {self.session.session_id}] Speech synthesis opened")

            def on_complete(self):
                print(f"\n[Session {self.session.session_id}] Speech synthesis completed")

            def on_error(self, message: str):
                print(f'[Session {self.session.session_id}] Speech synthesis failed: {message}')

            def on_close(self):
                print(f"[Session {self.session.session_id}] Speech synthesis closed")

            def on_event(self, message):
                pass

            def on_data(self, data: bytes) -> None:
                # 关键：检查这个 session 的状态，不是全局状态
                if not self.session.stop_event.is_set() and self.session.player:
                    try:
                        self.session.player.write(data)
                    except Exception as e:
                        print(f"[Session {self.session.session_id}] Error writing audio: {e}")

        # 初始化 synthesizer
        session.synthesizer = SpeechSynthesizer(
            model="cosyvoice-v2",
            voice="longhua_v2",
            callback=Callback(session),
        )
        print(f"[Session {session.session_id}] Synthesizer created")

        # 检查是否已经被取消
        if session.stop_event.is_set():
            print(f"[Session {session.session_id}] Cancelled after synthesizer init")
            return

        # 准备消息
        messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": session.query_text},
        ]

        # 调用 LLM
        print(f"[Session {session.session_id}] Calling LLM...")
        responses = Generation.call(
            model="qwen-plus",
            messages=messages,
            result_format="message",
            stream=True,
            incremental_output=True,
        )

        # 流式处理响应
        for response in responses:
            # 检查停止标志
            if session.stop_event.is_set():
                print(f"\n[Session {session.session_id}] Stop requested, breaking immediately")
                break
                
            if response.status_code == HTTPStatus.OK:
                llm_text_chunk = response.output.choices[0]["message"]["content"]
                print(llm_text_chunk, end="", flush=True)
                
                # 在发送到 synthesizer 前再次检查
                if session.stop_event.is_set():
                    print(f"\n[Session {session.session_id}] Stop detected, skipping synthesis")
                    break
                
                # 只有在未停止时才继续合成
                if session.synthesizer and not session.stop_event.is_set():
                    try:
                        session.synthesizer.streaming_call(llm_text_chunk)
                    except Exception as e:
                        print(f"\n[Session {session.session_id}] Error in streaming synthesis: {e}")
                        break
            else:
                print(f"\n[Session {session.session_id}] LLM Error: {response.message}")
                break

        # 只有在正常完成时才调用 streaming_complete
        if session.synthesizer and not session.stop_event.is_set():
            print(f"\n[Session {session.session_id}] Completing synthesis...")
            session.synthesizer.streaming_complete()
            
    except Exception as e:
        print(f"[Session {session.session_id}] Error in run_speech: {e}")
    finally:
        # 清理资源
        print(f"[Session {session.session_id}] Cleaning up resources...")
        
        # 如果被强制停止，不要调用 streaming_complete
        if session.synthesizer and not session.stop_event.is_set():
            try:
                session.synthesizer.streaming_complete()
            except:
                pass
        
        # 清理 player（如果还没被清理）
        if session.player:
            try:
                session.player.stop()
            except:
                pass
                
        print(f"[Session {session.session_id}] Speech session ended")


# ---------- API Endpoints ----------
# 用于生成唯一的会话ID
session_counter = 0
session_counter_lock = threading.Lock()

def get_next_session_id():
    global session_counter
    with session_counter_lock:
        session_counter += 1
        return session_counter


@app.post("/start")
def start_speech(query: Query):
    """Start a new speech synthesis session"""
    global current_session
    
    if not query.text or not query.text.strip():
        raise HTTPException(status_code=400, detail="Query text cannot be empty")
    
    try:
        with state_lock:
            # 停止之前的会话
            if current_session:
                print(f"\n=== Stopping previous session {current_session.session_id} ===")
                current_session.stop()
                
                # 给线程短暂时间退出
                if current_session.thread and current_session.thread.is_alive():
                    current_session.thread.join(timeout=0.5)
                
                current_session = None
            
            # 生成新的会话ID
            session_id = get_next_session_id()
            
            # 创建新会话
            print(f"\n{'='*50}")
            print(f"Starting new session {session_id}")
            print(f"Query: {query.text}")
            print(f"{'='*50}")
            
            current_session = SpeechSession(session_id, query.text)
            
            # 启动会话线程
            current_session.thread = threading.Thread(
                target=run_speech, 
                args=(current_session,),
                daemon=False
            )
            current_session.thread.start()

            return {
                "status": "started", 
                "session_id": session_id,
                "query": query.text,
                "message": "Speech synthesis started"
            }
            
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Failed to start speech: {str(e)}"
            }
        )


@app.post("/stop")
def stop_speech():
    """Stop the current speech synthesis session - immediate mode"""
    global current_session

    try:
        print("\n=== STOP REQUEST RECEIVED ===")
        
        if current_session:
            current_session.stop()
            
            # 不等待线程，让它自己退出
            if current_session.thread and current_session.thread.is_alive():
                print("Speech thread will exit on its own")
            
            current_session = None
        else:
            print("No active session to stop")
        
        print("=== STOP COMPLETED (immediate return) ===\n")
        
        return {
            "status": "stopped",
            "message": "Speech synthesis stopped immediately"
        }
            
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Failed to stop speech: {str(e)}"
            }
        )


@app.get("/status")
def get_status():
    """Get the current status of the speech synthesis"""
    if current_session:
        is_active = current_session.thread and current_session.thread.is_alive()
        return {
            "active": is_active,
            "session_id": current_session.session_id,
            "stop_requested": current_session.stop_event.is_set(),
            "has_player": current_session.player is not None,
            "has_synthesizer": current_session.synthesizer is not None
        }
    else:
        return {
            "active": False,
            "session_id": None
        }


@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "voice-ai-assistant"
    }


if __name__ == '__main__':
    print("Starting Voice AI Assistant Server...")
    print(f"API Key configured: {'Yes' if dashscope.api_key else 'No'}")
    
    uvicorn.run(
        app, 
        host="127.0.0.1", 
        port=8001,
        log_level="info"
    )