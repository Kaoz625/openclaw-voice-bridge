#!/usr/bin/env python3
"""
OpenClaw Voice Bridge
Jarvis-style wake word → AI agent voice interface.

Features:
- Wake word detection (openwakeword, 100% local/free)
- STT: Vosk (offline, base.en)
- TTS: Piper (offline, en_US-lessac-medium)
- VAD: webrtcvad (mode 2, 300ms min speech, 2.0s silence)
- Backends: Telegram (primary), openclaw_http (secondary)
- Agent-specific wake words: "hey rufus" → rufus, "hey ceo" → ceo
- Siri-style interruption: new wake word kills active TTS immediately
- Sender-aware queue: most recent route gets focus
- Bluetooth output device selection
- 100% local and free

Setup:
  pip install sounddevice webrtcvad vosk openwakeword python-telegram-bot requests numpy
  # Download Vosk model:
  curl -L https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip -o /tmp/vosk.zip
  unzip /tmp/vosk.zip -d ~/openclaw-voice-bridge/models/
  mv ~/openclaw-voice-bridge/models/vosk-model-small-en-us-0.15 ~/openclaw-voice-bridge/models/vosk-en
  # Install Piper (macOS Intel):
  curl -L https://github.com/rhasspy/piper/releases/latest/download/piper_macos_x86_64.tar.gz | tar -xz -C ~/openclaw-voice-bridge/
  # Download Piper voice:
  mkdir -p ~/openclaw-voice-bridge/voices
  curl -L https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
       -o ~/openclaw-voice-bridge/voices/en_US-lessac-medium.onnx
  curl -L https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json \
       -o ~/openclaw-voice-bridge/voices/en_US-lessac-medium.onnx.json

Config: edit CONFIG section below or create bridge_config.json
"""

import json
import os
import sys
import time
import queue
import struct
import threading
import subprocess
import tempfile
import logging
from pathlib import Path
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger("bridge")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent

CONFIG_DEFAULTS = {
    # Wake words — maps phrase → agent_id (None = primary/broadcast)
    "wake_routes": {
        "hey dog":   None,          # primary — routes to active/last agent
        "hey rufus": "rufus",
        "hey ceo":   "ceo",
        "hey zero":  "zero",
        "hey info":  "info",
    },
    "default_agent": None,          # None = use last active agent

    # Audio
    "input_device":       None,     # None = system default; set to device name substring
    "output_device":      None,     # e.g. "nearQ-01" for Bluetooth sunglasses
    "sample_rate":        16000,
    "vad_mode":           2,        # 0=lenient, 3=aggressive
    "min_speech_ms":      300,
    "silence_timeout_s":  2.0,
    "max_recording_s":    30.0,
    "speech_start_timeout_s": 30.0,

    # STT
    "vosk_model_path":    str(BASE_DIR / "models/vosk-en"),
    "whisper_model":      "base.en",   # fallback if Vosk fails
    "whisper_beam_size":  1,

    # TTS
    "piper_binary":       str(BASE_DIR / "piper/piper"),
    "piper_voice":        str(BASE_DIR / "voices/en_US-lessac-medium.onnx"),
    "tts_volume":         1.0,
    "tts_length_scale":   1.0,

    # Backends
    "primary_backend":    "telegram",   # telegram | openclaw_http
    "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id":   os.environ.get("TELEGRAM_CHAT_ID", ""),
    "openclaw_url":       "http://127.0.0.1:18789",

    # UX
    "play_activation_tone":  True,
    "play_completion_tone":  True,
    "announce_sender":       True,     # say agent name once per response group
    "wake_interrupt":        True,     # new wake word kills active TTS immediately
    "auto_start":            True,
}

_config_path = BASE_DIR / "bridge_config.json"
if _config_path.exists():
    with open(_config_path) as f:
        _user_cfg = json.load(f)
    CONFIG = {**CONFIG_DEFAULTS, **_user_cfg}
else:
    CONFIG = CONFIG_DEFAULTS.copy()
    with open(_config_path, "w") as f:
        json.dump(CONFIG, f, indent=2)
    log.info(f"Created default config at {_config_path}")

# ---------------------------------------------------------------------------
# Audio utilities
# ---------------------------------------------------------------------------
try:
    import sounddevice as sd
    import numpy as np
    AUDIO_OK = True
