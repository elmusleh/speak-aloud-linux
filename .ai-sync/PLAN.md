# Implementation Plan — Speak a Loud Universal Facelift & Engine Upgrade

## Goal
Upgrade `/home/steve/dev/tts/speak-aloud-linux` to a leading-standard Linux desktop TTS system inspired by `/home/steve/dev/Sandbox/obsidian-edge-tts`, fixing all existing playback/synthesis/settings bugs and introducing a modern floating on-screen player HUD (`FloatingPlayerUI`), chunk progress tracker (`ChunkedProgressUI`), playback queue manager (`QueueManagerUI`), and facelifted settings dialog.

## Phase 1: Core Engine & Bug Fixes (`speak.sh`, `tts_engine.py`, `speak-pause.sh`, `speak-stop.sh`)
1. **Fix `speak.sh` critical bugs**:
   - Remove duplicate CLI argument loop so `--generate-only`, `--queue`, and all flags parse in a single pass.
   - Fix stray `fi` at line 1236 that caused `--text` invocations to fall through and play audio twice.
   - Normalize boolean config values (`yes`/`true`/`TRUE`/`1` -> `yes`, `no`/`false`/`FALSE`/`0` -> `no`) so YAD and GUI settings never break `speak.sh`.
   - Optimize X11 active-window office-app detection (query `xprop -id $ACTIVE_WID WM_CLASS` directly in 1ms instead of 4 full `xdotool search --class` scans).
2. **Smart Text Filtering & Fast Trilingual Chunking**:
   - Port `obsidian-edge-tts`'s markdown/text cleaning (`filterFrontmatter`, `filterMarkdown`, code-block/URL/HTML/comment stripping, comparison symbol replacement `>=`, `<=`, bullet list normalization) while preserving English, Arabic, and German text.
   - Fix chunk splitting so bulleted lists, single newlines after colons/headings, and technical passages split cleanly into natural sentence chunks (~200 chars for `seg_000` for sub-second startup, ~450 chars for subsequent chunks).
   - Avoid comma-level micro-splitting (`split_punctuation_segments`) unless explicitly requested with a non-zero inter-segment delay, since `edge-tts` handles intra-sentence comma prosody natively and 20+ micro-processes caused 6–12s lag.
   - Use direct Python `edge_tts` async synthesis (via pipx venv site-packages with CLI fallback) + structured chunk status metadata (`/tmp/speak-aloud-work/manifest.json` & `chunk_XXX.status`) for real-time progress reporting.
   - Keep `mpv` alive with `--keep-open=yes` at the end of playback so the Floating Player supports instant **Replay** and seeking back after completion (matching `obsidian-edge-tts`), while updating status accurately.

## Phase 2: Floating On-Screen Player HUD, Chunk Progress & Queue Manager (`tts-player.py` / `tts-daemon.py`)
1. **Floating Player Popup (`FloatingPlayerWindow` — inspired by `FloatingPlayerUI.tsx`)**:
   - Frameless, translucent, always-on-top draggable HUD window (`PyQt6` + `QtSvg` vector icons matching Lucide icons from `obsidian-edge-tts`).
   - Remembers screen position (`~/.config/tts_settings/player_position.json`) and clamps to screen bounds on resize/monitor changes.
   - **Unified Timeline Scrubber**: Computes global `currentTime` and `totalDuration` across all segments (`filesize / 6000.0` CBR 48kbps + live `mpv` IPC `playlist-pos` & `time-pos`) so the seek slider and `-10s` / `+10s` buttons seek smoothly across the entire text, even across segment boundaries.
   - **Transport Controls**:
     - Jump Backward `-10s` (`rotate-ccw`)
     - Play / Pause (`play` / `pause`) and **Replay** (`rotate-cw`) when at the end
     - Jump Forward `+10s` (`rotate-cw`)
     - Stop (`square`)
     - Close (`x`) button
     - Live **Speed Pill** (`0.75x`, `1.0x`, `1.25x`, `1.5x`, `1.75x`, `2.0x`) that adjusts `mpv` playback speed in real time
     - **Save MP3** button (`download`) to export the current speech audio
     - **Add Selection to Queue** (`plus`) & **Toggle Queue** (`list-music`) buttons
2. **Chunked Progress View (inspired by `ChunkedProgressUI.tsx`)**:
   - Embedded collapsible progress section showing phase (`Splitting...`, `Generating audio chunks...`, `Ready`), overall progress bar + percentage, chunk counters (`Completed: X / Total: Y`), and individual chunk status pills (`1 ✓`, `2 ⟳`, `3 ○`).
