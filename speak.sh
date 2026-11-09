#!/bin/bash

# Add local bin directory and user local bin to PATH for mpv and edge-tts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$SCRIPT_DIR/bin:$HOME/.local/bin:$PATH"

# ── Ensure tray & floating player daemon is running ───────────────────────────
if ! pgrep -f "tts-daemon.py" > /dev/null 2>&1; then
    setsid python3 "$SCRIPT_DIR/tts-daemon.py" > /dev/null 2>&1 < /dev/null &
fi

CONFIG_DIR="$HOME/.config/tts_settings"

# Keep routine status updates in the tray/floating HUD, but surface failures.
notify-send() {
    case "${1:-}" in
        *Error*|*Warning*)
            printf '%s: %s\n' "$1" "${2:-}" >&2
            if command -v notify-send >/dev/null 2>&1; then
                command notify-send "$@" || true
            fi
            ;;
    esac
}

# Use system temp directory for portability
TEMP_DIR="${TMPDIR:-/tmp}"
WORK_DIR="$TEMP_DIR/speak-aloud-work"
TTS_STATUS_FILE="$TEMP_DIR/tts-status"          # polled by tts-daemon.py
QUEUE_FILE="$TEMP_DIR/speak-aloud-queue.json"   # shared playback queue
LOCK_FILE="$TEMP_DIR/speak-aloud.lock"
MPV_SOCKET="$TEMP_DIR/speak-aloud-mpv.sock"
INTERRUPT_FLAG="$TEMP_DIR/speak-aloud.interrupt"

# ── Parse optional CLI args (single clean pass) ───────────────────────────────
OVERRIDE_TEXT=""
OVERRIDE_EN_VOICE=""
OVERRIDE_AR_VOICE=""
OVERRIDE_DE_VOICE=""
OVERRIDE_EN_RATE=""
OVERRIDE_AR_RATE=""
OVERRIDE_DE_RATE=""
OVERRIDE_SPEED=""
OUTPUT_FILE=""
OVERRIDE_SOURCE_LANG=""
OVERRIDE_TRANSLATE_ENABLED=""
OVERRIDE_TRANSLATE_PROVIDER=""
OVERRIDE_TRANSLATE_TARGET=""
OVERRIDE_TRANSLATE_KEY=""
OVERRIDE_PAUSE_PUNCTUATION=""
OVERRIDE_PAUSE_DELAY_MS=""
TTS_MODE="play"   # play | generate | queue

show_help() {
    cat << 'EOF'
Usage: speak.sh [OPTIONS]

Options:
  --text TEXT                Speak explicit text
  --queue                    Add selected (or --text) content to the playback queue
  --generate-only            Generate segment audio files without playing
  --en-voice VOICE           Override English voice
  --ar-voice VOICE           Override Arabic voice
  --de-voice VOICE           Override German voice
  --en-rate RATE             Override English rate (e.g., +50%)
  --ar-rate RATE             Override Arabic rate (e.g., +30%)
  --de-rate RATE             Override German rate (e.g., +30%)
  --speed MULTIPLIER         Playback speed multiplier (e.g., 1.25)
  --output FILE.mp3          Export concatenated audio to file
  --source-lang LANG         Force source language (auto, en, ar, de, ...)
  --translate-enabled yes/no Enable/disable translation
  --translate-provider PROV  Translation provider (deepl, google)
  --translate-target LANG    Target language for translation
  --translate-api-key KEY    API key for translation service
  --pause-punctuation yes/no Enable/disable punctuation pauses
  --pause-delay-ms MS        Pause delay between segments in ms (default 100)
  --help                     Show this help message

Audio file locations:
  Cache folder (30-day persistent): ~/.cache/speak-aloud/
  Temp work folder (segments):      /tmp/speak-aloud-work/
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --text)               OVERRIDE_TEXT="$2";              shift 2 ;;
        --queue)              TTS_MODE="queue";                shift 1 ;;
        --generate-only)      TTS_MODE="generate";             shift 1 ;;
        --en-voice)           OVERRIDE_EN_VOICE="$2";          shift 2 ;;
        --ar-voice)           OVERRIDE_AR_VOICE="$2";          shift 2 ;;
        --de-voice)           OVERRIDE_DE_VOICE="$2";          shift 2 ;;
        --en-rate)            OVERRIDE_EN_RATE="$2";           shift 2 ;;
        --ar-rate)            OVERRIDE_AR_RATE="$2";           shift 2 ;;
        --de-rate)            OVERRIDE_DE_RATE="$2";           shift 2 ;;
        --speed)              OVERRIDE_SPEED="$2";             shift 2 ;;
        --output)             OUTPUT_FILE="$2";                shift 2 ;;
        --source-lang)        OVERRIDE_SOURCE_LANG="$2";       shift 2 ;;
        --translate-enabled)  OVERRIDE_TRANSLATE_ENABLED="$2"; shift 2 ;;
        --translate-provider) OVERRIDE_TRANSLATE_PROVIDER="$2"; shift 2 ;;
        --translate-target)   OVERRIDE_TRANSLATE_TARGET="$2";  shift 2 ;;
        --translate-api-key)  OVERRIDE_TRANSLATE_KEY="$2";     shift 2 ;;
        --pause-punctuation)  OVERRIDE_PAUSE_PUNCTUATION="$2"; shift 2 ;;
        --pause-delay-ms)     OVERRIDE_PAUSE_DELAY_MS="$2";    shift 2 ;;
        --help)               show_help; exit 0 ;;
        *)                    shift ;;
    esac
