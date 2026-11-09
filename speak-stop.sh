#!/bin/bash

# Stop currently playing or generating TTS audio cleanly
TEMP_DIR="${TMPDIR:-/tmp}"
USER_UID="$(id -u)"
if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "${XDG_RUNTIME_DIR}" ] && [ -w "${XDG_RUNTIME_DIR}" ]; then
    RUNTIME_DIR="${XDG_RUNTIME_DIR}/speak-aloud-${USER_UID}"
else
    RUNTIME_DIR="${TEMP_DIR}/speak-aloud-${USER_UID}"
fi
mkdir -p "$RUNTIME_DIR" 2>/dev/null || true
chmod 700 "$RUNTIME_DIR" 2>/dev/null || true

SOCKET="$RUNTIME_DIR/mpv.sock"
[ -S "$SOCKET" ] || SOCKET="$TEMP_DIR/speak-aloud-mpv.sock"
LOCK_FILE="$RUNTIME_DIR/lock"
INTERRUPT_FLAG="$RUNTIME_DIR/interrupt"
STATUS_FILE="$RUNTIME_DIR/status"

touch "$INTERRUPT_FLAG"

if [ -S "$SOCKET" ]; then
    echo '{"command":["quit"]}' | socat - "$SOCKET" 2>/dev/null >/dev/null
fi

# Also terminate any in-flight speak.sh generation if lock is held or PID recorded
if [ -f "$RUNTIME_DIR/pid" ]; then
    RECORDED_PID=$(cat "$RUNTIME_DIR/pid" 2>/dev/null)
    if [ -n "$RECORDED_PID" ] && [ "$RECORDED_PID" != "$$" ]; then
        kill -TERM "$RECORDED_PID" 2>/dev/null || true
    fi
fi
if command -v lsof &>/dev/null && [ -f "$LOCK_FILE" ]; then
    for pid in $(lsof -t "$LOCK_FILE" 2>/dev/null); do
        if [ "$pid" != "$$" ]; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
fi

echo "IDLE" > "$STATUS_FILE"
