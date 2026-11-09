# System Architecture

Speak a Loud Universal combines a fast shell/Python synthesis pipeline with a persistent PyQt6 system tray and Floating On-Screen Player HUD (`tts-daemon.py` + `tts_hud.py`).

## 🧱 Component Overview

### 1. The Orchestrator (`speak.sh`)
Handles text acquisition (X11 primary selection, Office `WM_CLASS` + `Ctrl+C`, Wayland `wl-paste`, AT-SPI `get-selected-text.py`, clipboard fallback), optional translation (`translate_text.py`), parallel `edge-tts` synthesis with caching (`~/.cache/speak-aloud/`), and progressive `mpv` playback.

### 2. Smart Text Processor & Trilingual Segmenter (`tts_text_processor.py`)
Single-pass Python processor that:
- Filters Markdown formatting, YAML frontmatter, raw URLs, code blocks, and converts comparison/dimension symbols (`>=`, `<=`, `320 x 240`, `≈`).
- Splits text into English, Arabic, and German segments using deterministic `langdetect` + German function-word verification.
- Uses **Fast-First-Chunk splitting** (`seg_000` <= 220 chars, subsequent chunks <= 480 chars) so playback begins in under a second.
- Writes `$XDG_RUNTIME_DIR/speak-aloud-$UID/work/manifest.json` (`0700` isolated runtime directory with `/tmp` symlinks) and `seg_XXX.status` files for real-time HUD tracking.
- Applies user-customizable **Pronunciation Lexicon** aliases (`~/.config/tts_settings/lexicon.json`) and manages **Schema-Validated Atomic Settings** (`TTSSettingsStore` / `settings.json`).

### 3. Persistent Async Synthesis Worker & Piper Fallback (`tts_synth_worker.py`)
- Runs an `asyncio` UNIX domain socket server (`$XDG_RUNTIME_DIR/speak-aloud-$UID/synth.sock`, `0600`) inside `tts-daemon.py` with `edge_tts` pre-imported in memory.
- Falls back automatically to local `piper` / `piper-tts` ONNX neural models when offline or rate-limited before `speak.sh` falls back to `espeak-ng`.

### 4. OCR Screen-Region & Image Extractor (`tts_ocr.py`)
- Provides a built-in PyQt6 full-screen rubber-band region selector (`speak.sh --ocr`) and `--ocr-image <file>` mode powered by `tesseract` (`eng+deu+ara`).

### 5. Floating Player HUD, Live Transcript, Chunk Progress & Queue Manager (`tts_hud.py` & `tts-daemon.py`)
- **`MPVTimelineController`**: Communicates directly with `$XDG_RUNTIME_DIR/speak-aloud-$UID/mpv.sock` over `socket.AF_UNIX` (sub-millisecond latency) and calculates a unified global timeline across all segment files (`filesize / 6000.0` CBR 48kbps MP3) for seamless cross-segment seeking, `-10s` / `+10s` jumps, segment jumps, live speed changes, and post-playback **Replay**.
- **`FloatingPlayerWindow`**: Always-on-top, frameless, draggable HUD with timeline scrubber, transport controls, keyboard hotkeys (`Space`, `←`/`→`, `[`/`]`, `T`, `Q`, `O`, `Esc`), speed pill, OCR trigger, MP3 export, collapsible **Chunked Generation Progress** pills, and a collapsible **Live Interactive Transcript Drawer**.
- **`QueueManagerWindow`**: Draggable companion window managing `queue.json` with auto-advance, loop mode, item reordering, and one-click selection queueing.
- **Systemd User Service (`speak-aloud-daemon.service`)**: Supervises `tts-daemon.py` with `Restart=on-failure`.

### 6. Settings & Studio GUI (`tts-settings.py` / `tts-settings.sh`)
Tabbed PyQt6 dark-slate configuration studio for voices, per-language rates, live voice previews, Markdown filtering rules, pronunciation lexicon editor, translation, Quick Speak/OCR/Export bench, and cache management (with YAD fallback via `tts-settings.sh --yad`).
