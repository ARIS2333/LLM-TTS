import os
import threading
import dotenv
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import asyncio
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback
from .RealtimeMp3Player import RealtimeMp3Player
from fastapi.middleware.cors import CORSMiddleware
import sys
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

dotenv.load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Setup ----------
app = FastAPI(title="Voice AI Assistant API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
state_lock = threading.Lock()
current_session = None

# Load API key
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
if not dashscope.api_key:
    raise ValueError("DASHSCOPE_API_KEY not set in environment")

# ---------- Models ----------
class TTSRequest(BaseModel):
    data: List[str]

class StopResponse(BaseModel):
    status: str
    message: str
    session_id: Optional[int] = None

# ---------- Session ----------
class SpeechSession:
    def __init__(self, session_id: int, texts: List[str], voice: str = "longhua_v2", model: str = "cosyvoice-v2"):
        self.session_id = session_id
        self.query_text = texts
        self.voice = voice
        self.model = model
        self.player = None
        self.synthesizer = None
        self.thread = None
        self.stop_event = threading.Event()
        self.is_stopped = False
        self.synthesis_complete = threading.Event()  # New event to track synthesis completion
        self.all_text_processed = threading.Event()  # New event to track when all text is sent to synthesizer
        self._lock = threading.Lock()

    def stop(self):
        with self._lock:
            if self.is_stopped:
                return
            self.is_stopped = True
            logger.info(f"[Session {self.session_id}] Stopping...")

            # Signal stop
            self.stop_event.set()

            # Stop player
            if self.player:
                try:
                    self.player.force_stop()
                    logger.info(f"[Session {self.session_id}] Player stopped")
                except Exception as e:
                    logger.error(f"[Session {self.session_id}] Error stopping player: {e}")
                self.player = None

            # Clear synthesizer
            if self.synthesizer:
                try:
                    # Add cleanup if available
                    self.synthesizer = None
                except Exception as e:
                    logger.error(f"[Session {self.session_id}] Error clearing synthesizer: {e}")
                logger.info(f"[Session {self.session_id}] Synthesizer cleared")

            # Set the events to unblock any waiting threads
            self.synthesis_complete.set()
            self.all_text_processed.set()

            logger.info(f"[Session {self.session_id}] Stop completed")

# ---------- Worker ----------
def run_speech(session: SpeechSession):
    logger.info(f"\n[Session {session.session_id}] Starting speech generation...")
    
    player_started = False
    try:
        if session.stop_event.is_set():
            logger.info(f"[Session {session.session_id}] Cancelled before start")
            return

        # Start player
        session.player = RealtimeMp3Player(verbose=True)
        session.player.start()
        player_started = True
        logger.info(f"[Session {session.session_id}] Player started")

        # Callback class with completion tracking
        class Callback(ResultCallback):
            def __init__(self, sess: SpeechSession):
                super().__init__()
                self.session = sess
                self.data_received = 0  # Track if we've received any data

            def on_open(self):
                logger.info(f"[Session {self.session.session_id}] Synth opened")

            def on_complete(self):
                logger.info(f"[Session {self.session.session_id}] Synth complete")
                # Signal that synthesis is complete
                self.session.synthesis_complete.set()

            def on_error(self, message: str):
                logger.error(f"[Session {self.session.session_id}] Synth error: {message}")
                # Still set the completion event even on error
                self.session.synthesis_complete.set()

            def on_close(self):
                logger.info(f"[Session {self.session.session_id}] Synth closed")

            def on_data(self,  data: bytes):
                self.data_received += 1
                if not self.session.stop_event.is_set() and self.session.player:
                    try:
                        self.session.player.write(data)
                    except Exception as e:
                        logger.error(f"[Session {self.session.session_id}] Write error: {e}")

        # Create synthesizer
        session.synthesizer = SpeechSynthesizer(
            model=session.model,
            voice=session.voice,
            callback=Callback(session),
        )
        logger.info(f"[Session {session.session_id}] Synthesizer created with model={session.model}, voice={session.voice}")

        # Process text chunks - but don't complete synthesis yet
        for chunk in session.query_text:
            if session.stop_event.is_set():
                logger.info(f"[Session {session.session_id}] Stop requested, breaking")
                break

            if chunk.strip():
                logger.info(f"[Session {session.session_id}] Synthesizing: {chunk[:30]}...")
                try:
                    session.synthesizer.streaming_call(chunk)
                except Exception as e:
                    logger.error(f"[Session {session.session_id}] Error streaming: {e}")
                    break

        # Signal that all text has been processed
        session.all_text_processed.set()

        # Only complete synthesis if not stopped
        if session.synthesizer and not session.stop_event.is_set():
            logger.info(f"[Session {session.session_id}] Completing synthesis...")
            session.synthesizer.streaming_complete()

    except Exception as e:
        logger.error(f"[Session {session.session_id}] Error during synthesis: {e}")
    finally:
        # Wait for synthesis to complete before proceeding
        if not session.stop_event.is_set():
            logger.info(f"[Session {session.session_id}] Waiting for synthesis to complete...")
            session.synthesis_complete.wait(timeout=15.0)  # Wait up to 15 seconds for synthesis
            logger.info(f"[Session {session.session_id}] Synthesis completed or timeout reached")

        # Now wait for player to finish playing any remaining audio
        if session.player and player_started:
            logger.info(f"[Session {session.session_id}] Waiting for player to finish playing buffered audio...")
            
            # Wait for player to finish playing all buffered audio
            start_time = time.time()
            timeout = 20.0  # 20 second timeout
            
            while time.time() - start_time < timeout:
                # Check if player is still playing or has buffered data
                if not session.player.is_playing():
                    logger.info(f"[Session {session.session_id}] Player finished playing")
                    break
                
                # Small delay to allow audio to play
                time.sleep(0.1)
                
                # If we were stopped during this wait, break out
                if session.stop_event.is_set():
                    break
            
            # Additional small delay to ensure everything is finished
            time.sleep(0.2)
            
            logger.info(f"[Session {session.session_id}] Finished waiting for player")
            
            try:
                session.player.stop()
                logger.info(f"[Session {session.session_id}] Player stopped in finally")
            except Exception as e:
                logger.error(f"[Session {session.session_id}] Error stopping player in finally: {e}")
        
        logger.info(f"[Session {session.session_id}] Ended")

# ---------- API ----------
session_counter = 0
session_counter_lock = threading.Lock()

def get_next_session_id():
    global session_counter
    with session_counter_lock:
        session_counter += 1
        return session_counter

@app.post("/speak", response_model=dict)
async def start_speech(query: TTSRequest):
    global current_session
    try:
        with state_lock:
            if current_session:
                logger.info(f"\n=== Stopping previous session {current_session.session_id} ===")
                current_session.stop()
                if current_session.thread and current_session.thread.is_alive():
                    current_session.thread.join(timeout=5.0)  # Increased timeout to allow player to finish
                current_session = None

            session_id = get_next_session_id()
            logger.info(f"\n{'='*50}")
            logger.info(f"Starting new session {session_id}")
            logger.info(f"{'='*50}")

            current_session = SpeechSession(
                session_id, 
                query.data, 
                voice="cosyvoice-v3-prefix-36d6a3f4cbae4cd8bd3664acba2cc891", 
                model="cosyvoice-v3"
            )

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
                "voice": "cosyvoice-v3-prefix-36d6a3f4cbae4cd8bd3664acba2cc891",
                "model": "cosyvoice-v3"
            }

    except Exception as e:
        logger.error(f"Error starting speech: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start: {str(e)}")

