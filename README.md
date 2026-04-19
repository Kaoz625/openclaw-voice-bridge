# 🎙️ OpenClaw Voice Bridge

Jarvis-style wake word → AI agent voice interface. 100% local, free, and offline.

## Features

- 🔊 **Wake word detection** via `openwakeword` — fully local, no API
- 🗣️ **Speech-to-Text** via `Vosk` (offline, base.en model)
- 📢 **Text-to-Speech** via `Piper` (offline, en_US-lessac-medium)
- 🔇 **Voice Activity Detection** via `webrtcvad`
- 🤖 **Multi-agent routing** — "hey rufus" → rufus agent, "hey ceo" → ceo agent
- ✂️ **Siri-style interruption** — new wake word kills active TTS immediately
- 📱 **Backends** — Telegram (primary) or OpenClaw HTTP (secondary)
- 🎧 **Bluetooth output** device selection support

## Requirements

```bash
pip install sounddevice webrtcvad vosk openwakeword python-telegram-bot requests numpy
```

## Setup

### 1. Download Vosk STT Model
```bash
curl -L https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip -o /tmp/vosk.zip
unzip /tmp/vosk.zip -d ~/openclaw-voice-bridge/models/
mv ~/openclaw-voice-bridge/models/vosk-model-small-en-us-0.15 ~/openclaw-voice-bridge/models/vosk-en
```

### 2. Install Piper TTS (macOS Intel)
```bash
curl -L https://github.com/rhasspy/piper/releases/latest/download/piper_macos_x86_64.tar.gz | tar -xz -C ~/openclaw-voice-bridge/
```

### 3. Download Piper Voice
```bash
mkdir -p ~/openclaw-voice-bridge/voices
curl -L https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
     -o ~/openclaw-voice-bridge/voices/en_US-lessac-medium.onnx
curl -L https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json \
     -o ~/openclaw-voice-bridge/voices/en_US-lessac-medium.onnx.json
```

### 4. Configure
Edit `bridge_config.json` (auto-generated on first run) or set environment variables:
```bash
export TELEGRAM_BOT_TOKEN=your_token_here
export TELEGRAM_CHAT_ID=your_chat_id_here
```

## Usage

```bash
# Start the bridge
python bridge.py

# Test TTS only
python bridge.py --test-tts "Hello, I am ready"

# List audio devices
python bridge.py --list-devices
```

## Wake Words

| Say | Routes to |
|---|---|
| "hey dog" | Last active agent |
| "hey rufus" | rufus agent |
| "hey ceo" | ceo agent |
| "hey zero" | zero agent |
| "hey info" | info agent |

## Configuration

All settings live in `bridge_config.json`. Key options:

| Setting | Default | Description |
|---|---|---|
| `primary_backend` | `telegram` | `telegram` or `openclaw_http` |
| `output_device` | `null` | Bluetooth device name substring |
| `silence_timeout_s` | `2.0` | Seconds of silence before processing |
| `wake_interrupt` | `true` | New wake word kills active TTS |
| `announce_sender` | `true` | Speaks agent name before response |

## License

MIT
