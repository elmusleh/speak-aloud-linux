#!/bin/bash

# Toggle pause/resume for currently playing TTS audio
TEMP_DIR="${TMPDIR:-/tmp}"
USER_UID="$(id -u)"
if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "${XDG_RUNTIME_DIR}" ] && [ -w "${XDG_RUNTIME_DIR}" ]; then
    RUNTIME_DIR="${XDG_RUNTIME_DIR}/speak-aloud-${USER_UID}"
else
    RUNTIME_DIR="${TEMP_DIR}/speak-aloud-${USER_UID}"
fi

SOCKET="$RUNTIME_DIR/mpv.sock"
[ -S "$SOCKET" ] || SOCKET="$TEMP_DIR/speak-aloud-mpv.sock"
STATUS_FILE="$RUNTIME_DIR/status"
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