3. **Playback Queue Manager (`QueueManagerWindow` — inspired by `QueueManagerUI.tsx`)**:
   - Draggable companion panel displaying queued items (`QueueItem { title, text }`), item count badge, Auto-scroll toggle, Loop toggle, Play Queue from beginning, Clear Queue (`trash-2`), and per-item Move Up (`chevron-up`), Move Down (`chevron-down`), Remove (`x`), and click-to-play.
   - Support `speak.sh --queue` (and tray/player button) to enqueue currently highlighted text while audio is already playing or idle.

## Phase 3: Facelifted Settings GUI (`tts-settings.py` & `tts-settings.sh`)
1. Build a modern dark-slate PyQt6 Settings window (`tts-settings.py`, launched by `tts-settings.sh` with YAD fallback):
   - **Voices & Speed Tab**: Human-friendly voice dropdowns (`Christopher (US)`, `Hamed (Saudi)`, `Conrad (DE)`, expanded voice catalog in `tts-shared.json`), per-language speed sliders, default playback speed multiplier, Custom Voice override, and a live **"Test Voice"** preview button.
   - **Text & Playback Tab**: Floating Player toggle, Replay-at-end toggle, Markdown/Frontmatter/URL/Code filtering toggles (from `obsidian-edge-tts`), punctuation pause settings.
   - **Translation & Cache Tab**: Translation enable/provider/target/API key, plus live Cache stats (file count & MB used in `~/.cache/speak-aloud`) and a **"Clear Cache"** button.

## Phase 4: Verification & Documentation
1. Verify syntax, unit/integration tests, live MP3 synthesis, multi-segment seeking, queue playback, floating player UI rendering, and settings persistence.
2. Update `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, and `.ai-sync/` handoff files, and commit cleanly.

## Phase 5: Post-Implementation Enhancements (All 9 Suggestions)
1. **Per-User Runtime Directory (`$XDG_RUNTIME_DIR/speak-aloud`)**:
   - Store IPC sockets (`mpv.sock`, `synth.sock`), lock files, PID files, and segment work directory (`work/`) in `${XDG_RUNTIME_DIR:-/tmp}/speak-aloud-$UID` with `0700` permissions, while maintaining backward-compatible symlinks in `/tmp` for legacy external callers.
2. **Persistent Async Python Synthesis Worker (`edge-tts` Keep-Alive Daemon)**:
   - Build `tts_synth_worker.py` (running inside the `edge-tts` Python environment and supervised by `tts-daemon.py`) listening on `$RUNTIME_DIR/synth.sock` to synthesize chunks via `edge_tts.Communicate` in-memory without per-segment interpreter/TLS startup overhead.
3. **Local Offline Neural Fallback (`piper-tts`) When Offline or Rate-Limited**:
   - Automatically fall back to `piper` / `piper-tts` with local ONNX voice models (`~/.local/share/piper` or `/usr/share/piper-voices`) when `edge-tts` fails after retries, before dropping to `espeak-ng`.
4. **Systemd User Service (`speak-aloud-daemon.service`)**:
   - Provide and install `~/.config/systemd/user/speak-aloud-daemon.service` with `Restart=on-failure` and `systemctl --user` integration in `install.sh`.
5. **Interactive Sentence Transcript Highlighting in the Floating HUD**:
   - Add a collapsible live Transcript drawer in `FloatingPlayerWindow` that highlights and auto-scrolls the currently playing segment (`seg_XXX`) and jumps to any segment on click.
6. **Strict Schema Validation & Atomic Writes for `~/.config/tts_settings`**:
   - Add `TTSSettingsStore` in `tts_text_processor.py` with schema validation, range clamping, atomic `tempfile` + `os.replace()` writes to `~/.config/tts_settings/settings.json`, and synchronized flat files.
7. **Keyboard Navigation & Hotkeys Inside the Floating HUD**:
   - Implement `keyPressEvent` in `FloatingPlayerWindow` (`Space` play/pause/replay, `Left`/`Right` seek `-5s`/`+5s`, `[`/`]` speed step, `T` toggle transcript, `Q` toggle queue, `S` stop, `Esc` hide).
8. **OCR Screen-Region Speak Mode (`--ocr`)**:
   - Add `speak.sh --ocr` and `tts_ocr.py` (with PyQt6 interactive screen-region rubber-band capture + `gnome-screenshot`/`maim` support + `tesseract` OCR) and expose an OCR button in the Floating HUD, Tray menu, and Settings Studio.
9. **Pronunciation Lexicon / Custom Alias Dictionary (`~/.config/tts_settings/lexicon.json`)**:
   - Support case-insensitive word-boundary pronunciation replacements in `tts_text_processor.py` with built-in technical defaults (`kubectl`, `PyQt6`, `nginx`, `SQLite`, etc.) and a Lexicon Editor in `tts-settings.py`.
