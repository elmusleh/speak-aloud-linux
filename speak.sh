#!/bin/bash

# Add local bin directory to PATH for mpv
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$SCRIPT_DIR/bin:$PATH"

# ── Ensure tray daemon is running ────────────────────────────────────────────
# Auto-start the daemon on every speak invocation so the tray icon
# is always present (handles cases where the user quit it earlier).
if ! pgrep -f "tts-daemon.py" > /dev/null 2>&1; then
    python3 "$SCRIPT_DIR/tts-daemon.py" > /dev/null 2>&1 &
fi

CONFIG_DIR="$HOME/.config/tts_settings"
# Notifications disabled — tray icon only
notify-send() { :; }
# Use system temp directory for portability
TEMP_DIR="${TMPDIR:-/tmp}"
WORK_DIR="$TEMP_DIR/speak-aloud-work"
TTS_STATUS_FILE="$TEMP_DIR/tts-status"   # polled by tts-daemon.py tray icon

# ── Parse optional CLI args ───────────────────────────────────────────────────
OVERRIDE_TEXT=""
OVERRIDE_EN_VOICE=""
OVERRIDE_AR_VOICE=""
OVERRIDE_DE_VOICE=""
OVERRIDE_EN_RATE=""
OVERRIDE_AR_RATE=""
OVERRIDE_DE_RATE=""
OVERRIDE_SPEED=""     # mpv speed multiplier (e.g. 1.5) — used in GUI mode only
OUTPUT_FILE=""        # optional: export concatenated audio to this path
OVERRIDE_SOURCE_LANG=""   # optional: force source language (auto, en, ar, de, ...)
OVERRIDE_TRANSLATE_ENABLED=""  # optional: yes/no
OVERRIDE_TRANSLATE_PROVIDER="" # optional: deepl, google
OVERRIDE_TRANSLATE_TARGET=""   # optional: target language code
OVERRIDE_TRANSLATE_KEY=""  # optional: API key string
OVERRIDE_PAUSE_PUNCTUATION=""  # optional: yes/no
OVERRIDE_PAUSE_DELAY_MS=""   # optional: pause delay in milliseconds (default 100)

show_help() {
    cat << 'EOF'
Usage: speak.sh [OPTIONS]

Options:
  --text TEXT              Speak explicit text
  --en-voice VOICE         Override English voice
  --ar-voice VOICE         Override Arabic voice
  --de-voice VOICE         Override German voice
  --en-rate RATE           Override English rate (e.g., +50%)
  --ar-rate RATE           Override Arabic rate (e.g., +30%)
  --de-rate RATE           Override German rate (e.g., +30%)
  --speed MULTIPLIER       Playback speed for GUI mode (e.g., 1.5)
  --output FILE.mp3        Export concatenated audio to file
  --source-lang LANG         Force source language (auto, en, ar, de, ...)
  --translate-enabled yes/no Enable/disable translation
  --translate-provider     Translation provider (deepl, google)
  --translate-target LANG  Target language for translation
  --translate-api-key KEY  API key for translation service
  --pause-punctuation yes/no Enable/disable punctuation pauses
  --pause-delay-ms MS      Pause delay between segments in ms (default 300)
  --help                   Show this help message

Audio file locations:
  Cache folder (30-day persistent): ~/.cache/speak-aloud/
  Temp work folder (segments):      /tmp/speak-aloud-work/
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --text)     OVERRIDE_TEXT="$2";     shift 2 ;;
        --en-voice) OVERRIDE_EN_VOICE="$2"; shift 2 ;;
        --ar-voice) OVERRIDE_AR_VOICE="$2"; shift 2 ;;
        --de-voice) OVERRIDE_DE_VOICE="$2"; shift 2 ;;
        --en-rate)  OVERRIDE_EN_RATE="$2";  shift 2 ;;
        --ar-rate)  OVERRIDE_AR_RATE="$2";  shift 2 ;;
        --de-rate)  OVERRIDE_DE_RATE="$2";  shift 2 ;;
        --speed)    OVERRIDE_SPEED="$2";    shift 2 ;;
        --output)   OUTPUT_FILE="$2";       shift 2 ;;
        --source-lang)       OVERRIDE_SOURCE_LANG="$2";       shift 2 ;;
        --translate-enabled) OVERRIDE_TRANSLATE_ENABLED="$2"; shift 2 ;;
        --translate-provider) OVERRIDE_TRANSLATE_PROVIDER="$2"; shift 2 ;;
        --translate-target)  OVERRIDE_TRANSLATE_TARGET="$2";  shift 2 ;;
        --translate-api-key) OVERRIDE_TRANSLATE_KEY="$2"; shift 2 ;;
        --pause-punctuation) OVERRIDE_PAUSE_PUNCTUATION="$2"; shift 2 ;;
        --pause-delay-ms) OVERRIDE_PAUSE_DELAY_MS="$2"; shift 2 ;;
        --help)     show_help; exit 0 ;;
        *) shift ;;
    esac
done

# ── Session lock: only one speak.sh at a time (held until exit) ────────────
LOCK_FILE="$TEMP_DIR/speak-aloud.lock"
MPV_SOCKET="$TEMP_DIR/speak-aloud-mpv.sock"
INTERRUPT_FLAG="$TEMP_DIR/speak-aloud.interrupt"

# Clean up stale interrupt flag from a crashed previous run
rm -f "$INTERRUPT_FLAG"

# Always reset tray status on exit (covers errors, interrupts, early exits)
trap 'echo "IDLE" > "$TTS_STATUS_FILE"' EXIT

# Open lock file
exec 200>"$LOCK_FILE"

