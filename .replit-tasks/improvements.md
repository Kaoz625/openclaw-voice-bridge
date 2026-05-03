# Replit Agent Task Spec — openclaw-voice-bridge

## Instructions for Replit Agent
Read this file carefully before touching any code.
Commit all changes with prefix "replit: " and push to main when done.

## Stack Rules
- Python 3.11+
- All local, offline-first
- AI → OpenClaw HTTP endpoint or Telegram (see bridge_config.json)
- Commit and push every change

## Context
OpenClaw Voice Bridge is a Jarvis-style offline voice interface:
- Wake word detection via openwakeword (fully local)
- STT via Vosk (offline base.en model)
- TTS via Piper (offline en_US-lessac-medium)
- Voice Activity Detection via webrtcvad
- Multi-agent routing: "hey rufus" → rufus, "hey ceo" → ceo agent
- Siri-style interruption: new wake word kills active TTS
- Backends: Telegram (primary) or OpenClaw HTTP (secondary)

Key files: bridge.py, bridge_config.json, setup.sh

## Tasks for Replit Agent

### Task 1 — Install dependencies
```bash
pip install sounddevice webrtcvad vosk openwakeword python-telegram-bot requests numpy
```
Also install piper-tts:
```bash
pip install piper-tts
```
Fix any install conflicts.

### Task 2 — Integrate Camofox Browser for Web Search Tool
Camofox-browser (github.com/jo-inc/camofox-browser) is an anti-detect browser
built on Firefox that bypasses bot detection.

Add `tools/web_search.py` — a voice-triggered web search tool:
```python
"""
Wake phrase: "hey rufus, search for <query>"
Uses: Camofox browser (anti-detect) → extract page text → speak summary via Piper TTS
"""
import subprocess, json

def camofox_search(query: str) -> str:
    """Run a web search via camofox and return extracted text."""
    # camofox-browser CLI: camofox --url "https://duckduckgo.com/?q=<query>" --extract-text
    result = subprocess.run(
        ["camofox", "--url", f"https://duckduckgo.com/?q={query.replace(' ', '+')}", "--extract-text"],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout[:1000]  # cap at 1000 chars for TTS
```

Wire it into bridge.py: detect "search for" in transcribed speech → call camofox_search() → speak result.

### Task 3 — Add camofox install to setup.sh
Append to setup.sh:
```bash
# Camofox browser (anti-detect web search)
pip install camofox-browser || echo "Camofox not on PyPI — clone manually: github.com/jo-inc/camofox-browser"
```

### Task 4 — Wake Word Config
Add new wake words to bridge_config.json for the search tool:
```json
{
  "wake_words": ["hey rufus", "hey ceo"],
  "search_trigger": "search for",
  "search_backend": "camofox"
}
```

### Task 5 — README Update
Document camofox integration and new "search for" voice command.

## Do NOT touch
- Existing wake word detection logic in bridge.py
- Vosk STT setup
- Piper TTS configuration
- Telegram backend credentials
