#!/bin/bash

# Stop currently playing or generating TTS audio cleanly
TEMP_DIR="${TMPDIR:-/tmp}"
SOCKET="$TEMP_DIR/speak-aloud-mpv.sock"
LOCK_FILE="$TEMP_DIR/speak-aloud.lock"
INTERRUPT_FLAG="$TEMP_DIR/speak-aloud.interrupt"
STATUS_FILE="$TEMP_DIR/tts-status"

touch "$INTERRUPT_FLAG"

if [ -S "$SOCKET" ]; then
    echo '{"command":["quit"]}' | socat - "$SOCKET" 2>/dev/null >/dev/null
fi

# Also terminate any in-flight speak.sh generation if lock is held
if command -v lsof &>/dev/null && [ -f "$LOCK_FILE" ]; then
    for pid in $(lsof -t "$LOCK_FILE" 2>/dev/null); do
        if [ "$pid" != "$$" ]; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
fi

echo "IDLE" > "$STATUS_FILE"
