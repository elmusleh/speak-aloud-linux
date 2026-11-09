# Changelog

## 2026-11-09

- **Floating On-Screen Player HUD (`tts_hud.py`, `tts-daemon.py`)**: Added an always-on-top, draggable dark-slate player popup inspired by `obsidian-edge-tts` (`FloatingPlayerUI`), featuring a unified cross-segment timeline seek bar, elapsed/total timecodes, Play/Pause/Replay, Jump `-10s`/`+10s`, Stop, live Speed selector pill (`0.75x`–`2.0x`), and one-click Save MP3.
- **Chunked Generation Progress (`ChunkedProgressUI`)**: Added real-time chunk synthesis progress bar and per-chunk status pills (`1 ✓`, `2 ⟳`, `3 ○`) backed by `/tmp/speak-aloud-work/manifest.json` and `seg_XXX.status`.
- **Playback Queue Manager (`QueueManagerUI`)**: Added a draggable companion Queue window and `speak.sh --queue` flag to enqueue selections, reorder items, loop, and auto-advance through queued passages.
- **Smart Markdown & Technical Text Processor (`tts_text_processor.py`)**: Added Markdown/frontmatter/URL/code filtering, math & comparison symbol speech conversion (`320 x 240` → `320 by 240`, `>=`, `<=`, `≈`), bullet list normalization, deterministic German detection (preventing false positives on English technical text), and fast-first-chunk splitting (~220 chars for `seg_000`).
- **Facelifted Settings & Studio GUI (`tts-settings.py`, `tts-settings.sh`)**: Replaced the YAD form with a 3-tab PyQt6 Studio Settings dialog featuring expanded voices, live Test Voice buttons, filter toggles, a Quick Speak & MP3 Export bench, and cache management.
- **Core Bug Fixes (`speak.sh`, `speak-stop.sh`)**: Fixed duplicate CLI parsing loop, fixed stray `fi` causing double playback in `--text` mode, normalized boolean config files (`TRUE`/`FALSE` vs `yes`/`no`), replaced slow `xdotool search` scans with 2ms `xprop WM_CLASS` lookup, and isolated daemon process group via `setsid`.

## 2026-10-15

- Require edge-tts 7.3.1 or newer on installation for the updated Microsoft speech authentication protocol.
- Report HTTP 403 as a rejected speech connection without repeated timeout retries.
- Show error and warning notifications and retain underlying synthesis errors for diagnosis.
- Verified live MP3 synthesis, playback with an active unmuted desktop audio stream, shortcut-mode playback with a controlled selection, and immediate HTTP 403 error handling.