done

DEBUG_LOG="$TEMP_DIR/speak-debug.log"
debug() { printf '[%s] %s\n' "$(date '+%H:%M:%S.%3N')" "$*" >> "$DEBUG_LOG"; }

# ── Helper to acquire selected text (used by both play and queue modes) ───────
acquire_input_text() {
    local text="$OVERRIDE_TEXT"
    local method=""

    if [ -n "$text" ]; then
        method="override"
    fi

    # Office apps (OnlyOffice, LibreOffice, WPS): use xprop WM_CLASS (fast ~2ms)
    if [ -z "$text" ] && command -v xdotool &>/dev/null && command -v xsel &>/dev/null; then
        local wid win_class=""
        wid=$(xdotool getactivewindow 2>/dev/null || xdotool getwindowfocus 2>/dev/null)
        if [ -n "$wid" ] && [ "$wid" != "0" ] && command -v xprop &>/dev/null; then
            win_class=$(xprop -id "$wid" WM_CLASS 2>/dev/null | tr '[:upper:]' '[:lower:]')
            debug "Active wid=$wid class=$win_class"
        fi

        if echo "$win_class" | grep -qE 'onlyoffice|soffice|libreoffice|wps'; then
            debug "Matched office app, using xdotool ctrl+c"
            local saved_clip
            saved_clip=$(xsel -b -o 2>/dev/null 200>&-)
            xdotool key --clearmodifiers ctrl+c
            sleep 0.25
            text=$(xsel -b 2>/dev/null 200>&-)
            [ -n "$text" ] && method="xdotool"
            if [ -n "$saved_clip" ]; then
                printf '%s' "$saved_clip" | xsel -b -i 2>/dev/null 200>&-
            else
                xsel -b -c 2>/dev/null 200>&- || true
            fi
        fi
    fi

    # Wayland primary selection if on Wayland
    if [ -z "$text" ] && [ -n "${WAYLAND_DISPLAY:-}" ] && command -v wl-paste &>/dev/null; then
        text=$(wl-paste -p --no-newline 2>/dev/null 200>&-)
        [ -n "$text" ] && method="wl-primary"
    fi

    # X11 PRIMARY selection
    if [ -z "$text" ] && command -v xsel &>/dev/null; then
        text=$(xsel -p 2>/dev/null 200>&-)
        [ -n "$text" ] && method="primary"
    fi

    # AT-SPI accessibility API
    if [ -z "$text" ] && [ -f "$SCRIPT_DIR/get-selected-text.py" ]; then
        local at_spi_text at_spi_status
        at_spi_text=$(python3 "$SCRIPT_DIR/get-selected-text.py" 2>/dev/null 200>&-)
        at_spi_status=$?
        debug "AT-SPI exit=$at_spi_status text len=${#at_spi_text}"
        if [ "$at_spi_status" -eq 0 ] && [ -n "$at_spi_text" ]; then
            text="$at_spi_text"
            method="atspi"
        fi
    fi

    # CLIPBOARD fallback
    if [ -z "$text" ] && command -v xsel &>/dev/null; then
        text=$(xsel -b 2>/dev/null 200>&-)
        [ -n "$text" ] && method="clipboard"
    fi

    debug "ACQUIRE=$method text len=${#text}"
    printf '%s' "$text"
}

# ── Handle --queue mode without interrupting active playback ──────────────────
if [ "$TTS_MODE" = "queue" ]; then
    RAW_QUEUE_TEXT=$(acquire_input_text)
    [ -z "$RAW_QUEUE_TEXT" ] && exit 0
    python3 - "$QUEUE_FILE" "$RAW_QUEUE_TEXT" << 'PYEOF'
