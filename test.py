
import os
import threading
import dotenv
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List

import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback

from RealtimeMp3Player import RealtimeMp3Player

dotenv.load_dotenv()

# ---------- Setup ----------
app = FastAPI()

# Global state
state_lock = threading.Lock()
current_session = None

# Load API key
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
if not dashscope.api_key:
    raise ValueError("DASHSCOPE_API_KEY not set in environment")

# ---------- Models ----------
class TTSRequest(BaseModel):
    data: List[str]   # llm’s streamed output chunks


# ---------- Session ----------
class SpeechSession:
    def __init__(self, session_id: int, texts: List[str]):
        self.session_id = session_id
        self.query_text = texts
        self.player = None
        self.synthesizer = None
        self.thread = None
        self.stop_event = threading.Event()
        self.is_stopped = False

    def stop(self):
        if self.is_stopped:
            return
        self.is_stopped = True
        print(f"[Session {self.session_id}] Stopping...")

        # signal stop
        self.stop_event.set()

        # stop player
        if self.player:
            try:
                self.player.force_stop()
                print(f"[Session {self.session_id}] Player stopped")
            except Exception as e:
                print(f"[Session {self.session_id}] Error stopping player: {e}")
            self.player = None

        # clear synthesizer
        if self.synthesizer:
            self.synthesizer = None
            print(f"[Session {self.session_id}] Synthesizer cleared")

        print(f"[Session {self.session_id}] Stop completed")


# ---------- Worker ----------
def run_speech(session: SpeechSession):
    print(f"\n[Session {session.session_id}] Starting speech generation...")

    try:
        if session.stop_event.is_set():
            print(f"[Session {session.session_id}] Cancelled before start")
            return

        # start player
        session.player = RealtimeMp3Player(verbose=True)
        session.player.start()
        print(f"[Session {session.session_id}] Player started")

        # callback
        class Callback(ResultCallback):
            def __init__(self, sess: SpeechSession):
                super().__init__()
                self.session = sess

            def on_open(self):
                print(f"[Session {self.session.session_id}] Synth opened")

            def on_complete(self):
                print(f"[Session {self.session.session_id}] Synth complete")

            def on_error(self, message: str):
                print(f"[Session {self.session.session_id}] Synth error: {message}")

            def on_close(self):
                print(f"[Session {self.session.session_id}] Synth closed")

            def on_data(self, data: bytes):
                if not self.session.stop_event.is_set() and self.session.player:
                    try:
                        self.session.player.write(data)
                    except Exception as e:
                        print(f"[Session {self.session.session_id}] Write error: {e}")

        session.synthesizer = SpeechSynthesizer(
            model="cosyvoice-v2",
            voice="longhua_v2",
            callback=Callback(session),
        )
        print(f"[Session {session.session_id}] Synthesizer created")

        # loop over incoming text chunks
        for chunk in session.query_text:
            if session.stop_event.is_set():
                print(f"[Session {session.session_id}] Stop requested, break")
                break

            if chunk.strip():
                print(f"[Session {session.session_id}] Synthesizing: {chunk[:30]}...")
                try:
                    session.synthesizer.streaming_call(chunk)
                except Exception as e:
                    print(f"[Session {session.session_id}] Error streaming: {e}")
                    break

        # complete if not stopped
        if session.synthesizer and not session.stop_event.is_set():
            print(f"[Session {session.session_id}] Completing synthesis...")
            session.synthesizer.streaming_complete()

    except Exception as e:
        print(f"[Session {session.session_id}] Error: {e}")
    finally:
        if session.player:
            try:
                session.player.stop()
            except:
                pass
        print(f"[Session {session.session_id}] Ended")


# ---------- API ----------
session_counter = 0
session_counter_lock = threading.Lock()

def get_next_session_id():
    global session_counter
    with session_counter_lock:
        session_counter += 1
        return session_counter


@app.post("/start")
def start_speech(query: TTSRequest):
    global current_session
    try:
        with state_lock:
            if current_session:
                print(f"\n=== Stopping previous session {current_session.session_id} ===")
                current_session.stop()
                if current_session.thread and current_session.thread.is_alive():
                    current_session.thread.join(timeout=0.5)
                current_session = None

            session_id = get_next_session_id()
            print(f"\n{'='*50}")
            print(f"Starting new session {session_id}")
            print(f"{'='*50}")

            current_session = SpeechSession(session_id, query.data)

            current_session.thread = threading.Thread(
                target=run_speech,
                args=(current_session,),
                daemon=False,
            )
            current_session.thread.start()

            return {
                "status": "started",
                "session_id": session_id,
                "message": "Speech synthesis started",
            }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Failed to start: {str(e)}"},
        )


@app.post("/stop")
def stop_speech():
    global current_session
    try:
        print("\n=== STOP REQUEST RECEIVED ===")
        if current_session:
            current_session.stop()
            if current_session.thread and current_session.thread.is_alive():
                print("Speech thread will exit on its own")
            current_session = None
        else:
            print("No active session to stop")

        print("=== STOP COMPLETED ===\n")
        return {"status": "stopped", "message": "Stopped immediately"}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Failed to stop: {str(e)}"},
        )


@app.get("/status")
def get_status():
    if current_session:
        return {
            "active": current_session.thread and current_session.thread.is_alive(),
            "session_id": current_session.session_id,
            "stop_requested": current_session.stop_event.is_set(),
            "has_player": current_session.player is not None,
            "has_synthesizer": current_session.synthesizer is not None,
        }
    else:
        return {"active": False, "session_id": None}


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "voice-ai-assistant"}


if __name__ == "__main__":
    print("Starting Voice AI Assistant Server...")
    print(f"API Key configured: {'Yes' if dashscope.api_key else 'No'}")
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
