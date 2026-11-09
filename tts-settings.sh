#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$HOME/.config/tts_settings"
mkdir -p "$CONFIG_DIR"

# Prefer the modern PyQt6 Studio Settings GUI unless --yad is requested
if [ "${1:-}" != "--yad" ] && python3 -c "import PyQt6.QtWidgets" 2>/dev/null; then
    exec python3 "$SCRIPT_DIR/tts-settings.py" "$@"
fi

# ── Fallback: YAD settings dialog (with boolean normalization fix) ───────────
_json_get() {
    python3 -c "
import json
d = json.load(open('$SCRIPT_DIR/tts-shared.json'))
$1
"
}

normalize_bool_to_yad() {
    case "${1,,}" in
        yes|true|1|on) echo "TRUE" ;;
        *)             echo "FALSE" ;;
    esac
}

normalize_bool_from_yad() {
    case "${1,,}" in
        yes|true|1|on) echo "yes" ;;
        *)             echo "no" ;;
    esac
}

DEFAULT_VOICE=$(_json_get "print(d['defaults']['voice'])")
DEFAULT_AR_VOICE=$(_json_get "print(d['defaults']['arabic_voice'])")
DEFAULT_DE_VOICE=$(_json_get "print(d['defaults']['german_voice'])")
DEFAULT_RATE=$(_json_get "print(d['defaults']['rate'])")
DEFAULT_AR_RATE=$(_json_get "print(d['defaults']['arabic_rate'])")
DEFAULT_DE_RATE=$(_json_get "print(d['defaults']['german_rate'])")
DEFAULT_SOURCE_LANG=$(_json_get "print(d['defaults']['source_language'])")
DEFAULT_TRANSLATE_ENABLED=$(_json_get "print('yes' if d['defaults']['translate_enabled'] else 'no')")
DEFAULT_TRANSLATE_PROVIDER=$(_json_get "print(d['defaults']['translate_provider'])")
DEFAULT_TRANSLATE_TARGET=$(_json_get "print(d['defaults']['translate_target'])")

CURRENT_VOICE=$(cat    "$CONFIG_DIR/voice"         2>/dev/null || echo "$DEFAULT_VOICE")
CURRENT_AR_VOICE=$(cat "$CONFIG_DIR/arabic_voice"  2>/dev/null || echo "$DEFAULT_AR_VOICE")
CURRENT_DE_VOICE=$(cat "$CONFIG_DIR/german_voice"  2>/dev/null || echo "$DEFAULT_DE_VOICE")
CURRENT_RATE=$(tr -d '+%' < "$CONFIG_DIR/rate" 2>/dev/null)
CURRENT_AR_RATE=$(tr -d '+%' < "$CONFIG_DIR/arabic_rate" 2>/dev/null)
CURRENT_DE_RATE=$(tr -d '+%' < "$CONFIG_DIR/german_rate" 2>/dev/null)
CURRENT_SOURCE_LANG=$(cat "$CONFIG_DIR/source_language" 2>/dev/null || echo "$DEFAULT_SOURCE_LANG")
CURRENT_TRANSLATE_ENABLED=$(normalize_bool_to_yad "$(cat "$CONFIG_DIR/translate_enabled" 2>/dev/null || echo "$DEFAULT_TRANSLATE_ENABLED")")
CURRENT_TRANSLATE_PROVIDER=$(cat "$CONFIG_DIR/translate_provider" 2>/dev/null || echo "$DEFAULT_TRANSLATE_PROVIDER")
CURRENT_TRANSLATE_TARGET=$(cat "$CONFIG_DIR/translate_target" 2>/dev/null || echo "$DEFAULT_TRANSLATE_TARGET")
CURRENT_TRANSLATE_KEY=$(cat "$CONFIG_DIR/translate_api_key" 2>/dev/null || echo "")
CURRENT_PAUSE_PUNCTUATION=$(normalize_bool_to_yad "$(cat "$CONFIG_DIR/pause_punctuation" 2>/dev/null || echo "no")")
CURRENT_PAUSE_DELAY_MS=$(cat "$CONFIG_DIR/pause_delay_ms" 2>/dev/null || echo "100")
[ -z "$CURRENT_RATE" ]    && CURRENT_RATE=$DEFAULT_RATE
[ -z "$CURRENT_AR_RATE" ] && CURRENT_AR_RATE=$DEFAULT_AR_RATE
[ -z "$CURRENT_DE_RATE" ] && CURRENT_DE_RATE=$DEFAULT_DE_RATE

