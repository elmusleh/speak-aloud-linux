#!/bin/bash

# Add local bin directory and user local bin to PATH for mpv and edge-tts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$SCRIPT_DIR/bin:$HOME/.local/bin:$PATH"

# ── Ensure tray & floating player daemon is running ───────────────────────────
if ! systemctl --user is-active --quiet speak-aloud-daemon.service 2>/dev/null && ! pgrep -f "tts-daemon.py" > /dev/null 2>&1; then
    systemctl --user start speak-aloud-daemon.service 2>/dev/null || setsid python3 "$SCRIPT_DIR/tts-daemon.py" > /dev/null 2>&1 < /dev/null &
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

# Use per-user 0700 runtime directory ($XDG_RUNTIME_DIR/speak-aloud-$UID) with /tmp symlinks
TEMP_DIR="${TMPDIR:-/tmp}"
USER_UID="$(id -u)"
if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "${XDG_RUNTIME_DIR}" ] && [ -w "${XDG_RUNTIME_DIR}" ]; then
    RUNTIME_DIR="${XDG_RUNTIME_DIR}/speak-aloud-${USER_UID}"
else
    RUNTIME_DIR="${TEMP_DIR}/speak-aloud-${USER_UID}"
fi
mkdir -p "$RUNTIME_DIR" "$RUNTIME_DIR/work" 2>/dev/null || true
chmod 700 "$RUNTIME_DIR" "$RUNTIME_DIR/work" 2>/dev/null || true

WORK_DIR="$RUNTIME_DIR/work"
TTS_STATUS_FILE="$RUNTIME_DIR/status"           # polled by tts-daemon.py
QUEUE_FILE="$RUNTIME_DIR/queue.json"            # shared playback queue
LOCK_FILE="$RUNTIME_DIR/lock"
MPV_SOCKET="$RUNTIME_DIR/mpv.sock"
SYNTH_SOCKET="$RUNTIME_DIR/synth.sock"
INTERRUPT_FLAG="$RUNTIME_DIR/interrupt"

# Maintain backward-compatible user-owned symlinks in /tmp
ln -sfn "$WORK_DIR" "$TEMP_DIR/speak-aloud-work" 2>/dev/null || true
ln -sfn "$TTS_STATUS_FILE" "$TEMP_DIR/tts-status" 2>/dev/null || true
ln -sfn "$TTS_STATUS_FILE" "$TEMP_DIR/speak-aloud-status" 2>/dev/null || true
ln -sfn "$QUEUE_FILE" "$TEMP_DIR/speak-aloud-queue.json" 2>/dev/null || true
ln -sfn "$MPV_SOCKET" "$TEMP_DIR/speak-aloud-mpv.sock" 2>/dev/null || true
ln -sfn "$SYNTH_SOCKET" "$TEMP_DIR/speak-aloud-synth.sock" 2>/dev/null || true

# ── Parse optional CLI args (single clean pass) ───────────────────────────────
OVERRIDE_TEXT=""
OCR_MODE="no"
OCR_IMAGE=""
FILE_INPUT=""
PAGE_INPUT=""
URL_INPUT=""
BUNDLE_OUTPUT=""
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
  --file FILE                Read text from PDF, EPUB, or text file
  --page N                   Page number for PDF (1-based)
  --url URL                  Extract article text from webpage URL
  --ocr                      Select a screen region via rubber-band & read via OCR
  --ocr-image FILE           Extract text from an image file via OCR & speak it
  --queue                    Add selected (or --text / --ocr) content to the playback queue
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
  Runtime folder (0700 isolated):   $XDG_RUNTIME_DIR/speak-aloud-$UID/
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --text)               OVERRIDE_TEXT="$2";              shift 2 ;;
        --file)               FILE_INPUT="$2";                 shift 2 ;;
        --page)               PAGE_INPUT="$2";                 shift 2 ;;
        --url)                URL_INPUT="$2";                  shift 2 ;;
        --bundle)             BUNDLE_OUTPUT="$2";              shift 2 ;;
        --summarize)          SUMMARIZE_MODE="yes";            shift 1 ;;
        --first-chunk-max)    OVERRIDE_FIRST_CHUNK="$2";       shift 2 ;;
        --max-segment-chars)  OVERRIDE_MAX_SEG="$2";           shift 2 ;;
        --voice-clarity-eq)   OVERRIDE_VOICE_CLARITY="$2";     shift 2 ;;
        --ocr)                OCR_MODE="yes";                  shift 1 ;;
        --ocr-image)          OCR_MODE="yes"; OCR_IMAGE="$2";  shift 2 ;;
        --queue|-q)           TTS_MODE="queue";                shift 1 ;;
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