import json, os, sys, time
from sys import path
queue_file, raw_text = sys.argv[1], sys.argv[2]
script_dir = os.path.dirname(os.path.abspath(queue_file))
# Derive title
first_line = ' '.join(raw_text.strip().splitlines()[0].split())[:45] if raw_text.strip() else 'Queued Text'
try:
    with open(queue_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
except Exception:
    data = {'items': [], 'current_index': -1, 'is_playing': False, 'loop': False}

if not isinstance(data, dict) or 'items' not in data:
    data = {'items': [], 'current_index': -1, 'is_playing': False, 'loop': False}

data['items'].append({
    'id': f"{int(time.time() * 1000)}",
    'title': first_line or 'Selection',
    'text': raw_text.strip(),
    'added_at': time.time(),
})
tmp = queue_file + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp, queue_file)
PYEOF
    exit 0
fi

# ── Session lock: only one speak.sh playback at a time (held until exit) ──────
rm -f "$INTERRUPT_FLAG"
trap 'echo "IDLE" > "$TTS_STATUS_FILE"' EXIT
exec 200>"$LOCK_FILE"

if ! flock -n 200 2>/dev/null; then
    # Another instance is running — ask it to stop and wait for the lock.
    touch "$INTERRUPT_FLAG"
    if [ -S "$MPV_SOCKET" ]; then
        echo '{"command":["quit"]}' | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null
    else
        pkill -f "mpv .*--input-ipc-server=$MPV_SOCKET" 2>/dev/null || true
        for pid in $(lsof -t "$LOCK_FILE" 2>/dev/null); do
            if [ "$pid" != "$$" ]; then
                kill -TERM "$pid" 2>/dev/null || true
                kill -TERM -"$pid" 2>/dev/null || true
            fi
        done
        sleep 0.3
    fi
    if ! flock -w 5 200 2>/dev/null; then
        rm -f "$INTERRUPT_FLAG"
        exit 0
    fi
    rm -f "$INTERRUPT_FLAG"
fi

# Temporary file management
declare -a TEMP_FILES=()
cleanup_temp_files() {
    for temp_file in "${TEMP_FILES[@]}"; do
        rm -f "$temp_file" 2>/dev/null
    done
    rm -f "$MPV_SOCKET" 2>/dev/null
}
trap 'cleanup_temp_files; echo "IDLE" > "$TTS_STATUS_FILE"' EXIT

create_temp_file() {
    local suffix="${1:-}"
    local temp_file
    temp_file=$(mktemp -t "tts-XXXXXX$suffix")
    TEMP_FILES+=("$temp_file")
    echo "$temp_file"
}

# ── Dependency check ──────────────────────────────────────────────────────────
for cmd in edge-tts mpv python3; do
    if ! command -v "$cmd" &>/dev/null; then
        notify-send "TTS Error" "Missing dependency: $cmd"
        exit 1
    fi
done
if [ -z "$OVERRIDE_TEXT" ] && ! command -v xsel &>/dev/null && ! command -v wl-paste &>/dev/null; then
    notify-send "TTS Error" "Missing dependency: xsel or wl-clipboard"
    exit 1
fi

# ── Acquire & validate text before wiping work dir ────────────────────────────
TEXT=$(acquire_input_text)
if [ -z "$TEXT" ]; then
    debug "Empty text, exiting"
    exit 0
fi

# Normalize boolean strings (handles yes/no, TRUE/FALSE from YAD, true/false, 1/0)
normalize_bool() {
    local val="${1:-}"
    local def="${2:-no}"
    case "${val,,}" in
        yes|true|1|on)  echo "yes" ;;
        no|false|0|off) echo "no" ;;
        *)              echo "$def" ;;
    esac
}

# ── Read config with CLI overrides taking priority ────────────────────────────
EN_VOICE="${OVERRIDE_EN_VOICE:-$(cat "$CONFIG_DIR/voice"        2>/dev/null || echo "en-US-ChristopherNeural")}"
AR_VOICE="${OVERRIDE_AR_VOICE:-$(cat "$CONFIG_DIR/arabic_voice" 2>/dev/null || echo "ar-SA-HamedNeural")}"
DE_VOICE="${OVERRIDE_DE_VOICE:-$(cat "$CONFIG_DIR/german_voice" 2>/dev/null || echo "de-DE-ConradNeural")}"
EN_RATE="${OVERRIDE_EN_RATE:-$(cat   "$CONFIG_DIR/rate"         2>/dev/null || echo "+50%")}"
AR_RATE="${OVERRIDE_AR_RATE:-$(cat   "$CONFIG_DIR/arabic_rate"  2>/dev/null || echo "+30%")}"
DE_RATE="${OVERRIDE_DE_RATE:-$(cat   "$CONFIG_DIR/german_rate"  2>/dev/null || echo "+30%")}"