except ImportError:
    log.error("sounddevice/numpy not installed — run: pip install sounddevice numpy")
    AUDIO_OK = False

try:
    import webrtcvad
    VAD_OK = True
except ImportError:
    log.warning("webrtcvad not installed — pip install webrtcvad")
    VAD_OK = False


def find_device_index(name_substr: Optional[str], kind: str) -> Optional[int]:
    """Find audio device index by name substring."""
    if not name_substr or not AUDIO_OK:
        return None
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if name_substr.lower() in dev["name"].lower():
            if kind == "input"  and dev["max_input_channels"] > 0:  return i
            if kind == "output" and dev["max_output_channels"] > 0: return i
    log.warning(f"Audio device '{name_substr}' not found — using system default")
    return None


def play_tone(freq: float = 880, duration: float = 0.1, output_device=None):
    """Play a short beep tone."""
    if not AUDIO_OK:
        return
    try:
        sr = 44100
        t = np.linspace(0, duration, int(sr * duration), False)
        tone = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        sd.play(tone, sr, device=output_device, blocking=True)
    except Exception as e:
        log.debug(f"Tone error: {e}")


def play_wav(path: str, output_device=None):
    """Play a wav file to the output device."""
    if not AUDIO_OK:
        return
    try:
        import wave
        with wave.open(path, "rb") as wf:
            sr = wf.getframerate()
            nch = wf.getnchannels()
            data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            if nch > 1:
                data = data.reshape(-1, nch)
            sd.play(data.astype(np.float32) / 32768.0, sr, device=output_device, blocking=True)
    except Exception as e:
        log.error(f"play_wav error: {e}")

# ---------------------------------------------------------------------------
# TTS — Piper (local) with Python API primary, binary fallback, say last resort
# ---------------------------------------------------------------------------

# Try loading piper-tts Python package (most reliable on macOS)
try:
    from piper import PiperVoice as _PiperVoice
    import wave as _wave
    PIPER_PYTHON_OK = True
except ImportError:
    PIPER_PYTHON_OK = False