if [ -n "$BUNDLE_OUTPUT" ]; then
    declare -a bundle_src=()
    if [ -n "$FILE_INPUT" ]; then
        bundle_src=(--file "$FILE_INPUT")
        [ -n "$PAGE_INPUT" ] && bundle_src+=(--page "$PAGE_INPUT")
    elif [ -n "$URL_INPUT" ]; then
        bundle_src=(--url "$URL_INPUT")
    fi
    python3 "$SCRIPT_DIR/tts_reader.py" "${bundle_src[@]}" --bundle "$BUNDLE_OUTPUT"
    exit $?
fi

if [ -n "$URL_INPUT" ] && [ -z "$OVERRIDE_TEXT" ]; then
    if [ "$TTS_MODE" = "queue" ]; then
        python3 "$SCRIPT_DIR/tts_reader.py" --url "$URL_INPUT" --queue
        exit 0
    else
        OVERRIDE_TEXT=$(python3 "$SCRIPT_DIR/tts_reader.py" --url "$URL_INPUT" 2>/dev/null || echo "")
        [ -z "$OVERRIDE_TEXT" ] && exit 0
    fi
fi

if [ -n "$FILE_INPUT" ] && [ -z "$OVERRIDE_TEXT" ]; then
    declare -a page_args=()
    [ -n "$PAGE_INPUT" ] && page_args=(--page "$PAGE_INPUT")
    if [ "$TTS_MODE" = "queue" ]; then
        python3 "$SCRIPT_DIR/tts_reader.py" --file "$FILE_INPUT" "${page_args[@]}" --queue
        exit 0
    else
        OVERRIDE_TEXT=$(python3 "$SCRIPT_DIR/tts_reader.py" --file "$FILE_INPUT" "${page_args[@]}" 2>/dev/null || echo "")
        [ -z "$OVERRIDE_TEXT" ] && exit 0
    fi
fi

if [ "$OCR_MODE" = "yes" ] && [ -z "$OVERRIDE_TEXT" ]; then
    if [ -n "$OCR_IMAGE" ]; then
        OVERRIDE_TEXT=$(python3 "$SCRIPT_DIR/tts_ocr.py" --image "$OCR_IMAGE" 2>/dev/null || echo "")
    else
        OVERRIDE_TEXT=$(python3 "$SCRIPT_DIR/tts_ocr.py" 2>/dev/null || echo "")
    fi
    if [ -z "$OVERRIDE_TEXT" ]; then
        notify-send "OCR Speak" "No readable text detected in selected screen region." 2>/dev/null || true
        exit 0
    fi
fi

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
exec 200>"$LOCK_FILE"

if ! flock -n 200 2>/dev/null; then
    # Another instance is running — ask it to stop and wait for the lock.
    touch "$INTERRUPT_FLAG"
    if [ -S "$MPV_SOCKET" ]; then
        echo '{"command":["quit"]}' | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null
    else
        pkill -f "mpv .*--input-ipc-server=$MPV_SOCKET" 2>/dev/null || true
    fi
    if [ -f "$RUNTIME_DIR/pid" ]; then
        OLD_PID=$(cat "$RUNTIME_DIR/pid" 2>/dev/null)
        if [ -n "$OLD_PID" ] && [ "$OLD_PID" != "$$" ]; then
            kill -TERM "$OLD_PID" 2>/dev/null || true
        fi
    fi
    if ! flock -w 5 200 2>/dev/null; then
        exit 0
    fi
    rm -f "$INTERRUPT_FLAG"
