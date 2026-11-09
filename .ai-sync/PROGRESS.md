# Progress

## Phase 1: Core Engine & Bug Fixes
- [x] Fix `speak.sh` duplicate CLI loop, stray `fi` bug, boolean config normalization, and fast X11 window class check
- [x] Add smart Markdown/text cleaning and fast first-chunk trilingual segmentation (`tts_text_processor.py`)
- [x] Add real-time chunk manifest & cross-segment timeline/seek support

## Phase 2: Floating On-Screen Player HUD, Chunk Progress & Queue Manager
- [x] Build `tts_hud.py` / upgraded `tts-daemon.py` with `FloatingPlayerUI`, `ChunkedProgressUI`, and `QueueManagerUI`
- [x] Support cross-segment seeking (`seek-slider`, `-10s`, `+10s`), Replay at end, live Speed control, Save MP3, and Queue management

## Phase 3: Facelifted Settings GUI
- [x] Build modern `tts-settings.py` (with `tts-settings.sh` wrapper) and expand `tts-shared.json` voice catalog

## Phase 4: Verification & Documentation
- [x] Run automated verification & UI tests (`pytest` 6/6 passed + live synthesis + UI snapshot verification)
- [x] Update documentation and `.ai-sync/` handoff files

## Phase 5: Post-Implementation Enhancements (All 9 Suggestions)
- [x] 1. Per-User Runtime Directory (`$XDG_RUNTIME_DIR/speak-aloud-$UID`, `0700` permissions)
- [x] 2. Persistent Async Python Synthesis Worker (`tts_synth_worker.py` + `synth.sock`)
- [x] 3. Local Offline Neural Fallback (`piper-tts` ONNX support before `espeak-ng`)
- [x] 4. Systemd User Service (`speak-aloud-daemon.service` + `install.sh` integration)
- [x] 5. Interactive Sentence Transcript Highlighting in Floating HUD (`FloatingPlayerWindow`)
- [x] 6. Strict Schema Validation & Atomic Writes (`TTSSettingsStore` + `settings.json`)
- [x] 7. Keyboard Navigation & Hotkeys Inside Floating HUD (`Space`, `Left`/`Right`, `[`/`]`, `T`, `Q`, `Esc`)
- [x] 8. OCR Screen-Region Speak Mode (`--ocr` CLI flag, `tts_ocr.py`, HUD & Studio buttons)
- [x] 9. Pronunciation Lexicon / Custom Alias Dictionary (`lexicon.json` + Studio editor)

## Phase 6: Post-Implementation Suggestions Batch 2 (6 Advanced Integrations)
- [x] 1. MPRIS2 D-Bus Media Player Service (`org.mpris.MediaPlayer2.SpeakALoud`, `tts_mpris.py`)
- [x] 2. Word-Level Karaoke Highlighting via `edge_tts` WordBoundary events (`tts_synth_worker.py` & `tts_hud.py`)
- [x] 3. Smart PipeWire / PulseAudio Audio Ducking (`tts_ducking.py` + Settings Studio toggle)
- [x] 4. Desktop Keyring Integration (`secret-tool` for DeepL/OpenAI API keys in `TTSSettingsStore`)
- [x] 5. Direct PDF / EPUB / Webpage URL Reader (`tts_reader.py` + `speak.sh --file` & `--url` modes)
- [x] 6. Bookmark & Timestamped Audio Note Saver (`B` hotkey & HUD button saving to `bookmarks.md`)

## Phase 7: UI Polish, Error Hardening & System Integration
- [x] 1. Adaptive prosody inflection for parentheticals & citations (`tts_text_processor.py`)
- [x] 2. Systemd watchdog heartbeat ping (`sd_notify("WATCHDOG=1")` in `tts-daemon.py`)
- [x] 3. Timeline micro-tooltip hover preview (`ClickJumpSlider` in `tts_hud.py`)
- [x] 4. Auto-scrolling active transcript lock with user manual scroll detection (`tts_hud.py`)
- [x] 5. Whisper pronunciation verification loop (`tts_whisper.py` + Settings Studio)
- [x] 6. Bubblewrap sandbox isolation for web reader (`tts_reader.py`)
- [x] 7. Anki flashcard exporter with audio clips (`AnkiExporter` in `tts_hud.py`)

## Phase 8: Compact Essentials HUD, OCR Fix, Chunk Tuning & Advanced Integrations
- [x] 1. Fix OCR Screen-Region selection lifecycle (`QApplication.quit()` fix), 2.5x Lanczos upscale, contrast boost, and multi-PSM fallback (`tts_ocr.py`)
- [x] 2. Remove confusing status LED and truncated title from HUD header; add prominent Speed Pill on the left (`tts_hud.py`)
- [x] 3. Add collapsible More Tools Drawer (<kbd>M</kbd>) containing secondary tools (Enqueue, Transcript, Queue, OCR, Save MP3, Bookmark, Anki, Sleep, Settings) (`tts_hud.py`)
- [x] 4. Keep compact essentials mode always visible with essential transport controls (`tts_hud.py`)
- [x] 5. Configurable initial chunk (`first_chunk_max`, default 140) and segment size (`max_segment_chars`, default 280) with priority parallel download staggering (`tts_text_processor.py`, `speak.sh`, `tts-settings.py`)
- [x] 6. AI Text Condenser & Summarizer before speaking (`tts_text_processor.py`, `speak.sh --summarize`)
- [x] 7. Voice Clarity & Frequency Compensation EQ preset (`tts-settings.py`, `speak.sh`)
- [x] 8. Per-application speech profile rules (`~/.config/tts_settings/app_rules.json`, `speak.sh`)
- [x] 9. Audio cache checksum and corrupt header verification (`tts_synth_worker.py`)
- [x] 10. Calibre eBook Library direct extractor (`tts_reader.py`, `speak.sh --calibre`)
- [x] 11. Headless GitHub Actions CI test workflow (`.github/workflows/ci.yml`)

## Phase 9: Dedicated Popup Buttons Management Tab
- [x] 1. Add schema-validated HUD button visibility keys to `TTSSettingsStore.DEFAULTS` & `validate` (`tts_text_processor.py`)
- [x] 2. Build dedicated "Popup Buttons" tab in `TTSSettingsWindow` with categorized Primary Controls & Drawer Tools (`tts-settings.py`)
- [x] 3. Add one-click presets: Minimal, Reader / Study, Productivity, and Enable All (`tts-settings.py`)
- [x] 4. Add `apply_button_settings` to `FloatingPlayerWindow` to show/hide controls cleanly and auto-hide More Tools when empty (`tts_hud.py`)
- [x] 5. Add dynamic live-sync reloading button visibility on `settings.json` modification without daemon restarts (`tts_hud.py`)
- [x] 6. Guard keyboard hotkeys in `FloatingPlayerWindow` against disabled buttons (`tts_hud.py`)
- [x] 7. Automated tests covering presets, schema validation, and button hide/show rules (50/50 passed)