EN_VOICES=$(_json_get "
voices = [v['id'] for v in d['voices']['english']]
print('$CURRENT_VOICE' + '!' + '!'.join(voices))
")
AR_VOICES=$(_json_get "
voices = [v['id'] for v in d['voices']['arabic']]
print('$CURRENT_AR_VOICE' + '!' + '!'.join(voices))
")
DE_VOICES=$(_json_get "
voices = [v['id'] for v in d['voices']['german']]
print('$CURRENT_DE_VOICE' + '!' + '!'.join(voices))
")
SRC_LANGS=$(_json_get "
codes = [l['code'] for l in d['source_languages']]
print('$CURRENT_SOURCE_LANG' + '!' + '!'.join(codes))
")
TRANS_PROVIDERS=$(_json_get "
codes = [p['code'] for p in d['translation_providers']]
print('$CURRENT_TRANSLATE_PROVIDER' + '!' + '!'.join(codes))
")
TRANS_TARGETS=$(_json_get "
codes = [l['code'] for l in d['translation_languages']]
print('$CURRENT_TRANSLATE_TARGET' + '!' + '!'.join(codes))
")

CACHE_DIR="$HOME/.cache/speak-aloud"

RESULTS=$(yad --title="TTS Settings" --form --width=450 \
    --field="English Voice:CB"              "$EN_VOICES" \
    --field="Arabic Voice:CB"               "$AR_VOICES" \
    --field="German Voice:CB"               "$DE_VOICES" \
    --field="English Speed (+%):HSCALE"     "$CURRENT_RATE" \
    --field="Arabic Speed (+%):HSCALE"      "$CURRENT_AR_RATE" \
    --field="German Speed (+%):HSCALE"      "$CURRENT_DE_RATE" \
    --field="Custom Voice ID (optional):TEXT" "" \
    --field="Source Language:CB"            "$SRC_LANGS" \
    --field="Translate Enabled:CHK"         "$CURRENT_TRANSLATE_ENABLED" \
    --field="Translate Provider:CB"         "$TRANS_PROVIDERS" \
    --field="Translate Target:CB"           "$TRANS_TARGETS" \
    --field="Translate API Key:TEXT"        "$CURRENT_TRANSLATE_KEY" \
    --field="Pause at Punctuation:CHK"      "$CURRENT_PAUSE_PUNCTUATION" \
    --field="Pause Delay (ms):NUM"          "$CURRENT_PAUSE_DELAY_MS" \
    --field="Cache Folder ($CACHE_DIR):LBL" "" \
    --button="Save":0 --button="Cancel":1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    EN_VOICE=$(echo    "$RESULTS" | cut -d'|' -f1)
    AR_VOICE=$(echo    "$RESULTS" | cut -d'|' -f2)
    DE_VOICE=$(echo    "$RESULTS" | cut -d'|' -f3)
    EN_RATE=$(echo     "$RESULTS" | cut -d'|' -f4 | cut -d'.' -f1 | grep -o '[0-9]\+' | head -1)
    AR_RATE=$(echo     "$RESULTS" | cut -d'|' -f5 | cut -d'.' -f1 | grep -o '[0-9]\+' | head -1)
    DE_RATE=$(echo     "$RESULTS" | cut -d'|' -f6 | cut -d'.' -f1 | grep -o '[0-9]\+' | head -1)
    CUSTOM_VOICE=$(echo "$RESULTS" | cut -d'|' -f7)
    SOURCE_LANG=$(echo   "$RESULTS" | cut -d'|' -f8)
    TRANSLATE_ENABLED=$(normalize_bool_from_yad "$(echo "$RESULTS" | cut -d'|' -f9)")
    TRANSLATE_PROVIDER=$(echo "$RESULTS" | cut -d'|' -f10)
    TRANSLATE_TARGET=$(echo "$RESULTS" | cut -d'|' -f11)
    TRANSLATE_KEY=$(echo "$RESULTS" | cut -d'|' -f12)
    PAUSE_PUNCTUATION=$(normalize_bool_from_yad "$(echo "$RESULTS" | cut -d'|' -f13)")
    PAUSE_DELAY_MS=$(echo "$RESULTS" | cut -d'|' -f14 | cut -d'.' -f1 | grep -o '[0-9]\+' | head -1)
    PAUSE_DELAY_MS=${PAUSE_DELAY_MS:-100}
    [ "$PAUSE_DELAY_MS" -lt 0 ] 2>/dev/null && PAUSE_DELAY_MS=0
    [ "$PAUSE_DELAY_MS" -gt 2000 ] 2>/dev/null && PAUSE_DELAY_MS=2000

    EN_RATE=${EN_RATE:-$DEFAULT_RATE}
    AR_RATE=${AR_RATE:-$DEFAULT_AR_RATE}
    DE_RATE=${DE_RATE:-$DEFAULT_DE_RATE}

    validate_voice() {
        local voice="$1" fallback="$2"
        if echo "$voice" | grep -q '^[a-zA-Z][a-zA-Z0-9-]*$'; then
            echo "$voice"
        else
            echo "$fallback"
        fi
    }

    EN_VOICE=$(validate_voice "$EN_VOICE" "$DEFAULT_VOICE")
    AR_VOICE=$(validate_voice "$AR_VOICE" "$DEFAULT_AR_VOICE")
    DE_VOICE=$(validate_voice "$DE_VOICE" "$DEFAULT_DE_VOICE")
    [ -n "$CUSTOM_VOICE" ] && EN_VOICE=$(validate_voice "$CUSTOM_VOICE" "$EN_VOICE")

    echo "$EN_VOICE"           > "$CONFIG_DIR/voice"
    echo "$AR_VOICE"           > "$CONFIG_DIR/arabic_voice"
    echo "$DE_VOICE"           > "$CONFIG_DIR/german_voice"
    echo "+${EN_RATE}%"        > "$CONFIG_DIR/rate"
    echo "+${AR_RATE}%"        > "$CONFIG_DIR/arabic_rate"
    echo "+${DE_RATE}%"        > "$CONFIG_DIR/german_rate"
    echo "$SOURCE_LANG"        > "$CONFIG_DIR/source_language"
    echo "$TRANSLATE_ENABLED"  > "$CONFIG_DIR/translate_enabled"
    echo "$TRANSLATE_PROVIDER" > "$CONFIG_DIR/translate_provider"
    echo "$TRANSLATE_TARGET"   > "$CONFIG_DIR/translate_target"
    echo "$TRANSLATE_KEY"      > "$CONFIG_DIR/translate_api_key"
    echo "$PAUSE_PUNCTUATION"  > "$CONFIG_DIR/pause_punctuation"
    echo "$PAUSE_DELAY_MS"     > "$CONFIG_DIR/pause_delay_ms"
fi