SOURCE_LANG="${OVERRIDE_SOURCE_LANG:-$(cat "$CONFIG_DIR/source_language" 2>/dev/null || echo "auto")}"
RAW_TRANSLATE_ENABLED="${OVERRIDE_TRANSLATE_ENABLED:-$(cat "$CONFIG_DIR/translate_enabled" 2>/dev/null || echo "no")}"
TRANSLATE_ENABLED=$(normalize_bool "$RAW_TRANSLATE_ENABLED" "no")
TRANSLATE_PROVIDER="${OVERRIDE_TRANSLATE_PROVIDER:-$(cat "$CONFIG_DIR/translate_provider" 2>/dev/null || echo "deepl")}"
TRANSLATE_TARGET="${OVERRIDE_TRANSLATE_TARGET:-$(cat "$CONFIG_DIR/translate_target" 2>/dev/null || echo "")}"
TRANSLATE_KEY="${OVERRIDE_TRANSLATE_KEY:-$(cat "$CONFIG_DIR/translate_api_key" 2>/dev/null || echo "")}"

RAW_PAUSE_PUNCT="${OVERRIDE_PAUSE_PUNCTUATION:-$(cat "$CONFIG_DIR/pause_punctuation" 2>/dev/null || echo "no")}"
PAUSE_PUNCTUATION=$(normalize_bool "$RAW_PAUSE_PUNCT" "no")
PAUSE_DELAY_MS="${OVERRIDE_PAUSE_DELAY_MS:-$(cat "$CONFIG_DIR/pause_delay_ms" 2>/dev/null || echo "100")}"
if ! [[ "$PAUSE_DELAY_MS" =~ ^[0-9]+$ ]] || [ "$PAUSE_DELAY_MS" -lt 0 ] || [ "$PAUSE_DELAY_MS" -gt 2000 ]; then
    PAUSE_DELAY_MS=100
fi

FILTER_MARKDOWN=$(normalize_bool "$(cat "$CONFIG_DIR/filter_markdown" 2>/dev/null || echo "yes")" "yes")
FILTER_URLS=$(normalize_bool "$(cat "$CONFIG_DIR/filter_urls" 2>/dev/null || echo "yes")" "yes")
FILTER_CODE=$(normalize_bool "$(cat "$CONFIG_DIR/filter_code" 2>/dev/null || echo "no")" "no")
REPLACE_SYMBOLS=$(normalize_bool "$(cat "$CONFIG_DIR/replace_symbols" 2>/dev/null || echo "yes")" "yes")

# ── Optional: translate text before TTS ───────────────────────────────────────
if [ "$TRANSLATE_ENABLED" = "yes" ] && [ -n "$TRANSLATE_TARGET" ] && [ -n "$TRANSLATE_KEY" ]; then
    TRANSLATED=$(printf '%s' "$TEXT" | python3 "$SCRIPT_DIR/translate_text.py" \
        --provider "$TRANSLATE_PROVIDER" \
        --target "$TRANSLATE_TARGET" \
        --api-key "$TRANSLATE_KEY" 2>&1)
    TRANSLATE_STATUS=$?
    if [ "$TRANSLATE_STATUS" -ne 0 ]; then
        if [ -n "$OVERRIDE_TEXT" ]; then
            echo "STATUS:ERROR"
            notify-send "TTS Translation Error" "${TRANSLATED:-Translation failed.}"
            exit 1
        fi
        exit 0
    fi
    TEXT="$TRANSLATED"
fi

# ── Clean work dir and run unified Python text processor & segmenter ──────────
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

SEG_COUNT=$(printf '%s' "$TEXT" | python3 "$SCRIPT_DIR/tts_text_processor.py" \
    --workdir "$WORK_DIR" \
    --source-lang "$SOURCE_LANG" \
    --pause-punctuation "$PAUSE_PUNCTUATION" \
    --filter-markdown "$FILTER_MARKDOWN" \
    --filter-urls "$FILTER_URLS" \
    --filter-code "$FILTER_CODE" \
    --replace-symbols "$REPLACE_SYMBOLS" 2>/dev/null)

if [ -z "$SEG_COUNT" ] || ! [ "$SEG_COUNT" -ge 1 ] 2>/dev/null; then
    if [ -n "$OVERRIDE_TEXT" ]; then
        echo "STATUS:ERROR"
        notify-send "TTS Error" "Text contains no speakable content (e.g., only punctuation)."
        exit 1
    fi
    exit 0
fi

# ── Cache helpers ─────────────────────────────────────────────────────────────
CACHE_DIR="$HOME/.cache/speak-aloud"
mkdir -p "$CACHE_DIR"

