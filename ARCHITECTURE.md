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
- Writes `/tmp/speak-aloud-work/manifest.json` and `seg_XXX.status` files for real-time HUD tracking.

### 3. Floating Player HUD, Chunk Progress & Queue Manager (`tts_hud.py` & `tts-daemon.py`)
- **`MPVTimelineController`**: Communicates directly with `/tmp/speak-aloud-mpv.sock` over `socket.AF_UNIX` (sub-millisecond latency) and calculates a unified global timeline across all segment files (`filesize / 6000.0` CBR 48kbps MP3) for seamless cross-segment seeking, `-10s` / `+10s` jumps, live speed changes, and post-playback **Replay**.
- **`FloatingPlayerWindow`**: Always-on-top, frameless, draggable HUD with timeline scrubber, transport controls, speed pill, MP3 export, and collapsible **Chunked Generation Progress** pills.
- **`QueueManagerWindow`**: Draggable companion window managing `/tmp/speak-aloud-queue.json` with auto-advance, loop mode, item reordering, and one-click selection queueing.

### 4. Settings & Studio GUI (`tts-settings.py` / `tts-settings.sh`)
Tabbed PyQt6 dark-slate configuration studio for voices, per-language rates, live voice previews, Markdown filtering rules, translation, Quick Speak/Export bench, and cache management (with YAD fallback via `tts-settings.sh --yad`).