# Try to acquire lock without blocking
if ! flock -n 200 2>/dev/null; then
    # Another instance is running — ask it to stop and wait for the lock.
    touch "$INTERRUPT_FLAG"
    if [ -S "$MPV_SOCKET" ]; then
        # Socket exists: ask mpv to quit gracefully
        echo '{"command":["quit"]}' | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null
    else
        # Socket is gone but lock is held: previous instance is stuck waiting
        # for a dead/ghost mpv.  Kill the whole process group to release it.
        pkill -f "mpv .*--input-ipc-server=$MPV_SOCKET" 2>/dev/null || true
        # Also SIGTERM any speak.sh holding the same lock file
        for pid in $(lsof -t "$LOCK_FILE" 2>/dev/null); do
            if [ "$pid" != "$$" ]; then
                kill -TERM -"$pid" 2>/dev/null || true
            fi
        done
        sleep 0.5
    fi
    # Block up to 5 seconds for the previous instance to release the lock
    if ! flock -w 5 200 2>/dev/null; then
        rm -f "$INTERRUPT_FLAG"
        exit 0
    fi
    rm -f "$INTERRUPT_FLAG"
fi

# Lock (fd 200) is held for the entire lifetime of this script, including
# playback. New invocations interrupt us via the mpv socket + interrupt flag.
# Combine cleanup + tray reset (line 101 trap is intentionally overridden here).
trap 'cleanup_temp_files; echo "IDLE" > "$TTS_STATUS_FILE"' EXIT

# Temporary file management
declare -a TEMP_FILES=()
cleanup_temp_files() {
    for temp_file in "${TEMP_FILES[@]}"; do
        rm -f "$temp_file" 2>/dev/null
    done
    rm -f "$MPV_SOCKET" 2>/dev/null
}

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
        notify-send "TTS Error" "Missing dependency: $cmd"; exit 1
    fi
done
if [ -z "$OVERRIDE_TEXT" ] && ! command -v xsel &>/dev/null; then
    notify-send "TTS Error" "Missing dependency: xsel"; exit 1
fi

# ── Clean work dir (no killing - we have the lock) ────────────────────────────
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

# ── Get text ──────────────────────────────────────────────────────────────────
TEXT="${OVERRIDE_TEXT:-$(xsel -p)}"
[ -z "$TEXT" ] && TEXT="$(xsel -b 2>/dev/null)"
[ -z "$TEXT" ] && exit 0