# Prune cache entries not used in the last 30 days in background
find "$CACHE_DIR" -name '*.mp3' -type f -atime +30 -delete 2>/dev/null 200>&- &

cache_key() {
    local voice="$1" rate="$2" text="$3"
    if [ -z "$voice" ] || [ -z "$text" ]; then
        echo "invalid"
        return 1
    fi
    local extra=""
    if [ "$TRANSLATE_ENABLED" = "yes" ] && [ -n "$TRANSLATE_TARGET" ]; then
        extra="${TRANSLATE_PROVIDER}:${TRANSLATE_TARGET}"
    fi
    printf '%s\x1f%s\x1f%s\x1f%s' "$voice" "$rate" "$text" "$extra" | sha256sum | cut -d' ' -f1
}

cache_path() {
    echo "$CACHE_DIR/$(cache_key "$1" "$2" "$3").mp3"
}

cache_store() {
    local src="$1" dest="$2" tmp
    tmp="${dest}.tmp.$$"
    cp "$src" "$tmp" && mv "$tmp" "$dest"
}

silence_file_for_delay() {
    local ms="$1"
    local dest="$CACHE_DIR/silence_${ms}ms.mp3"
    if [ -f "$dest" ]; then
        echo "$dest"
        return 0
    fi
    local tmp="${dest}.tmp.$$"
    local sec
    sec=$(printf '%s.%02d' "$((ms / 1000))" "$((ms % 1000 / 10))")
    if ffmpeg -y -f lavfi -i "anullsrc=r=24000:cl=mono" -t "$sec" -acodec libmp3lame -b:a 48k "$tmp" 2>/dev/null; then
        mv "$tmp" "$dest"
        echo "$dest"
        return 0
    fi
    rm -f "$tmp"
    echo ""
    return 1
}

generate_audio() {
    local voice="$1" rate="$2" text="$3" audio="$4"
    local tmp_stderr tmp_audio attempt delay
    local rate_args=()
    [ -n "$rate" ] && [ "$rate" != "+0%" ] && [ "$rate" != "0%" ] && rate_args=(--rate="$rate")
    tmp_stderr=$(create_temp_file)
    tmp_audio=$(create_temp_file ".mp3")

    for attempt in 1 2 3 4 5; do
        : > "$tmp_stderr"
        if edge-tts --voice "$voice" "${rate_args[@]}" --text "$text" --write-media "$tmp_audio" 2>"$tmp_stderr"; then
            if [ -s "$tmp_audio" ] && file "$tmp_audio" 2>/dev/null | grep -qi "audio\|mpeg"; then
                mv "$tmp_audio" "$audio"
                rm -f "$tmp_stderr" "$tmp_audio"
                return 0
            else
                echo "Generated file is not valid audio (size: $(stat -c%s "$tmp_audio" 2>/dev/null || echo 0) bytes)" >&2
                echo "edge-tts stderr: $(cat "$tmp_stderr")" >&2
            fi
        fi

        local err_msg=""
        if grep -qiE 'WSServerHandshakeError: 403|Invalid response status.*403|403.*Invalid response status' "$tmp_stderr"; then
            err_msg="Microsoft rejected the speech connection (HTTP 403). Update edge-tts with: pipx upgrade edge-tts"
        elif grep -q "NoAudioReceived" "$tmp_stderr"; then
            err_msg="The speech service returned no audio. Try different text or update edge-tts."
        elif grep -q -i "timeout\|connection\|websocket" "$tmp_stderr"; then
            if [ "$attempt" -lt 5 ]; then
                delay=$(( 2 ** attempt ))
                [ -n "$OVERRIDE_TEXT" ] && echo "STATUS:RETRYING"
                echo "RETRYING" > "$TTS_STATUS_FILE"
                notify-send "TTS Retry" "Connection issue — retrying in ${delay}s (attempt $attempt/5)"
                sleep "$delay"
                continue
            fi
            err_msg="Connection timed out after 5 attempts. Check your internet."
        elif grep -q -i "rate.*limit\|429\|too many" "$tmp_stderr"; then
            if [ "$attempt" -lt 5 ]; then
                delay=$(( 3 * attempt ))
                [ -n "$OVERRIDE_TEXT" ] && echo "STATUS:RETRYING"
                echo "RETRYING" > "$TTS_STATUS_FILE"
                notify-send "TTS Retry" "Rate limited — waiting ${delay}s (attempt $attempt/5)"
                sleep "$delay"
                continue
            fi
            err_msg="Rate limited by Microsoft TTS service. Try again later."
        else
            err_msg="Failed to generate speech. Check your internet connection."
        fi

        echo "$err_msg" >&2
        cat "$tmp_stderr" >&2
        rm -f "$tmp_stderr" "$tmp_audio"
        return 1
    done
}