else
    rm -f "$INTERRUPT_FLAG"
fi
echo "$$" > "$RUNTIME_DIR/pid"

# Temporary file management
declare -a TEMP_FILES=()
cleanup_temp_files() {
    for temp_file in "${TEMP_FILES[@]}"; do
        rm -f "$temp_file" 2>/dev/null
    done
    rm -f "$MPV_SOCKET" 2>/dev/null
    if [ -f "$RUNTIME_DIR/pid" ]; then
        CUR_PID=$(cat "$RUNTIME_DIR/pid" 2>/dev/null)
        if [ "$CUR_PID" = "$$" ]; then
            rm -f "$RUNTIME_DIR/pid" 2>/dev/null || true
        fi
    fi
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

# Safely read the first non-empty line from the first existing config file
read_cfg_first() {
    local def="$1"
    shift
    local f val
    for f in "$@"; do
        if [ -f "$f" ]; then
            val=$(head -n 1 "$f" 2>/dev/null | tr -d '\r\n')
            if [ -n "$val" ]; then
                printf '%s' "$val"
                return 0
            fi
        fi
    done
    printf '%s' "$def"
}

# Guarantee rate matches edge-tts requirement: ^[+-][0-9]+%$
normalize_rate() {
    local raw="${1:-}"
    local def="${2:-+0%}"
    local num
    num=$(printf '%s' "$raw" | grep -oE '[+-]?[0-9]+' | head -n 1)
    if [ -z "$num" ]; then
        printf '%s' "$def"
        return 0
    fi
    num=$((10#${num#+}))
    [ "$num" -gt 100 ] && num=100
    [ "$num" -lt -50 ] && num=-50
    if [ "$num" -ge 0 ]; then
        printf '+%d%%' "$num"
    else
        printf '%d%%' "$num"
    fi
}

# ── Read config with CLI overrides taking priority ────────────────────────────
EN_VOICE="${OVERRIDE_EN_VOICE:-$(read_cfg_first "en-US-ChristopherNeural" "$CONFIG_DIR/voice" "$CONFIG_DIR/voice_en")}"
AR_VOICE="${OVERRIDE_AR_VOICE:-$(read_cfg_first "ar-SA-HamedNeural" "$CONFIG_DIR/arabic_voice" "$CONFIG_DIR/voice_ar")}"
DE_VOICE="${OVERRIDE_DE_VOICE:-$(read_cfg_first "de-DE-ConradNeural" "$CONFIG_DIR/german_voice" "$CONFIG_DIR/voice_de")}"

RAW_EN_RATE="${OVERRIDE_EN_RATE:-$(read_cfg_first "+50%" "$CONFIG_DIR/rate" "$CONFIG_DIR/rate_en")}"
RAW_AR_RATE="${OVERRIDE_AR_RATE:-$(read_cfg_first "+30%" "$CONFIG_DIR/arabic_rate" "$CONFIG_DIR/rate_ar")}"
RAW_DE_RATE="${OVERRIDE_DE_RATE:-$(read_cfg_first "+30%" "$CONFIG_DIR/german_rate" "$CONFIG_DIR/rate_de")}"
EN_RATE=$(normalize_rate "$RAW_EN_RATE" "+50%")
AR_RATE=$(normalize_rate "$RAW_AR_RATE" "+30%")
DE_RATE=$(normalize_rate "$RAW_DE_RATE" "+30%")
TTS_PITCH="${OVERRIDE_PITCH:-$(read_cfg_first "+0Hz" "$CONFIG_DIR/pitch")}"
if ! [[ "$TTS_PITCH" =~ ^[+-]?[0-9]+(Hz|%)?$ ]]; then
    TTS_PITCH="+0Hz"
fi

SOURCE_LANG="${OVERRIDE_SOURCE_LANG:-$(read_cfg_first "auto" "$CONFIG_DIR/source_language" "$CONFIG_DIR/translate_from")}"
case "${SOURCE_LANG,,}" in
    en|ar|de) SOURCE_LANG="${SOURCE_LANG,,}" ;;
    *)        SOURCE_LANG="auto" ;;
esac
RAW_TRANSLATE_ENABLED="${OVERRIDE_TRANSLATE_ENABLED:-$(read_cfg_first "no" "$CONFIG_DIR/translate_enabled")}"
TRANSLATE_ENABLED=$(normalize_bool "$RAW_TRANSLATE_ENABLED" "no")
TRANSLATE_PROVIDER="${OVERRIDE_TRANSLATE_PROVIDER:-$(read_cfg_first "deepl" "$CONFIG_DIR/translate_provider")}"
TRANSLATE_TARGET="${OVERRIDE_TRANSLATE_TARGET:-$(read_cfg_first "" "$CONFIG_DIR/translate_target" "$CONFIG_DIR/translate_to")}"
TRANSLATE_KEY="${OVERRIDE_TRANSLATE_KEY:-$(read_cfg_first "" "$CONFIG_DIR/translate_api_key")}"

RAW_PAUSE_PUNCT="${OVERRIDE_PAUSE_PUNCTUATION:-$(read_cfg_first "no" "$CONFIG_DIR/pause_punctuation")}"
PAUSE_PUNCTUATION=$(normalize_bool "$RAW_PAUSE_PUNCT" "no")
PAUSE_DELAY_MS="${OVERRIDE_PAUSE_DELAY_MS:-$(read_cfg_first "100" "$CONFIG_DIR/pause_delay_ms" "$CONFIG_DIR/pause_delay")}"
if ! [[ "$PAUSE_DELAY_MS" =~ ^[0-9]+$ ]] || [ "$PAUSE_DELAY_MS" -lt 0 ] || [ "$PAUSE_DELAY_MS" -gt 2000 ]; then
    PAUSE_DELAY_MS=100
fi

FILTER_MARKDOWN=$(normalize_bool "$(read_cfg_first "yes" "$CONFIG_DIR/filter_markdown")" "yes")
FILTER_URLS=$(normalize_bool "$(read_cfg_first "yes" "$CONFIG_DIR/filter_urls")" "yes")
FILTER_CODE=$(normalize_bool "$(read_cfg_first "no" "$CONFIG_DIR/skip_code" "$CONFIG_DIR/filter_code")" "no")
REPLACE_SYMBOLS=$(normalize_bool "$(read_cfg_first "yes" "$CONFIG_DIR/replace_symbols")" "yes")
USE_LEXICON=$(normalize_bool "$(read_cfg_first "yes" "$CONFIG_DIR/use_lexicon")" "yes")
ADAPTIVE_PROSODY=$(normalize_bool "$(read_cfg_first "yes" "$CONFIG_DIR/adaptive_prosody")" "yes")
VOICE_CLARITY_EQ=$(normalize_bool "${OVERRIDE_VOICE_CLARITY:-$(read_cfg_first "no" "$CONFIG_DIR/voice_clarity_eq")}" "no")
FIRST_CHUNK_MAX="${OVERRIDE_FIRST_CHUNK:-$(read_cfg_first "140" "$CONFIG_DIR/first_chunk_max")}"
MAX_SEGMENT_CHARS="${OVERRIDE_MAX_SEG:-$(read_cfg_first "280" "$CONFIG_DIR/max_segment_chars")}"
SUMMARIZE_ARG="no"
[ "${SUMMARIZE_MODE:-no}" = "yes" ] && SUMMARIZE_ARG="yes"

# ── Optional: Per-App Speech Profile Rules ────────────────────────────────────
APP_RULES_FILE="$CONFIG_DIR/app_rules.json"
if [ -f "$APP_RULES_FILE" ] && command -v xprop &>/dev/null && command -v xdotool &>/dev/null; then
    ACTIVE_WID=$(xdotool getactivewindow 2>/dev/null || true)
    if [ -n "$ACTIVE_WID" ]; then
        ACTIVE_CLASS=$(xprop -id "$ACTIVE_WID" WM_CLASS 2>/dev/null | tr '[:upper:]' '[:lower:]' | tr -d '"' | tr ',' ' ')
        for c in $ACTIVE_CLASS; do
            RULE_SPD=$(python3 -c "import json; r = json.load(open('$APP_RULES_FILE')).get('$c', {}); print(r.get('speed', ''))" 2>/dev/null || true)
            if [ -n "$RULE_SPD" ] && [ -z "$OVERRIDE_SPEED" ]; then
                INITIAL_SPEED="$RULE_SPD"
                debug "Applied per-app rule for class '$c': speed $RULE_SPD"
                break
            fi
        done
    fi
fi

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
chmod 700 "$WORK_DIR" 2>/dev/null || true

SEG_COUNT=$(printf '%s' "$TEXT" | python3 "$SCRIPT_DIR/tts_text_processor.py" \
    --workdir "$WORK_DIR" \
    --source-lang "$SOURCE_LANG" \
    --pause-punctuation "$PAUSE_PUNCTUATION" \
    --filter-markdown "$FILTER_MARKDOWN" \
    --filter-urls "$FILTER_URLS" \
    --filter-code "$FILTER_CODE" \
    --replace-symbols "$REPLACE_SYMBOLS" \
    --use-lexicon "$USE_LEXICON" \
    --adaptive-prosody "$ADAPTIVE_PROSODY" \
    --first-chunk-max "$FIRST_CHUNK_MAX" \
    --max-segment-chars "$MAX_SEGMENT_CHARS" \
    --summarize "$SUMMARIZE_ARG" 2>/dev/null)

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
    local voice="$1" rate="$2" text="$3" audio="$4" lang="${5:-en}"
    local tmp_stderr tmp_audio attempt delay
    local rate_args=()
    [ -n "$rate" ] && [ "$rate" != "+0%" ] && [ "$rate" != "0%" ] && rate_args+=(--rate="$rate")
    [ -n "$TTS_PITCH" ] && [ "$TTS_PITCH" != "+0Hz" ] && rate_args+=(--pitch="$TTS_PITCH")
    tmp_stderr=$(create_temp_file)
    tmp_audio=$(create_temp_file ".mp3")

    # 1. Fast path: Persistent Async Python Worker over UNIX socket ($SYNTH_SOCKET)
    #    (uses in-memory edge_tts.Communicate, SHA256 cache, and falls back to local Piper ONNX if available)
    if [ -f "$SCRIPT_DIR/tts_synth_worker.py" ]; then
        if python3 "$SCRIPT_DIR/tts_synth_worker.py" \
            --text "$text" \
            --voice "$voice" \
            --rate "${rate:-+0%}" \
            --pitch "${TTS_PITCH:-+0Hz}" \
            --lang "$lang" \
            --output "$tmp_audio" \
            --socket "$SYNTH_SOCKET" 2>"$tmp_stderr"; then
            if [ -s "$tmp_audio" ]; then
                mv "$tmp_audio" "$audio"
                local tmp_words="${tmp_audio%.mp3}.words.json"
                if [ -f "$tmp_words" ]; then
                    mv "$tmp_words" "${audio%.mp3}.words.json" 2>/dev/null || true
                fi
                rm -f "$tmp_stderr" "$tmp_audio"
                return 0
            fi
        fi
    fi

    # 2. Secondary path: edge-tts CLI with exponential backoff
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
            if [ "$attempt" -lt 3 ]; then
                delay=$(( attempt + 1 ))
                [ -n "$OVERRIDE_TEXT" ] && echo "STATUS:RETRYING"
                echo "RETRYING" > "$TTS_STATUS_FILE"
                sleep "$delay"
                continue
            fi
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
            local actual_err
            actual_err=$(tail -n 1 "$tmp_stderr" 2>/dev/null | tr -d '\r')
            err_msg="Speech synthesis error: ${actual_err:-Failed to generate speech.}"
        fi

        # 3. Offline fallback: espeak-ng -> WAV/MP3 if both edge-tts and piper failed
        if command -v espeak-ng &>/dev/null; then
            local tmp_wav
            tmp_wav=$(create_temp_file ".wav")
            if espeak-ng -v "${lang:-en}" -w "$tmp_wav" "$text" 2>/dev/null && [ -s "$tmp_wav" ]; then
                if command -v ffmpeg &>/dev/null && ffmpeg -y -loglevel error -i "$tmp_wav" -codec:a libmp3lame -b:a 48k "$tmp_audio" 2>/dev/null && [ -s "$tmp_audio" ]; then
                    mv "$tmp_audio" "$audio"
                else
                    mv "$tmp_wav" "$audio"
                fi
                rm -f "$tmp_stderr" "$tmp_audio" "$tmp_wav"
                return 0
            fi
            rm -f "$tmp_wav"
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

BATTERY_SAVER=$(read_cfg_first "battery_saver" "battery" "yes")
IS_ON_BATTERY=0
if [ "$BATTERY_SAVER" = "yes" ]; then
    for b in /sys/class/power_supply/BAT*/status; do
        if [ -f "$b" ] && grep -qi "Discharging" "$b" 2>/dev/null; then
            IS_ON_BATTERY=1
            break
        fi
    done
fi

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

    printf '%s\n' "$RATE" > "$WORK_DIR/seg_${PAD}.rate"
    printf '%s\n' "$VOICE" > "$WORK_DIR/seg_${PAD}.voice"

    # If --speed was explicitly passed in GUI mode without per-language rate override, use _GUI_ cache key
    CACHE_RATE_TAG="$RATE"
    if [ -n "$OVERRIDE_SPEED" ] && [ -z "$OVERRIDE_EN_RATE" ] && [ -z "$OVERRIDE_AR_RATE" ] && [ -z "$OVERRIDE_DE_RATE" ]; then
        RATE=""
        CACHE_RATE_TAG="_GUI_"
    fi

    (
        # Chunk 0 starts immediately (0s delay); subsequent chunks stagger gently
        if [ "$i" -gt 0 ]; then
            if [ "$IS_ON_BATTERY" -eq 1 ]; then
                sleep "$(awk -v n="$i" 'BEGIN{printf "%.2f", (n <= 4 ? n : 4) * 0.35}')"
            else
                sleep "$(awk -v n="$i" 'BEGIN{printf "%.2f", (n <= 6 ? n : 6) * 0.12}')"
            fi
        fi
        echo "processing" > "$STATUS_PATH"
        CACHED=$(cache_path "$VOICE" "$CACHE_RATE_TAG" "$SEG_TEXT")
        if [ -f "$CACHED" ] && [ -s "$CACHED" ]; then
            cp "$CACHED" "$AUDIO"
            echo "completed" > "$STATUS_PATH"
            debug "Segment $PAD: cache hit"
        else
            if ! generate_audio "$VOICE" "$RATE" "$SEG_TEXT" "$AUDIO" "$LANG" 2>"$WORK_DIR/err_${PAD}.txt"; then
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
    if [ -f "$INTERRUPT_FLAG" ]; then
        exit 0
    fi
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

if [ -f "$INTERRUPT_FLAG" ]; then
    exit 0
fi

if [ -z "$FIRST_FILE" ]; then
    for _ in $(seq 1 300); do
        if [ -f "$INTERRUPT_FLAG" ]; then
            exit 0
        fi
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

if [ -f "$INTERRUPT_FLAG" ]; then
    exit 0
fi

if [ -z "$FIRST_FILE" ]; then
    FAILED=0
    for pid in $PIDS; do
        wait "$pid" || FAILED=1
    done
    if [ -f "$INTERRUPT_FLAG" ]; then
        exit 0
    fi
    ERR_MSG=$(cat "$WORK_DIR"/err_*.txt 2>/dev/null | grep -v '^+' | grep . | head -1)
    [ -n "$OVERRIDE_TEXT" ] && echo "STATUS:ERROR"
    echo "ERROR" > "$TTS_STATUS_FILE"
    notify-send "TTS Error" "${ERR_MSG:-Failed to generate speech. Check your audio/network settings.}"
    exit 1
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

    INITIAL_SPEED="${OVERRIDE_SPEED:-1.0}"
    # Validate speed float (0.25 to 3.0)
    if ! awk -v s="$INITIAL_SPEED" 'BEGIN{exit !(s+0 >= 0.25 && s+0 <= 3.0)}' 2>/dev/null; then
        INITIAL_SPEED="1.0"
    fi

    debug "Starting mpv on $FIRST_FILE (seg_$CURRENT_IDX, speed=$INITIAL_SPEED)"
    MPV_AUDIO_FILTERS=()
    if [ "$VOICE_CLARITY_EQ" = "yes" ]; then
        MPV_AUDIO_FILTERS=(--af="highpass=f=100,equalizer=f=3000:width_type=h:width=1000:g=3.5,acompressor")
    fi
    mpv "$FIRST_FILE" --no-terminal --idle --gapless-audio=yes --audio-stream-silence=yes --audio-buffer=0.25 --speed="$INITIAL_SPEED" "${MPV_AUDIO_FILTERS[@]}" --input-ipc-server="$MPV_SOCKET" 200>&- &
    MPV_PID=$!

    # Record history entry in background
    (
        python3 -c "
import sys, os
sys.path.insert(0, '$SCRIPT_DIR')
from tts_text_processor import record_spoken_history
txt_path = '$WORK_DIR/seg_000.txt'
txt = open(txt_path, 'r', encoding='utf-8').read() if os.path.exists(txt_path) else ''
record_spoken_history('$WINDOW_TITLE', txt, '$CURRENT_LANG', 0.0)
" 2>/dev/null || true
    ) 200>&- &

    # Interactive desktop notification with transport actions
    if command -v notify-send >/dev/null 2>&1; then
        (
            if notify-send --help 2>&1 | grep -q -- '--action'; then
                ACT=$(notify-send -t 6000 -a "Speak a Loud" -i "audio-volume-high" \
                    --action="pause=Pause" --action="stop=Stop" \
                    "Speak a Loud" "${WINDOW_TITLE:-Reading selection aloud}" 2>/dev/null || true)
                if [ "$ACT" = "pause" ]; then
                    bash "$SCRIPT_DIR/speak-pause.sh" 2>/dev/null || true
                elif [ "$ACT" = "stop" ]; then
                    bash "$SCRIPT_DIR/speak-stop.sh" 2>/dev/null || true
                fi
            fi
        ) 200>&- &
    fi

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
            elif generate_audio "$VOICE" "$RATE" "$SEG_TEXT" "$AUDIO" "$LANG" 2>"$WORK_DIR/err_${PAD}.txt"; then
                cache_store "$AUDIO" "$CACHED"
                echo "completed" > "$STATUS_PATH"
                FAILED=0
            else
                echo "failed" > "$STATUS_PATH"
            fi
        fi
    done
fi

if [ "$FAILED" -ne 0 ] && [ ! -f "$INTERRUPT_FLAG" ]; then
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