# Validate and sanitize input text
validate_text() {
    local text="$1"
    # Remove ASCII control characters only — must NOT touch bytes >= 0x80,
    # otherwise multibyte UTF-8 (Arabic, umlauts, …) would be destroyed.
    text=$(printf '%s' "$text" | tr -d '\000-\010\013\014\016-\037\177')
    # Strip Unicode bidirectional formatting characters (LTR/RTL marks,
    # embeddings, overrides, pop directional formatting) that confuse TTS
    # engines and fragment copied text from PDFs/web pages.
    text=$(printf '%s' "$text" | python3 -c '
import sys
t = sys.stdin.read()
t = t.translate({0x200E:None, 0x200F:None, 0x202A:None, 0x202B:None, 0x202C:None, 0x202D:None, 0x202E:None})
print(t, end="")
')
    # Limit to reasonable length (10000 chars)
    if [ ${#text} -gt 10000 ]; then
        echo "Text too long (${#text} chars), truncating to 10000" >&2
        text="${text:0:10000}"
    fi
    echo "$text"
}

TEXT=$(validate_text "$TEXT")
[ -z "$TEXT" ] && exit 0

# ── Minimum-content guard ───────────────────────────────────────────────────────────────────
# Require at least 2 Unicode letters/digits before invoking TTS. Accidental
# tiny selections (single letter, space, punctuation) are skipped silently
# in shortcut mode; GUI mode still reports an error for deliberate input.
SPEAKABLE_COUNT=$(python3 -c '
import sys, unicodedata
text = sys.argv[1]
print(sum(1 for ch in text if unicodedata.category(ch)[0] in ("L", "N")))
' "$TEXT" 2>/dev/null || echo 0)
if [ "$SPEAKABLE_COUNT" -lt 2 ] 2>/dev/null; then
    if [ -n "$OVERRIDE_TEXT" ]; then
        echo "STATUS:ERROR"
        notify-send "TTS Error" "Text contains no speakable content (e.g., only punctuation)."
        exit 1
    fi
    exit 0
fi

# ── Read config with CLI overrides taking priority ────────────────────────────
EN_VOICE="${OVERRIDE_EN_VOICE:-$(cat "$CONFIG_DIR/voice"        2>/dev/null || echo "en-US-ChristopherNeural")}"
AR_VOICE="${OVERRIDE_AR_VOICE:-$(cat "$CONFIG_DIR/arabic_voice" 2>/dev/null || echo "ar-SA-HamedNeural")}"
DE_VOICE="${OVERRIDE_DE_VOICE:-$(cat "$CONFIG_DIR/german_voice" 2>/dev/null || echo "de-DE-ConradNeural")}"
EN_RATE="${OVERRIDE_EN_RATE:-$(cat   "$CONFIG_DIR/rate"         2>/dev/null || echo "+50%")}"
AR_RATE="${OVERRIDE_AR_RATE:-$(cat   "$CONFIG_DIR/arabic_rate"  2>/dev/null || echo "+30%")}"
DE_RATE="${OVERRIDE_DE_RATE:-$(cat   "$CONFIG_DIR/german_rate"  2>/dev/null || echo "+30%")}"
# Note: OVERRIDE_SPEED is read directly where needed (GUI mode only)

SOURCE_LANG="${OVERRIDE_SOURCE_LANG:-$(cat "$CONFIG_DIR/source_language" 2>/dev/null || echo "auto")}"
TRANSLATE_ENABLED="${OVERRIDE_TRANSLATE_ENABLED:-$(cat "$CONFIG_DIR/translate_enabled" 2>/dev/null || echo "no")}"
TRANSLATE_PROVIDER="${OVERRIDE_TRANSLATE_PROVIDER:-$(cat "$CONFIG_DIR/translate_provider" 2>/dev/null || echo "deepl")}"
TRANSLATE_TARGET="${OVERRIDE_TRANSLATE_TARGET:-$(cat "$CONFIG_DIR/translate_target" 2>/dev/null || echo "")}"
TRANSLATE_KEY="${OVERRIDE_TRANSLATE_KEY:-$(cat "$CONFIG_DIR/translate_api_key" 2>/dev/null || echo "")}"
PAUSE_PUNCTUATION="${OVERRIDE_PAUSE_PUNCTUATION:-$(cat "$CONFIG_DIR/pause_punctuation" 2>/dev/null || echo "yes")}"
PAUSE_DELAY_MS="${OVERRIDE_PAUSE_DELAY_MS:-$(cat "$CONFIG_DIR/pause_delay_ms" 2>/dev/null || echo "100")}"
# Validate pause delay (0–2000 ms)
if ! [[ "$PAUSE_DELAY_MS" =~ ^[0-9]+$ ]] || [ "$PAUSE_DELAY_MS" -lt 0 ] || [ "$PAUSE_DELAY_MS" -gt 2000 ]; then
    PAUSE_DELAY_MS=100
fi

# ── Cache helpers ─────────────────────────────────────────────────────────────
CACHE_DIR="$HOME/.cache/speak-aloud"
mkdir -p "$CACHE_DIR"

# Prune cache entries not used in the last 30 days (keeps cache bounded)
find "$CACHE_DIR" -name '*.mp3' -type f -atime +30 -delete 2>/dev/null 200>&- &

cache_key() {
    # Hash the full raw inputs — no sanitizing/truncation, so distinct
    # (voice, rate, text) combinations can never collide.
    local voice="$1" rate="$2" text="$3"
    if [ -z "$voice" ] || [ -z "$text" ]; then
        echo "invalid"
        return 1
    fi
    # Include translation settings so translated vs. original text don't collide
    local extra=""
    if [ "$TRANSLATE_ENABLED" = "yes" ] && [ -n "$TRANSLATE_TARGET" ]; then
        extra="${TRANSLATE_PROVIDER}:${TRANSLATE_TARGET}"
    fi
    printf '%s\x1f%s\x1f%s\x1f%s' "$voice" "$rate" "$text" "$extra" | sha256sum | cut -d' ' -f1
}

cache_path() {
    echo "$CACHE_DIR/$(cache_key "$1" "$2" "$3").mp3"
}

# Atomically store a generated file in the cache (parallel-safe).
cache_store() {
    local src="$1" dest="$2" tmp
    tmp="${dest}.tmp.$$"
    cp "$src" "$tmp" && mv "$tmp" "$dest"
}

# Generate (or reuse cached) a silent MP3 of a given duration in milliseconds.
# Args: delay_ms  Returns path to the silence file.
silence_file_for_delay() {
    local ms="$1"
    local dest="$CACHE_DIR/silence_${ms}ms.mp3"
    if [ -f "$dest" ]; then
        echo "$dest"
        return 0
    fi
    local tmp="${dest}.tmp.$$"
    local sec
    sec=$(printf '%s.%s' "$((ms / 1000))" "$((ms % 1000 / 10))")
    if ffmpeg -y -f lavfi -i "anullsrc=r=24000:cl=mono" -t "$sec" -acodec libmp3lame -q:a 9 "$tmp" 2>/dev/null; then
        mv "$tmp" "$dest"
        echo "$dest"
        return 0
    fi
    rm -f "$tmp"
    echo ""
    return 1
}

# Runs edge-tts with retry and real error parsing.
# Args: VOICE RATE TEXT AUDIO   (RATE may be empty → no --rate flag)
# Returns 0 on success, prints human-readable error to stderr on failure.
generate_audio() {
    local voice="$1" rate="$2" text="$3" audio="$4"
    local tmp_stderr tmp_audio attempt delay
    local rate_args=()
    [ -n "$rate" ] && rate_args=(--rate="$rate")
    tmp_stderr=$(create_temp_file)
    tmp_audio=$(create_temp_file ".mp3")

    for attempt in 1 2 3 4 5; do
        : > "$tmp_stderr"
        if edge-tts --voice "$voice" "${rate_args[@]}" --text "$text" --write-media "$tmp_audio" 2>"$tmp_stderr"; then
            # Validate the generated audio file
            if [ -s "$tmp_audio" ] && file "$tmp_audio" 2>/dev/null | grep -qi "audio\|mpeg"; then
                mv "$tmp_audio" "$audio"
                rm -f "$tmp_stderr" "$tmp_audio"
                return 0
            else
                # Debug: log why validation failed
                echo "Generated file is not valid audio (size: $(stat -c%s "$tmp_audio" 2>/dev/null || echo 0) bytes, type: $(file "$tmp_audio" 2>/dev/null))" >&2
                echo "edge-tts stderr: $(cat "$tmp_stderr")" >&2
                echo "Text was: '${text:0:100}...' (len=${#text})" >&2
            fi
        fi

        # Parse stderr for known errors
        local err_msg=""
        if grep -q "NoAudioReceived" "$tmp_stderr"; then
            err_msg="Text contains no speakable content (e.g., only punctuation)."
        elif grep -q -i "timeout\|connection\|websocket" "$tmp_stderr"; then
            if [ "$attempt" -lt 5 ]; then
                delay=$(( 2 ** attempt ))
                echo "STATUS:RETRYING"
                echo "RETRYING" > "$TTS_STATUS_FILE"
                notify-send "TTS Retry" "Connection issue — retrying in ${delay}s (attempt $attempt/5)"
                sleep "$delay"
                continue
            fi
            err_msg="Connection timed out after 5 attempts. Check your internet."
        elif grep -q -i "rate.*limit\|429\|too many" "$tmp_stderr"; then
            if [ "$attempt" -lt 5 ]; then
                delay=$(( 3 * attempt ))
                echo "STATUS:RETRYING"
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
        rm -f "$tmp_stderr" "$tmp_audio"
        return 1
    done
}

# ── Optional: translate text before TTS ─────────────────────────────────────
if [ "$TRANSLATE_ENABLED" = "yes" ] && [ -n "$TRANSLATE_TARGET" ] && [ -n "$TRANSLATE_KEY" ]; then
    SCRIPT_DIR_TR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    TRANSLATED=$(printf '%s' "$TEXT" | python3 "$SCRIPT_DIR_TR/translate_text.py" \
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
        # Shortcut mode: skip silently on translation error
        exit 0
    fi
    TEXT="$TRANSLATED"
fi

# ── Source-language override: bypass auto-detection ─────────────────────────
if [ "$SOURCE_LANG" != "auto" ]; then
    mkdir -p "$WORK_DIR"
    printf '%s' "$SOURCE_LANG" > "$WORK_DIR/seg_000.lang"
    printf '%s' "$TEXT"        > "$WORK_DIR/seg_000.txt"
    SEG_COUNT=1
else

# ── Split text into language segments ─────────────────────────────────────────
PY_SCRIPT=$(mktemp)
cat > "$PY_SCRIPT" <<'PYEOF'
import sys, re, unicodedata

text, workdir = sys.argv[1], sys.argv[2]

arabic = re.compile(
    r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]'
    r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\s]*'
)

GERMAN_CHARS = re.compile(r'[äöüßÄÖÜẞ]')

# Minimal fallback for very short fragments where langdetect is unreliable.
_FALLBACK_DE = {
    'der', 'die', 'das', 'den', 'dem', 'des', 'ein', 'eine', 'und', 'mit',
    'zu', 'von', 'bei', 'nach', 'ist', 'sind', 'du', 'ich', 'wir', 'nicht',
    'an', 'am', 'im', 'zum', 'zur', 'für', 'oder', 'aber', 'wenn', 'dass',
    'wird', 'werden', 'haben', 'kann', 'soll', 'muss', 'ja', 'nein',
}

SENT_RE = re.compile(r'(?<=[.!?])\s+')


def _has_umlaut(text):
    return bool(GERMAN_CHARS.search(text))


def _fallback_detect(text):
    """Word-list fallback for fragments too short for langdetect."""
    words = re.findall(r'\b\w+\b', text.lower())
    if not words:
        return 'en'
    de_count = sum(1 for w in words if w in _FALLBACK_DE)
    if de_count >= max(1, len(words) // 4):
        return 'de'
    return 'en'


def classify_block(chunk):
    """Classify a non-Arabic text block as English or German using langdetect.

    Arabic is handled separately before this function is called.
    The block is split into sentences for finer-grained detection,
    then consecutive same-language sentences are merged.
    """
    chunk = chunk.strip()
    if not chunk:
        return []

    # Split into sentences for finer-grained detection
    sentences = [s.strip() for s in SENT_RE.split(chunk) if s.strip()]
    if not sentences:
        sentences = [chunk]

    results = []
    for sent in sentences:
        # Umlauts are unambiguous German markers — short-circuit this sentence only
        if _has_umlaut(sent):
            results.append(('de', sent))
            continue

        words = re.findall(r'\b\w+\b', sent)
        if len(words) < 4:
            # Too short for langdetect — use minimal fallback list
            results.append((_fallback_detect(sent), sent))
            continue

        try:
            from langdetect import detect_langs
            probs = detect_langs(sent)
            de_prob = next((p.prob for p in probs if p.lang == 'de'), 0.0)
            results.append(('de' if de_prob >= 0.70 else 'en', sent))
        except Exception:
            results.append(('en', sent))

    # Merge consecutive same-language segments
    merged = []
    for lang, text in results:
        if merged and merged[-1][0] == lang:
            merged[-1] = (lang, merged[-1][1] + ' ' + text)
        else:
            merged.append((lang, text))
    return merged

# Pass 1: split out Arabic runs (highest priority)
raw_segs, last = [], 0
for m in arabic.finditer(text):
    if m.start() > last:
        chunk = text[last:m.start()].strip()
        if chunk:
            raw_segs.append(chunk)
    ar_chunk = m.group().strip()
    if ar_chunk:
        raw_segs.append(('ar', ar_chunk))
    last = m.end()
if last < len(text):
    chunk = text[last:].strip()
    if chunk:
        raw_segs.append(chunk)
if not raw_segs:
    raw_segs.append(text)

# Pass 2: split each non-Arabic block into English/German phrase segments
segs = []
for item in raw_segs:
    if isinstance(item, tuple):
        segs.append(item)
        continue
    chunk = item.strip()
    if not chunk:
        continue
    segs.extend(classify_block(chunk))

# Merge digit/punctuation-only segments with adjacent Arabic segments.
# Western numerals (0-9) embedded in Arabic text should be spoken in Arabic.
def is_non_letter_chunk(s):
    s = s.strip()
    if not s:
        return False
    for ch in s:
        if unicodedata.category(ch)[0] == 'L':
            return False
    return any(unicodedata.category(ch)[0] == 'N' for ch in s)

merged_digits = []
i = 0
while i < len(segs):
    lang, chunk = segs[i]
    if lang in ('en', 'de') and is_non_letter_chunk(chunk):
        if merged_digits and merged_digits[-1][0] == 'ar':
            pl, pc = merged_digits[-1]
            merged_digits[-1] = ('ar', pc + ' ' + chunk)
        elif i + 1 < len(segs) and segs[i + 1][0] == 'ar':
            nl, nc = segs[i + 1]
            segs[i + 1] = ('ar', chunk + ' ' + nc)
        else:
            merged_digits.append((lang, chunk))
    else:
        merged_digits.append((lang, chunk))
    i += 1
segs = merged_digits

# Skip segments that contain only whitespace / punctuation (no letters to speak)
def speakable_count(s):
    return sum(1 for ch in s if unicodedata.category(ch)[0] in ('L', 'N'))

# Merge adjacent segments of the same language, skipping empty/punctuation-only
# chunks and single-character fragments (they often yield NoAudioReceived)
merged, last_lang, acc = [], None, ''
for lang, chunk in segs:
    stripped = chunk.strip()
    if not stripped or speakable_count(stripped) < 2:
        continue
    if lang == last_lang:
        acc += ' ' + stripped
    else:
        if last_lang is not None:
            merged.append((last_lang, acc))
        last_lang = lang
        acc = stripped
if last_lang is not None:
    merged.append((last_lang, acc))
segs = merged

# Sanitize segments: strip characters the assigned voice cannot pronounce.
# This prevents edge-tts NoAudioReceived on text in unsupported scripts.
def sanitize_for_voice(lang, text):
    if lang == 'ar':
        # Arabic voice: keep Arabic script, basic punctuation, whitespace
        return re.sub(r'[^\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\s\d.,;:!?\'"\-–—()\[\]]+', ' ', text)
    else:
        # Latin voices (en/de): keep Latin scripts, basic punctuation, whitespace
        # Strip CJK, Cyrillic, Arabic, Hebrew, emojis, symbols, zero-width chars
        return re.sub(r'[^\u0000-\u024F\u1E00-\u1EFF\u2000-\u206F\s\d.,;:!?\'"\-–—()\[\]]+|\u200B|\u200C|\u200D|\uFEFF|[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF]', ' ', text)

sanitized = []
for lang, chunk in segs:
    cleaned = sanitize_for_voice(lang, chunk).strip()
    if cleaned and speakable_count(cleaned) >= 2:
        sanitized.append((lang, cleaned))
segs = sanitized

# Fallback: if all segments were sanitized away but original text had content,
# try transliterating unsupported scripts so the selected voice can speak them.
if not segs and speakable_count(text) >= 2:
    try:
        from unidecode import unidecode
        roman = unidecode(text)
        roman = re.sub(r'\s+', ' ', roman).strip()
        if speakable_count(roman) >= 2:
            segs = [('en', roman)]  # Use English voice for transliterated text
    except Exception:
        pass

for i, (lang, chunk) in enumerate(segs):
    open(f'{workdir}/seg_{i:03d}.lang', 'w').write(lang)
    open(f'{workdir}/seg_{i:03d}.txt',  'w').write(chunk)

print(len(segs))
PYEOF
SEG_COUNT=$(python3 "$PY_SCRIPT" "$TEXT" "$WORK_DIR")
rm -f "$PY_SCRIPT"

fi  # end of source-language override / auto-detection block

# ── Split segments at major punctuation for natural pauses ────────────────────
# When pause_punctuation is enabled, break each segment at commas, periods,
# semicolons, colons, exclamation/question marks (including Arabic equivalents)
# so edge-tts generates each phrase with proper cadence.
split_punctuation_segments() {
    local workdir="$1" old_count="$2"
    local py_script
    py_script=$(mktemp)
    cat > "$py_script" <<'PYEOF'
import sys, re, os, unicodedata

def speakable_count(s):
    return sum(1 for ch in s if unicodedata.category(ch)[0] in ('L', 'N'))

workdir = sys.argv[1]
seg_count = int(sys.argv[2])

# Split at major punctuation followed by whitespace.
# Arabic equivalents included: ، ؛ ؟ 。
# Avoid splitting at decimal points (e.g., 3.14).
PUNCT_RE = re.compile(r'(?<=[,;:!?،؛؟。])\s+|(?<=\.)\s+(?!\d)')

def split_at_punct(text):
    parts = PUNCT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]

new_segs = []
for i in range(seg_count):
    txt_path = os.path.join(workdir, f'seg_{i:03d}.txt')
    lang_path = os.path.join(workdir, f'seg_{i:03d}.lang')
    if not os.path.exists(txt_path):
        continue
    with open(txt_path, 'r') as f:
        text = f.read()
    lang = 'en'
    if os.path.exists(lang_path):
        with open(lang_path, 'r') as f:
            lang = f.read().strip()

    chunks = split_at_punct(text)

    # Merge small chunks with neighbors to avoid NoAudioReceived
    merged = []
    buffer = ''
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        if speakable_count(chunk) >= 2:
            if buffer:
                chunk = buffer + ' ' + chunk
                buffer = ''
            merged.append(chunk)
        else:
            if merged:
                merged[-1] = merged[-1] + ' ' + chunk
            else:
                buffer = (buffer + ' ' + chunk).strip() if buffer else chunk

    if buffer and merged:
        merged[-1] = merged[-1] + ' ' + buffer
    elif buffer:
        merged.append(buffer)

    for chunk in merged:
        if speakable_count(chunk) >= 2:
            new_segs.append((lang, chunk))

# Remove old segment files
for i in range(seg_count):
    for ext in ('.txt', '.lang'):
        p = os.path.join(workdir, f'seg_{i:03d}{ext}')
        if os.path.exists(p):
            os.remove(p)

# Write new segment files
for i, (lang, chunk) in enumerate(new_segs):
    with open(os.path.join(workdir, f'seg_{i:03d}.txt'), 'w') as f:
        f.write(chunk)
    with open(os.path.join(workdir, f'seg_{i:03d}.lang'), 'w') as f:
        f.write(lang)

print(len(new_segs))
PYEOF
    local new_count
    new_count=$(python3 "$py_script" "$workdir" "$old_count")
    rm -f "$py_script"
    if [ -z "$new_count" ]; then
        echo "0"
        return 1
    fi
    echo "$new_count"
}

if [ "$PAUSE_PUNCTUATION" = "yes" ] && [ "$SEG_COUNT" -ge 1 ] 2>/dev/null; then
    SEG_COUNT=$(split_punctuation_segments "$WORK_DIR" "$SEG_COUNT")
fi

# ── Split long monolingual segments at sentence boundaries ────────────────────
# If a language segment exceeds MAX_SEGMENT_CHARS, break it into smaller
# sentence-sized chunks so progressive playback can start sooner.
MAX_SEGMENT_CHARS=600
split_long_segments() {
    local max=$1 old_count=$2
    local staging split_out
    staging="$WORK_DIR/.staging"
    split_out="$WORK_DIR/.split_out"
    mkdir -p "$staging" "$split_out"
    # Move all original segments to staging first
    local i
    for i in $(seq 0 $((old_count - 1))); do
        local src_txt src_lang
        src_txt="$WORK_DIR/seg_$(printf "%03d" "$i").txt"
        src_lang="$WORK_DIR/seg_$(printf "%03d" "$i").lang"
        [ -f "$src_txt" ] && mv "$src_txt" "$staging/seg_$(printf "%03d" "$i").txt"
        [ -f "$src_lang" ] && mv "$src_lang" "$staging/seg_$(printf "%03d" "$i").lang"
    done
    # Process from staging, write short ones directly, long ones to split_out
    local idx_counter=0
    for i in $(seq 0 $((old_count - 1))); do
        local tmp_txt tmp_lang text lang
        tmp_txt="$staging/seg_$(printf "%03d" "$i").txt"
        tmp_lang="$staging/seg_$(printf "%03d" "$i").lang"
        [ -f "$tmp_txt" ] || continue
        text=$(cat "$tmp_txt")
        lang=$(cat "$tmp_lang" 2>/dev/null || echo en)
        if [ ${#text} -le "$max" ]; then
            # Short segment: write to split_out so all segments share one pool
            printf '%s' "$text" > "$split_out/chunk_$(printf "%03d" "$idx_counter").txt"
            printf '%s' "$lang" > "$split_out/chunk_$(printf "%03d" "$idx_counter").lang"
            idx_counter=$((idx_counter + 1))
        else
            # Long segment: split to temporary split_out dir with unique indices
            local py_script
            py_script=$(mktemp)
            cat > "$py_script" <<'PYEOF'
import sys, re, os
text, max_len, lang, outdir, start_counter = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5])
abbrev = r'\b(?:Dr|Mr|Mrs|Ms|Prof|Jr|Sr|vs|etc|e\.g|i\.e|Inc|Ltd|Corp|No|no|fig|Fig|vol|pg|p)\.'
text = re.sub(abbrev, lambda m: m.group().replace('.', '\x00'), text)
parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'\u0600-\u06FF])', text)
parts = [p.replace('\x00', '.') for p in parts]
result = []
for p in parts:
    for s in p.split('\n\n'):
        s = s.strip()
        if s:
            result.append(s)
chunks, current = [], ''
for p in result:
    if not current:
        current = p
    elif len(current) + len(p) + 1 <= max_len:
        current += ' ' + p
    else:
        chunks.append(current)
        current = p
if current:
    chunks.append(current)
idx = start_counter
for ch in chunks:
    with open(os.path.join(outdir, f'chunk_{idx:03d}.txt'), 'w') as f:
        f.write(ch)
    with open(os.path.join(outdir, f'chunk_{idx:03d}.lang'), 'w') as f:
        f.write(lang)
    idx += 1
print(idx)
PYEOF
            idx_counter=$(python3 "$py_script" "${text}" "$max" "$lang" "$split_out" "$idx_counter")
            rm -f "$py_script" "$tmp_txt" "$tmp_lang"
            if [ -z "$idx_counter" ]; then
                rm -rf "$staging" "$split_out"
                echo "0"
                return 1
            fi
        fi
    done
    # Move split chunks from split_out to final location with proper sequential indices
    local final_idx f
    final_idx=0
    while IFS= read -r -d '' f; do
        local chunk_idx
        chunk_idx=$(basename "$f" .txt | sed 's/chunk_//')
        mv "$f" "$WORK_DIR/seg_$(printf "%03d" "$final_idx").txt"
        mv "$split_out/chunk_${chunk_idx}.lang" "$WORK_DIR/seg_$(printf "%03d" "$final_idx").lang"
        final_idx=$((final_idx + 1))
    done < <(find "$split_out" -maxdepth 1 -name 'chunk_*.txt' -print0 2>/dev/null | sort -z)
    rm -rf "$staging" "$split_out"
    echo "$final_idx"
}
SEG_COUNT=$(split_long_segments "$MAX_SEGMENT_CHARS" "$SEG_COUNT")

if [ -z "$SEG_COUNT" ] || ! [ "$SEG_COUNT" -ge 1 ] 2>/dev/null; then
    if [ -n "$OVERRIDE_TEXT" ]; then
        echo "STATUS:ERROR"
        notify-send "TTS Error" "Text contains no speakable content (e.g., only punctuation)."
        exit 1
    fi
    # Shortcut mode: accidental tiny/punctuation selection — skip silently
    exit 0
fi

# ═════════════════════════════════════════════════════════════════════════════
# GUI MODE  (--text was provided)
# ─────────────────────────────────────────────────────────────────────────────
# Progressive playback: start mpv as soon as the first segment is ready,
# then append remaining segments via IPC while generation continues.
# ═════════════════════════════════════════════════════════════════════════════
if [ -n "$OVERRIDE_TEXT" ]; then

    echo "STATUS:GENERATING"
    echo "GENERATING" > "$TTS_STATUS_FILE"

    # Launch all edge-tts jobs in parallel
    PIDS=""
    for i in $(seq 0 $((SEG_COUNT - 1))); do
        PAD=$(printf "%03d" "$i")
        LANG=$(cat "$WORK_DIR/seg_${PAD}.lang")
        SEG_TEXT=$(cat "$WORK_DIR/seg_${PAD}.txt")
        AUDIO="$WORK_DIR/seg_${PAD}.mp3"
        if [ "$LANG" = "ar" ]; then VOICE="$AR_VOICE"
        elif [ "$LANG" = "de" ]; then VOICE="$DE_VOICE"
        else VOICE="$EN_VOICE"; fi

        (
            # GUI mode: no --rate — playback speed is controlled live via mpv
            CACHED=$(cache_path "$VOICE" "_GUI_" "$SEG_TEXT")
            if [ -f "$CACHED" ]; then
                cp "$CACHED" "$AUDIO"
            else
                if ! generate_audio "$VOICE" "" "$SEG_TEXT" "$AUDIO" 2>"$WORK_DIR/err_${PAD}.txt"; then
                    exit 1
                fi
                cache_store "$AUDIO" "$CACHED"
            fi
        ) 200>&- &
        PIDS="$PIDS $!"
    done

    # ── Wait for first MP3, then start mpv immediately ──────────────────────
    FIRST_FILE="$WORK_DIR/seg_000.mp3"
    for _ in $(seq 1 300); do
        [ -f "$FIRST_FILE" ] && break
        sleep 0.1
    done

    if [ ! -f "$FIRST_FILE" ]; then
        FAILED=0
        for pid in $PIDS; do
            wait "$pid" || FAILED=1
        done
        ERR_MSG=$(cat "$WORK_DIR"/err_*.txt 2>/dev/null | grep -v '^+' | grep . | head -1)
        echo "STATUS:ERROR"
        notify-send "TTS Error" "${ERR_MSG:-Failed to generate speech. Check your internet connection.}"
        exit 1
    fi

    # A newer invocation may have asked us to stop while we were generating
    if [ -f "$INTERRUPT_FLAG" ]; then
        exit 0
    fi

    echo "STATUS:PLAYING"
    echo "PLAYING" > "$TTS_STATUS_FILE"

    INITIAL_SPEED="${OVERRIDE_SPEED:-1.5}"
    rm -f "$MPV_SOCKET"
    # Pre-generate silence file for inter-segment pauses
    SILENCE_FILE=""
    if [ "$PAUSE_PUNCTUATION" = "yes" ] && [ "$PAUSE_DELAY_MS" -gt 0 ]; then
        SILENCE_FILE=$(silence_file_for_delay "$PAUSE_DELAY_MS")
    fi

    mpv "$FIRST_FILE" --no-terminal --idle --input-ipc-server="$MPV_SOCKET" 200>&- &
    MPV_PID=$!

    # Wait for socket, then set initial playback speed
    for _ in $(seq 1 30); do
        sleep 0.1
        if [ -S "$MPV_SOCKET" ]; then
            printf '{"command":["set_property","speed",%s]}\n' "$INITIAL_SPEED" \
                | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null 200>&-
            break
        fi
    done

    # ── Background: append remaining segments in order via IPC ──────────────
    (
        exec 200>&-
        CURRENT_IDX=0
        while true; do
            NEXT_IDX=$((CURRENT_IDX + 1))
            NEXT_FILE="$WORK_DIR/seg_$(printf "%03d" "$NEXT_IDX").mp3"

            if [ -f "$NEXT_FILE" ] && [ -s "$NEXT_FILE" ]; then
                if [ -S "$MPV_SOCKET" ]; then
                    if printf '{"command":["loadfile","%s","append"]}\n' "$NEXT_FILE" \
                            | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null 200>&-; then
                        CURRENT_IDX=$NEXT_IDX
                        # Inject silence between segments when configured
                        if [ -n "$SILENCE_FILE" ] && [ "$NEXT_IDX" -lt "$SEG_COUNT" ]; then
                            printf '{"command":["loadfile","%s","append"]}\n' "$SILENCE_FILE" \
                                | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null 200>&-
                        fi
                    fi
                fi
            fi

            # Check if all generation jobs are done
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
            sleep 0.1
        done
    ) &
    APPEND_PID=$!

    # ── Wait for all generation to complete (foreground) ──────────────────
    FAILED=0
    for pid in $PIDS; do
        wait "$pid" || FAILED=1
    done

    if [ "$FAILED" -ne 0 ]; then
        ERR_MSG=$(cat "$WORK_DIR"/err_*.txt 2>/dev/null | grep -v '^+' | grep . | head -1)
        # Don't kill mpv — let partial playback continue for better UX
        notify-send "TTS Warning" "${ERR_MSG:-Some segments failed to generate.}"
    fi

    # A newer invocation may have asked us to stop
    if [ -f "$INTERRUPT_FLAG" ]; then
        exit 0
    fi

    # Wait for background append loop to finish
    wait "$APPEND_PID"

    # All files appended. Poll mpv until idle, then quit it.
    IDLE_COUNT=0
    while kill -0 "$MPV_PID" 2>/dev/null; do
        if [ -S "$MPV_SOCKET" ]; then
            if echo '{"command":["get_property","core-idle"]}' | socat - "$MPV_SOCKET" 2>/dev/null | grep -q '"data":true'; then
                # core-idle is true when paused AND when finished — distinguish them
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
        sleep 0.5
    done

    wait "$MPV_PID"
    rm -f "$MPV_SOCKET"
    echo "IDLE" > "$TTS_STATUS_FILE"

    # Export to file if --output was requested
    if [ -n "$OUTPUT_FILE" ]; then
        mapfile -t FILES < <(find "$WORK_DIR" -maxdepth 1 -name 'seg_*.mp3' | sort)
        if command -v ffmpeg &>/dev/null; then
            LIST_FILE=$(mktemp)
            for f in "${FILES[@]}"; do
                printf "file '%s'\n" "$f" >> "$LIST_FILE"
            done
            ffmpeg -y -f concat -safe 0 -i "$LIST_FILE" -c copy "$OUTPUT_FILE" 2>/dev/null
            rm -f "$LIST_FILE"
        else
            cat "${FILES[@]}" > "$OUTPUT_FILE"
        fi
        notify-send "TTS Export" "Saved audio to $OUTPUT_FILE"
    fi

# ═════════════════════════════════════════════════════════════════════════════
# SHORTCUT MODE  (keyboard shortcut, no --text arg)
# ─────────────────────────────────────────────────────────────────────────────
# Progressive playback: start mpv as soon as the first segment is ready,
# then append remaining segments via IPC while generation continues.
# ═════════════════════════════════════════════════════════════════════════════
else

    echo "GENERATING" > "$TTS_STATUS_FILE"

    # Generate all audio files in parallel
    PIDS=""
    for i in $(seq 0 $((SEG_COUNT - 1))); do
        PAD=$(printf "%03d" "$i")
        LANG=$(cat "$WORK_DIR/seg_${PAD}.lang")
        SEG_TEXT=$(cat "$WORK_DIR/seg_${PAD}.txt")
        AUDIO="$WORK_DIR/seg_${PAD}.mp3"

        if [ "$LANG" = "ar" ]; then VOICE="$AR_VOICE"; RATE="$AR_RATE"
        elif [ "$LANG" = "de" ]; then VOICE="$DE_VOICE"; RATE="$DE_RATE"
        else VOICE="$EN_VOICE"; RATE="$EN_RATE"; fi

        (
            CACHED=$(cache_path "$VOICE" "$RATE" "$SEG_TEXT")
            if [ -f "$CACHED" ]; then
                cp "$CACHED" "$AUDIO"
            else
                if ! generate_audio "$VOICE" "$RATE" "$SEG_TEXT" "$AUDIO" 2>"$WORK_DIR/err_${PAD}.txt"; then
                    exit 1
                fi
                cache_store "$AUDIO" "$CACHED"
            fi
        ) 200>&- &
        PIDS="$PIDS $!"
    done

    # ── Progressive playback: start mpv on first ready segment ──────────────
    FIRST_FILE="$WORK_DIR/seg_000.mp3"
    for _ in $(seq 1 300); do
        [ -f "$FIRST_FILE" ] && break
        sleep 0.1
    done

    if [ ! -f "$FIRST_FILE" ]; then
        FAILED=0
        for pid in $PIDS; do
            wait "$pid" || FAILED=1
        done
        ERR_MSG=$(cat "$WORK_DIR"/err_*.txt 2>/dev/null | grep -v '^+' | grep . | head -1)
        notify-send "TTS Error" "${ERR_MSG:-Failed to generate speech. Check your internet connection.}"
        exit 1
    fi

    # A newer invocation may have asked us to stop while we were generating
    if [ -f "$INTERRUPT_FLAG" ]; then
        exit 0
    fi

    # Pre-generate silence file for inter-segment pauses
    SILENCE_FILE=""
    if [ "$PAUSE_PUNCTUATION" = "yes" ] && [ "$PAUSE_DELAY_MS" -gt 0 ]; then
        SILENCE_FILE=$(silence_file_for_delay "$PAUSE_DELAY_MS")
    fi

    echo "PLAYING" > "$TTS_STATUS_FILE"
    rm -f "$MPV_SOCKET"
    mpv "$FIRST_FILE" --no-terminal --idle --input-ipc-server="$MPV_SOCKET" 200>&- &
    MPV_PID=$!

    # ── Background: append remaining segments in order via IPC ──────────────
    (
        exec 200>&-
        CURRENT_IDX=0
        while true; do
            NEXT_IDX=$((CURRENT_IDX + 1))
            NEXT_FILE="$WORK_DIR/seg_$(printf "%03d" "$NEXT_IDX").mp3"

            if [ -f "$NEXT_FILE" ] && [ -s "$NEXT_FILE" ]; then
                if [ -S "$MPV_SOCKET" ]; then
                    if printf '{"command":["loadfile","%s","append"]}\n' "$NEXT_FILE" \
                            | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null 200>&-; then
                        CURRENT_IDX=$NEXT_IDX
                        # Inject silence between segments when configured
                        if [ -n "$SILENCE_FILE" ] && [ "$NEXT_IDX" -lt "$SEG_COUNT" ]; then
                            printf '{"command":["loadfile","%s","append"]}\n' "$SILENCE_FILE" \
                                | socat - "$MPV_SOCKET" 2>/dev/null >/dev/null 200>&-
                        fi
                    fi
                fi
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
            sleep 0.1
        done
    ) &
    APPEND_PID=$!

    # ── Wait for all generation to complete (foreground) ────────────────────
    FAILED=0
    for pid in $PIDS; do
        wait "$pid" || FAILED=1
    done

    if [ "$FAILED" -ne 0 ]; then
        ERR_MSG=$(cat "$WORK_DIR"/err_*.txt 2>/dev/null | grep -v '^+' | grep . | head -1)
        notify-send "TTS Warning" "${ERR_MSG:-Some segments failed to generate.}"
    fi

    # A newer invocation may have asked us to stop
    if [ -f "$INTERRUPT_FLAG" ]; then
        exit 0
    fi

    # Export to file if --output was requested
    if [ -n "$OUTPUT_FILE" ]; then
        mapfile -t FILES < <(find "$WORK_DIR" -maxdepth 1 -name 'seg_*.mp3' | sort)
        if command -v ffmpeg &>/dev/null; then
            LIST_FILE=$(mktemp)
            for f in "${FILES[@]}"; do
                printf "file '%s'\n" "$f" >> "$LIST_FILE"
            done
            ffmpeg -y -f concat -safe 0 -i "$LIST_FILE" -c copy "$OUTPUT_FILE" 2>/dev/null
            rm -f "$LIST_FILE"
        else
            cat "${FILES[@]}" > "$OUTPUT_FILE"
        fi
        notify-send "TTS Export" "Saved audio to $OUTPUT_FILE"
    fi

    # Wait for background append loop to finish
    wait "$APPEND_PID"

    # All files appended. Poll mpv until idle, then quit it.
    IDLE_COUNT=0
    while kill -0 "$MPV_PID" 2>/dev/null; do
        if [ -S "$MPV_SOCKET" ]; then
            if echo '{"command":["get_property","core-idle"]}' | socat - "$MPV_SOCKET" 2>/dev/null | grep -q '"data":true'; then
                # core-idle is true when paused AND when finished — distinguish them
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
        sleep 0.5
    done

    wait "$MPV_PID"
    rm -f "$MPV_SOCKET"
    echo "IDLE" > "$TTS_STATUS_FILE"

fi