# ═════════════════════════════════════════════════════════════════════════════
# UNIFIED PROGRESSIVE GENERATION & PLAYBACK PIPELINE
# ═════════════════════════════════════════════════════════════════════════════

[ -n "$OVERRIDE_TEXT" ] && echo "STATUS:GENERATING"
echo "GENERATING" > "$TTS_STATUS_FILE"

PIDS=""
for i in $(seq 0 $((SEG_COUNT - 1))); do
    PAD=$(printf "%03d" "$i")
    LANG=$(cat "$WORK_DIR/seg_${PAD}.lang")
    SEG_TEXT=$(cat "$WORK_DIR/seg_${PAD}.txt")
    AUDIO="$WORK_DIR/seg_${PAD}.mp3"
    STATUS_PATH="$WORK_DIR/seg_${PAD}.status"

    if [ "$LANG" = "ar" ]; then VOICE="$AR_VOICE"; RATE="$AR_RATE"
    elif [ "$LANG" = "de" ]; then VOICE="$DE_VOICE"; RATE="$DE_RATE"
    else VOICE="$EN_VOICE"; RATE="$EN_RATE"; fi

    # If --speed was explicitly passed in GUI mode without per-language rate override, use _GUI_ cache key
    CACHE_RATE_TAG="$RATE"
    if [ -n "$OVERRIDE_SPEED" ] && [ -z "$OVERRIDE_EN_RATE" ] && [ -z "$OVERRIDE_AR_RATE" ] && [ -z "$OVERRIDE_DE_RATE" ]; then
        RATE=""
        CACHE_RATE_TAG="_GUI_"
    fi

    (
        # Chunk 0 starts immediately (0s delay); subsequent chunks stagger gently (0.12s)
        if [ "$i" -gt 0 ]; then
            sleep "$(awk -v n="$i" 'BEGIN{printf "%.2f", (n <= 6 ? n : 6) * 0.12}')"
        fi
        echo "processing" > "$STATUS_PATH"
        CACHED=$(cache_path "$VOICE" "$CACHE_RATE_TAG" "$SEG_TEXT")
        if [ -f "$CACHED" ] && [ -s "$CACHED" ]; then
            cp "$CACHED" "$AUDIO"
            echo "completed" > "$STATUS_PATH"
            debug "Segment $PAD: cache hit"
        else
            if ! generate_audio "$VOICE" "$RATE" "$SEG_TEXT" "$AUDIO" 2>"$WORK_DIR/err_${PAD}.txt"; then
                echo "failed" > "$STATUS_PATH"
                debug "Segment $PAD: generation FAILED"
                exit 1
            fi
            cache_store "$AUDIO" "$CACHED"
            echo "completed" > "$STATUS_PATH"
            debug "Segment $PAD: generation OK"
        fi
    ) 200>&- &
    PIDS="$PIDS $!"
done

# ── Wait for first MP3 (prefer seg_000 for in-order start) ──────────────────
FIRST_FILE=""
CURRENT_IDX=-1

for _ in $(seq 1 300); do
    if [ -f "$WORK_DIR/seg_000.mp3" ] && [ -s "$WORK_DIR/seg_000.mp3" ]; then
        FIRST_FILE="$WORK_DIR/seg_000.mp3"
        CURRENT_IDX=0
        debug "Progressive: starting on seg_000"
        break
    fi
    # If seg_000 explicitly failed, break early to fallback
    if [ "$(cat "$WORK_DIR/seg_000.status" 2>/dev/null)" = "failed" ]; then
        break
    fi
    sleep 0.05
done

