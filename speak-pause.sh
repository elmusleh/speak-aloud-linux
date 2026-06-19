#!/bin/bash

# Toggle pause/resume for currently playing TTS audio
# Use system temp directory for portability
TEMP_DIR="${TMPDIR:-/tmp}"
SOCKET="$TEMP_DIR/speak-aloud-mpv.sock"
STATUS_FILE="$TEMP_DIR/tts-status"
[ -S "$SOCKET" ] || exit 0

# Toggle pause in mpv
echo '{"command":["cycle","pause"]}' | socat - "$SOCKET"

# Sync status file with mpv's actual pause state so the tray icon updates
sleep 0.1
PAUSED=$(echo '{"command":["get_property","pause"]}' | socat - "$SOCKET" 2>/dev/null | grep -o '"data":true')
if [ -n "$PAUSED" ]; then
    echo "PAUSED" > "$STATUS_FILE"
else
    echo "PLAYING" > "$STATUS_FILE"
fi