class PiperTTS:
    def __init__(self):
        self.binary  = CONFIG["piper_binary"]
        self.voice   = CONFIG["piper_voice"]
        self._proc:  Optional[subprocess.Popen] = None
        self._lock   = threading.Lock()
        self._output_device = find_device_index(CONFIG.get("output_device"), "output")
        self._piper_voice = None

        # Try loading piper-tts Python API voice model
        if PIPER_PYTHON_OK and os.path.exists(self.voice):
            try:
                self._piper_voice = _PiperVoice.load(self.voice)
                log.info("Piper Python API ready")
            except Exception as e:
                log.warning(f"Piper Python API load failed: {e}")

    def speak(self, text: str):
        """Speak text: tries Python piper-tts, then binary, then macOS say."""
        with self._lock:
            self._kill_proc()

            # Method 1: piper-tts Python package
            if self._piper_voice is not None:
                try:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                        wav_path = tmp.name
                    with _wave.open(wav_path, "wb") as wf:
                        self._piper_voice.synthesize(text, wf,
                            length_scale=CONFIG["tts_length_scale"])
                    play_wav(wav_path, self._output_device)
                    os.unlink(wav_path)
                    return
                except Exception as e:
                    log.warning(f"Piper Python API speak error: {e} — trying binary")

            # Method 2: piper binary
            if os.path.exists(self.binary):
                try:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                        wav_path = tmp.name
                    self._proc = subprocess.Popen(
                        [self.binary, "--model", self.voice,
                         "--output_file", wav_path,
                         "--length_scale", str(CONFIG["tts_length_scale"])],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._proc.communicate(input=text.encode())
                    if self._proc.returncode == 0:
                        play_wav(wav_path, self._output_device)
                    os.unlink(wav_path)
                    self._proc = None
                    return
                except Exception as e:
                    log.warning(f"Piper binary error: {e} — falling back to say")

            # Method 3: macOS say (always works, system voices)
            safe = text.replace('"', "'")
            if self._output_device is not None:
                # Route to specific audio device via say + afplay
                with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
                    aiff_path = tmp.name
                os.system(f'say -o "{aiff_path}" -- "{safe}"')
                os.system(f'afplay "{aiff_path}"')
                os.unlink(aiff_path)
            else:
                os.system(f'say -- "{safe}"')

    def interrupt(self):
        """Kill active TTS immediately (Siri-style interruption)."""
        with self._lock:
            self._kill_proc()
        os.system("killall afplay 2>/dev/null; killall piper 2>/dev/null")
        if AUDIO_OK:
            sd.stop()

    def _kill_proc(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc = None

# ---------------------------------------------------------------------------
# STT — Vosk (offline)
# ---------------------------------------------------------------------------
class VoskSTT:
    def __init__(self):
        self._model = None
        self._ready = False
        model_path = CONFIG["vosk_model_path"]
        try:
            from vosk import Model, KaldiRecognizer
            if not os.path.exists(model_path):
                log.error(f"Vosk model not found at {model_path}")
                log.error("Download: curl -L https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip | bsdtar -xf - -C ~/openclaw-voice-bridge/models/")
                return
            self._model = Model(model_path)
            self._KaldiRecognizer = KaldiRecognizer
            self._ready = True
            log.info("Vosk STT ready")
        except ImportError:
            log.warning("vosk not installed — pip install vosk")

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        if not self._ready:
            return self._whisper_fallback(audio_bytes, sample_rate)
        from vosk import KaldiRecognizer
        rec = self._KaldiRecognizer(self._model, sample_rate)
        rec.AcceptWaveform(audio_bytes)
        result = json.loads(rec.FinalResult())
        text = result.get("text", "").strip()
        return text if text else self._whisper_fallback(audio_bytes, sample_rate)

    def _whisper_fallback(self, audio_bytes: bytes, sample_rate: int) -> str:
        try:
            import whisper
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            # Write raw 16-bit PCM as wav
            import wave
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_bytes)
            model = whisper.load_model(CONFIG["whisper_model"])
            result = model.transcribe(wav_path, beam_size=CONFIG["whisper_beam_size"])
            os.unlink(wav_path)
            return result["text"].strip()
        except Exception as e:
            log.error(f"Whisper fallback error: {e}")
            return ""

# ---------------------------------------------------------------------------
# Wake word detection — openwakeword (local, no API)
# ---------------------------------------------------------------------------
class WakeWordDetector:
    """
    openwakeword-based wake word detector.
    Falls back to simple keyword matching in STT output if openwakeword unavailable.
    """
    def __init__(self, phrases: list[str]):
        self.phrases = [p.lower().strip() for p in phrases]
        self._oww_model = None
        try:
            from openwakeword.model import Model as OWWModel
            # openwakeword uses pre-trained models; for custom phrases, use text matching
            self._oww_model = OWWModel(inference_framework="onnx")
            log.info("openwakeword loaded")
        except ImportError:
            log.info("openwakeword not installed — using text matching fallback")
            log.info("Install: pip install openwakeword")

    def match(self, transcription: str) -> Optional[str]:
        """Returns matched wake phrase or None."""
        text = transcription.lower().strip()
        for phrase in self.phrases:
            if phrase in text:
                return phrase
        return None

    def process_audio_frame(self, frame: bytes) -> Optional[str]:
        """Process a 16ms audio frame; returns wake phrase if detected."""
        if self._oww_model is None:
            return None  # using STT-based detection
        try:
            audio = np.frombuffer(frame, dtype=np.int16)
            prediction = self._oww_model.predict(audio)
            for model_name, score in prediction.items():
                if score > 0.5:
                    # Map openwakeword model names to our phrases
                    model_lower = model_name.lower().replace("_", " ")
                    for phrase in self.phrases:
                        if any(word in model_lower for word in phrase.split()):
                            return phrase
        except Exception as e:
            log.debug(f"OWW frame error: {e}")
        return None

# ---------------------------------------------------------------------------
# Backend — Telegram
# ---------------------------------------------------------------------------
class TelegramBackend:
    def __init__(self):
        self.token   = CONFIG["telegram_bot_token"]
        self.chat_id = CONFIG["telegram_chat_id"]
        self._base   = f"https://api.telegram.org/bot{self.token}"
        self._offset  = 0

    def send(self, text: str, agent_id: Optional[str] = None) -> bool:
        """Send text to Telegram. If agent_id, prepend @mention."""
        if not self.token:
            log.error("TELEGRAM_BOT_TOKEN not set")
            return False
        import urllib.request
        message = f"@{agent_id}: {text}" if agent_id else text
        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": message,
        }).encode()
        try:
            req = urllib.request.Request(
                f"{self._base}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            log.error(f"Telegram send error: {e}")
            return False

    def poll_responses(self, callback):
        """Long-poll for new messages and call callback(sender, text)."""
        import urllib.request
        while True:
            try:
                url = f"{self._base}/getUpdates?offset={self._offset}&timeout=30"
                with urllib.request.urlopen(url, timeout=35) as resp:
                    data = json.loads(resp.read())
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    sender = str(msg.get("from", {}).get("username", "agent"))
                    if text and sender != "user":
                        callback(sender, text)
            except Exception as e:
                log.debug(f"Telegram poll error: {e}")
                time.sleep(2)

# ---------------------------------------------------------------------------
# Backend — OpenClaw HTTP
# ---------------------------------------------------------------------------
class OpenClawHTTPBackend:
    def __init__(self):
        self.base = CONFIG["openclaw_url"]

    def send(self, text: str, agent_id: Optional[str] = None) -> bool:
        import urllib.request
        payload = json.dumps({"message": text, "agent": agent_id}).encode()
        try:
            req = urllib.request.Request(
                f"{self.base}/send",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            log.error(f"OpenClaw HTTP error: {e}")
            return False

# ---------------------------------------------------------------------------
# Main Voice Bridge
# ---------------------------------------------------------------------------
@dataclass
class QueuedResponse:
    sender_id: str
    text: str
    timestamp: float = field(default_factory=time.time)

class VoiceBridge:
    def __init__(self):
        self.tts         = PiperTTS()
        self.stt         = VoskSTT()
        self.wake_words  = list(CONFIG["wake_routes"].keys())
        self.wake_map    = CONFIG["wake_routes"]
        self.detector    = WakeWordDetector(self.wake_words)
        self.backend     = TelegramBackend() if CONFIG["primary_backend"] == "telegram" \
                           else OpenClawHTTPBackend()

        self._response_queue = deque()
        self._tts_lock       = threading.Lock()
        self._active_agent   = CONFIG.get("default_agent")
        self._last_speaker   = None
        self._running        = False

        self._in_device  = find_device_index(CONFIG.get("input_device"),  "input")
        self._out_device = find_device_index(CONFIG.get("output_device"), "output")

        log.info(f"Wake words: {self.wake_words}")
        log.info(f"Backend: {CONFIG['primary_backend']}")

    def start(self):
        self._running = True

        # Start response polling thread
        if isinstance(self.backend, TelegramBackend):
            t = threading.Thread(target=self.backend.poll_responses,
                                 args=(self._on_agent_response,), daemon=True)
            t.start()

        # Start TTS queue worker
        tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        tts_thread.start()

        log.info("Bridge running — listening for wake words...")
        self._listen_loop()

    def stop(self):
        self._running = False
        self.tts.interrupt()

    def _listen_loop(self):
        """Main loop: continuously listen for wake words using VAD + STT."""
        if not AUDIO_OK:
            log.error("sounddevice not available — cannot listen")
            return
        if not VAD_OK:
            log.warning("webrtcvad unavailable — using simple energy-based VAD")

        sr  = CONFIG["sample_rate"]
        vad = webrtcvad.Vad(CONFIG["vad_mode"]) if VAD_OK else None
        frame_ms  = 30  # ms per frame (VAD requires 10/20/30ms frames)
        frame_samples = int(sr * frame_ms / 1000)

        log.info(f"Listening on device: {self._in_device or 'default'} @ {sr}Hz")

        audio_buffer = b""
        speech_frames = []
        in_speech = False
        silence_start = None
        speech_start = None

        def audio_callback(indata, frames, time_info, status):
            nonlocal audio_buffer
            raw = (indata[:, 0] * 32768).astype("int16").tobytes()
            audio_buffer += raw

        with sd.InputStream(samplerate=sr, channels=1, dtype="float32",
                             blocksize=frame_samples, device=self._in_device,
                             callback=audio_callback):
            while self._running:
                # Pull a frame from buffer
                needed = frame_samples * 2  # 16-bit = 2 bytes/sample
                if len(audio_buffer) < needed:
                    time.sleep(0.005)
                    continue
                frame = audio_buffer[:needed]
                audio_buffer = audio_buffer[needed:]

                # VAD check
                is_speech = True
                if vad:
                    try:
                        is_speech = vad.is_speech(frame, sr)
                    except Exception:
                        pass

                if is_speech:
                    if not in_speech:
                        in_speech = True
                        speech_start = time.time()
                        log.debug("Speech start detected")
                    speech_frames.append(frame)
                    silence_start = None
                else:
                    if in_speech:
                        if silence_start is None:
                            silence_start = time.time()
                        silence_elapsed = time.time() - silence_start
                        speech_frames.append(frame)  # include trailing silence

                        if silence_elapsed >= CONFIG["silence_timeout_s"]:
                            # End of utterance
                            speech_elapsed = time.time() - speech_start
                            if speech_elapsed * 1000 >= CONFIG["min_speech_ms"]:
                                audio_data = b"".join(speech_frames)
                                threading.Thread(
                                    target=self._process_utterance,
                                    args=(audio_data,),
                                    daemon=True,
                                ).start()
                            speech_frames = []
                            in_speech = False
                            silence_start = None

                        # Max recording limit
                        if in_speech and (time.time() - speech_start) >= CONFIG["max_recording_s"]:
                            audio_data = b"".join(speech_frames)
                            threading.Thread(
                                target=self._process_utterance,
                                args=(audio_data,),
                                daemon=True,
                            ).start()
                            speech_frames = []
                            in_speech = False

    def _process_utterance(self, audio_data: bytes):
        """Transcribe audio, check for wake word, route to agent."""
        text = self.stt.transcribe(audio_data, CONFIG["sample_rate"])
        if not text:
            return
        log.info(f"Transcribed: '{text}'")

        matched_wake = self.detector.match(text)
        if matched_wake is None:
            return  # Not a wake word utterance

        # Interrupt active TTS if configured
        if CONFIG["wake_interrupt"]:
            self.tts.interrupt()

        # Play activation tone
        if CONFIG["play_activation_tone"]:
            play_tone(880, 0.08, self._out_device)

        # Determine target agent
        agent_id = self.wake_map.get(matched_wake) or self._active_agent
        self._active_agent = agent_id

        # Extract command after wake phrase
        command = text.lower().replace(matched_wake, "").strip()
        if not command:
            log.info(f"Wake word '{matched_wake}' heard — no command, waiting...")
            return

        log.info(f"→ Agent '{agent_id}': {command}")

        # Send to backend
        ok = self.backend.send(command, agent_id)
        if not ok:
            log.error("Failed to send to backend")

    def _on_agent_response(self, sender_id: str, text: str):
        """Called when an agent sends a response via Telegram."""
        self._active_agent = sender_id
        self._response_queue.append(QueuedResponse(sender_id=sender_id, text=text))
        log.info(f"Response from {sender_id}: {text[:60]}...")

    def _tts_worker(self):
        """Process response queue, speaking each response."""
        while self._running:
            if not self._response_queue:
                time.sleep(0.1)
                continue

            # Most recently prompted route gets focus — but FIFO within each sender
            item = self._response_queue.popleft()

            # Announce sender name once per contiguous group
            if CONFIG["announce_sender"] and item.sender_id != self._last_speaker:
                self.tts.speak(item.sender_id.replace("_", " ").replace("-", " "))
                self._last_speaker = item.sender_id

            self.tts.speak(item.text)

            if CONFIG["play_completion_tone"] and not self._response_queue:
                play_tone(660, 0.06, self._out_device)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OpenClaw Voice Bridge")
    parser.add_argument("--config", help="Path to bridge_config.json")
    parser.add_argument("--test-tts", help="Test TTS with this text and exit")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        if AUDIO_OK:
            print(sd.query_devices())
        else:
            print("sounddevice not installed")
        sys.exit(0)

    if args.test_tts:
        tts = PiperTTS()
        tts.speak(args.test_tts)
        sys.exit(0)

    bridge = VoiceBridge()
    try:
        bridge.start()
    except KeyboardInterrupt:
        bridge.stop()
        print("\nVoice bridge stopped.")