if [ -z "$FIRST_FILE" ]; then
    for _ in $(seq 1 300); do
        FIRST_FILE=$(find "$WORK_DIR" -maxdepth 1 -name 'seg_*.mp3' | sort | head -1)
        if [ -n "$FIRST_FILE" ] && [ -s "$FIRST_FILE" ]; then
            FIRST_BASENAME=$(basename "$FIRST_FILE")
            CURRENT_IDX=${FIRST_BASENAME#seg_}
            CURRENT_IDX=${CURRENT_IDX%.mp3}
            CURRENT_IDX=$((10#$CURRENT_IDX))
            debug "Progressive: seg_000 missing, starting on seg_$CURRENT_IDX"
            break
        fi
        # Check if all background jobs already exited
        ALIVE=0
        for pid in $PIDS; do
            kill -0 "$pid" 2>/dev/null && ALIVE=1
        done
        [ "$ALIVE" -eq 0 ] && break
        sleep 0.05
    done
fi

if [ -z "$FIRST_FILE" ]; then
    FAILED=0
    for pid in $PIDS; do
        wait "$pid" || FAILED=1
    done
    ERR_MSG=$(cat "$WORK_DIR"/err_*.txt 2>/dev/null | grep -v '^+' | grep . | head -1)
    [ -n "$OVERRIDE_TEXT" ] && echo "STATUS:ERROR"
    echo "ERROR" > "$TTS_STATUS_FILE"
    notify-send "TTS Error" "${ERR_MSG:-Failed to generate speech. Check your internet connection.}"
    exit 1
fi

if [ -f "$INTERRUPT_FLAG" ]; then
    exit 0
fi

SILENCE_FILE=""
if [ "$PAUSE_PUNCTUATION" = "yes" ] && [ "$PAUSE_DELAY_MS" -gt 0 ]; then
    SILENCE_FILE=$(silence_file_for_delay "$PAUSE_DELAY_MS")
fi

# ── Start mpv playback (unless --generate-only) ─────────────────────────────
if [ "$TTS_MODE" = "play" ]; then
    [ -n "$OVERRIDE_TEXT" ] && echo "STATUS:PLAYING"
    echo "PLAYING" > "$TTS_STATUS_FILE"
    rm -f "$MPV_SOCKET"

    SAVED_SPEED=$(tr -cd '0-9.' < "$CONFIG_DIR/global_speed" 2>/dev/null || echo "")
    INITIAL_SPEED="${OVERRIDE_SPEED:-${SAVED_SPEED:-1.0}}"
    # Validate speed float (0.5 to 3.0)
    if ! awk -v s="$INITIAL_SPEED" 'BEGIN{exit !(s+0 >= 0.25 && s+0 <= 3.0)}' 2>/dev/null; then
        INITIAL_SPEED="1.0"
    fi

    debug "Starting mpv on $FIRST_FILE (seg_$CURRENT_IDX, speed=$INITIAL_SPEED)"
    mpv "$FIRST_FILE" --no-terminal --idle --speed="$INITIAL_SPEED" --input-ipc-server="$MPV_SOCKET" 200>&- &
    MPV_PID=$!

    # ── Background: append remaining segments in order via IPC ──────────────
    (
        exec 200>&-
        while true; do
            if ! kill -0 "$MPV_PID" 2>/dev/null; then
                break
            fi

            NEXT_IDX=$((CURRENT_IDX + 1))
            NEXT_PAD=$(printf "%03d" "$NEXT_IDX")
            NEXT_FILE="$WORK_DIR/seg_${NEXT_PAD}.mp3"
            NEXT_STATUS=$(cat "$WORK_DIR/seg_${NEXT_PAD}.status" 2>/dev/null || echo "pending")

            if [ -f "$NEXT_FILE" ] && [ -s "$NEXT_FILE" ] && [ "$NEXT_STATUS" = "completed" ]; then
                if [ -S "$MPV_SOCKET" ]; then
                    if [ -n "$SILENCE_FILE" ]; then
                        printf '{"command":["loadfile","%s","append"]}\n' "$SILENCE_FILE" \
                            | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null
                    fi
                    if printf '{"command":["loadfile","%s","append-play"]}\n' "$NEXT_FILE" \
                            | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null; then
                        CURRENT_IDX=$NEXT_IDX
                        debug "Appended seg_${NEXT_PAD}.mp3"
                    fi
                fi
            elif [ "$NEXT_STATUS" = "failed" ] && [ "$NEXT_IDX" -lt "$SEG_COUNT" ]; then
                # Skip failed segment so subsequent segments don't block forever
                CURRENT_IDX=$NEXT_IDX
                debug "Skipping failed seg_${NEXT_PAD}"
            fi

            ALL_DONE=1
            for pid in $PIDS; do
                if [ -f "/proc/$pid/status" ] && ! grep -q "^State:.*Z (zombie)" "/proc/$pid/status" && kill -0 "$pid" 2>/dev/null; then
                    ALL_DONE=0
                    break
                fi
            done

            if [ "$ALL_DONE" -eq 1 ] && [ "$NEXT_IDX" -ge "$SEG_COUNT" ]; then
                break
            fi
            sleep 0.08
        done
    ) &
    APPEND_PID=$!
fi

# ── Wait for all generation jobs to complete ─────────────────────────────────
FAILED=0
for pid in $PIDS; do
    wait "$pid" || FAILED=1
done

# Retry any failed segments once (handles transient rate limiting)
if [ "$FAILED" -ne 0 ]; then
    debug "Some segments failed; retrying missing ones..."
    for i in $(seq 0 $((SEG_COUNT - 1))); do
        PAD=$(printf "%03d" "$i")
        AUDIO="$WORK_DIR/seg_${PAD}.mp3"
        STATUS_PATH="$WORK_DIR/seg_${PAD}.status"
        if [ ! -f "$AUDIO" ] || [ ! -s "$AUDIO" ]; then
            LANG=$(cat "$WORK_DIR/seg_${PAD}.lang")
            SEG_TEXT=$(cat "$WORK_DIR/seg_${PAD}.txt")
            if [ "$LANG" = "ar" ]; then VOICE="$AR_VOICE"; RATE="$AR_RATE"
            elif [ "$LANG" = "de" ]; then VOICE="$DE_VOICE"; RATE="$DE_RATE"
            else VOICE="$EN_VOICE"; RATE="$EN_RATE"; fi
            CACHE_RATE_TAG="$RATE"
            if [ -n "$OVERRIDE_SPEED" ] && [ -z "$OVERRIDE_EN_RATE" ] && [ -z "$OVERRIDE_AR_RATE" ] && [ -z "$OVERRIDE_DE_RATE" ]; then
                RATE=""
                CACHE_RATE_TAG="_GUI_"
            fi
            echo "processing" > "$STATUS_PATH"
            CACHED=$(cache_path "$VOICE" "$CACHE_RATE_TAG" "$SEG_TEXT")
            if [ -f "$CACHED" ] && [ -s "$CACHED" ]; then
                cp "$CACHED" "$AUDIO"
                echo "completed" > "$STATUS_PATH"
                FAILED=0
            elif generate_audio "$VOICE" "$RATE" "$SEG_TEXT" "$AUDIO" 2>"$WORK_DIR/err_${PAD}.txt"; then
                cache_store "$AUDIO" "$CACHED"
                echo "completed" > "$STATUS_PATH"
                FAILED=0
            else
                echo "failed" > "$STATUS_PATH"
            fi
        fi
    done
fi

if [ "$FAILED" -ne 0 ]; then
    ERR_MSG=$(cat "$WORK_DIR"/err_*.txt 2>/dev/null | grep -v '^+' | grep . | head -1)
    notify-send "TTS Warning" "${ERR_MSG:-Some segments failed to generate.}"
fi

# Build combined full.mp3 in WORK_DIR for instant Replay / Save MP3 / seamless seeking
mapfile -t SEG_MP3S < <(find "$WORK_DIR" -maxdepth 1 -name 'seg_*.mp3' | sort)
if [ "${#SEG_MP3S[@]}" -gt 0 ]; then
    cat "${SEG_MP3S[@]}" > "$WORK_DIR/full.mp3.tmp" 2>/dev/null && mv "$WORK_DIR/full.mp3.tmp" "$WORK_DIR/full.mp3"
fi

# Export to file if --output was requested
if [ -n "$OUTPUT_FILE" ] && [ "${#SEG_MP3S[@]}" -gt 0 ]; then
    if command -v ffmpeg &>/dev/null; then
        LIST_FILE=$(mktemp)
        for f in "${SEG_MP3S[@]}"; do
            printf "file '%s'\n" "$f" >> "$LIST_FILE"
        done
        ffmpeg -y -f concat -safe 0 -i "$LIST_FILE" -c copy "$OUTPUT_FILE" 2>/dev/null
        rm -f "$LIST_FILE"
    else
        cp "$WORK_DIR/full.mp3" "$OUTPUT_FILE" 2>/dev/null || cat "${SEG_MP3S[@]}" > "$OUTPUT_FILE"
    fi
fi

if [ "$TTS_MODE" = "generate" ]; then
    echo "IDLE" > "$TTS_STATUS_FILE"
    exit 0
fi

if [ -f "$INTERRUPT_FLAG" ]; then
    exit 0
fi

wait "$APPEND_PID" 2>/dev/null || true

# Poll mpv until playback finishes
IDLE_COUNT=0
while kill -0 "$MPV_PID" 2>/dev/null; do
    if [ -S "$MPV_SOCKET" ]; then
        if echo '{"command":["get_property","core-idle"]}' | socat - "$MPV_SOCKET" 2>/dev/null | grep -q '"data":true'; then
            if echo '{"command":["get_property","pause"]}' | socat - "$MPV_SOCKET" 2>/dev/null | grep -q '"data":true'; then
                IDLE_COUNT=0
            else
                IDLE_COUNT=$((IDLE_COUNT + 1))
                if [ "$IDLE_COUNT" -ge 2 ]; then
                    echo '{"command":["quit"]}' | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null
                    break
                fi
            fi
        else
            IDLE_COUNT=0
        fi
    fi
    sleep 0.25
done

wait "$MPV_PID" 2>/dev/null || true
rm -f "$MPV_SOCKET"
echo "IDLE" > "$TTS_STATUS_FILE"
