#!/bin/bash
# OpenClaw Voice Bridge — macOS Intel Setup
# Run once: bash ~/openclaw-voice-bridge/setup.sh

set -e
BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BRIDGE_DIR"

echo "=== OpenClaw Voice Bridge Setup ==="
echo "Directory: $BRIDGE_DIR"
echo ""

# 1. Python deps
echo "[1/5] Installing Python dependencies..."
pip3 install sounddevice webrtcvad vosk numpy requests python-telegram-bot 2>&1 | tail -3
pip3 install openai-whisper 2>&1 | tail -2  # Whisper fallback (optional)
pip3 install openwakeword 2>&1 | tail -2    # Wake word detection (optional)
echo "Python deps installed."

# 2. Vosk model (small English, ~40MB)
echo ""
echo "[2/5] Downloading Vosk STT model (small-en, ~40MB)..."
mkdir -p "$BRIDGE_DIR/models"
if [ ! -d "$BRIDGE_DIR/models/vosk-en" ]; then
  curl -L "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" \
       -o /tmp/vosk-model.zip
  unzip -q /tmp/vosk-model.zip -d "$BRIDGE_DIR/models/"
  mv "$BRIDGE_DIR/models/vosk-model-small-en-us-0.15" "$BRIDGE_DIR/models/vosk-en"
  rm /tmp/vosk-model.zip
  echo "Vosk model installed at models/vosk-en"
else
  echo "Vosk model already present."
fi

# 3. Piper TTS binary (macOS x86_64)
echo ""
echo "[3/5] Installing Piper TTS binary..."
mkdir -p "$BRIDGE_DIR/piper"
if [ ! -f "$BRIDGE_DIR/piper/piper" ]; then
  PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_macos_x64.tar.gz"
  curl -L "$PIPER_URL" | tar -xz -C "$BRIDGE_DIR/"
  chmod +x "$BRIDGE_DIR/piper/piper"
  echo "Piper installed at piper/piper"
else
  echo "Piper already present."
fi

# 4. Piper voice (en_US-lessac-medium, ~60MB)
echo ""
echo "[4/5] Downloading Piper voice (en_US-lessac-medium)..."
mkdir -p "$BRIDGE_DIR/voices"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
if [ ! -f "$BRIDGE_DIR/voices/en_US-lessac-medium.onnx" ]; then
  curl -L "$VOICE_BASE/en_US-lessac-medium.onnx" \
       -o "$BRIDGE_DIR/voices/en_US-lessac-medium.onnx"
  curl -L "$VOICE_BASE/en_US-lessac-medium.onnx.json" \
       -o "$BRIDGE_DIR/voices/en_US-lessac-medium.onnx.json"
  echo "Voice installed at voices/en_US-lessac-medium.onnx"
else
  echo "Voice already present."
fi

# 5. Test
echo ""
echo "[5/5] Testing Piper TTS..."
echo "Welcome to OpenClaw Voice Bridge. Piper is working." | \
  "$BRIDGE_DIR/piper/piper" \
    --model "$BRIDGE_DIR/voices/en_US-lessac-medium.onnx" \
    --output_file /tmp/voice-test.wav 2>/dev/null && \
  afplay /tmp/voice-test.wav && echo "TTS test passed!" || \
  echo "TTS test failed — check piper binary and voice model paths."

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit ~/openclaw-voice-bridge/bridge_config.json:"
echo "     - Set telegram_bot_token + telegram_chat_id"
echo "     - Set output_device to 'nearQ-01' (your Bluetooth sunglasses)"
echo "     - Customize wake_routes for your agents"
echo ""
echo "  2. List audio devices:"
echo "     python3 ~/openclaw-voice-bridge/bridge.py --list-devices"
echo ""
echo "  3. Test TTS:"
echo "     python3 ~/openclaw-voice-bridge/bridge.py --test-tts 'Hey, I am ready.'"
echo ""
echo "  4. Start the bridge:"
echo "     python3 ~/openclaw-voice-bridge/bridge.py"
echo ""
echo "  5. Auto-start: launchctl load ~/Library/LaunchAgents/com.markus.voice-bridge.plist"