@app.post("/stop", response_model=StopResponse)
async def stop_speech():
    global current_session
    try:
        logger.info("\n=== STOP REQUEST RECEIVED ===")
        if current_session:
            current_session.stop()
            if current_session.thread and current_session.thread.is_alive():
                current_session.thread.join(timeout=5.0)  # Increased timeout for proper cleanup
                if current_session.thread.is_alive():
                    logger.warning("Speech thread did not stop within timeout")
            current_session = None
            logger.info("Current session stopped and cleared")
        else:
            logger.info("No active session to stop")

        logger.info("=== STOP COMPLETED ===\n")
        return StopResponse(
            status="stopped", 
            message="Stopped successfully",
            session_id=current_session.session_id if current_session else None
        )

    except Exception as e:
        logger.error(f"Error stopping speech: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to stop: {str(e)}")

@app.get("/status", response_model=dict)
async def get_status():
    if current_session:
        player_status = current_session.player.get_buffer_status() if current_session.player else {}
        return {
            "active": current_session.thread and current_session.thread.is_alive(),
            "session_id": current_session.session_id,
            "stop_requested": current_session.stop_event.is_set(),
            "has_player": current_session.player is not None,
            "has_synthesizer": current_session.synthesizer is not None,
            "synthesis_complete": current_session.synthesis_complete.is_set(),
            "all_text_processed": current_session.all_text_processed.is_set(),
            "player_status": player_status,
            "voice": current_session.voice,
            "model": current_session.model
        }
    else:
        return {"active": False, "session_id": None}

@app.get("/health", response_model=dict)
async def health_check():
    return {
        "status": "healthy", 
        "service": "voice-ai-assistant",
        "api_key_configured": bool(dashscope.api_key)
    }

# Graceful shutdown
@app.on_event("shutdown")
async def shutdown_event():
    global current_session
    if current_session:
        logger.info("Shutting down current session...")
        current_session.stop()
        if current_session.thread and current_session.thread.is_alive():
            current_session.thread.join(timeout=5.0)

if __name__ == "__main__":
    logger.info("Starting Voice AI Assistant Server...")
    logger.info(f"API Key configured: {'Yes' if dashscope.api_key else 'No'}")
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
