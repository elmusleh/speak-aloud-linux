---
last_agent: Antigravity
timestamp: 2026-11-09T23:03:00+01:00
status: completed
current_phase: "Phase 10: Advanced Reordering, Opacity, Hotkey Remapper & System Integrations"
current_task: "Implemented Edge TTS Auth Token Refresh Failure Backoff & Automatic Mirror Switching in tts_synth_worker.py and speak.sh. Verified 57/57 tests passing and reloaded daemon."
stop_reason: completed
---
## What Was Completed

1. **Edge TTS Auth Token Refresh Failure Backoff & Automatic Mirror Switching**:
   - Implemented `is_edge_tts_auth_or_ratelimit` detecting HTTP 403, 429, WSServerHandshakeError, and SkewAdjustmentError.
   - Added jittered exponential backoff calculation `compute_jittered_backoff(attempt)`.
   - Added identity & mirror rotator `rotate_edge_tts_identity(attempt)` resetting `DRM.clock_skew_seconds`, cycling across diverse modern Edge User-Agents, and rotating mirror endpoints.
   - Updated `synthesize_segment_async` retry loop with backoff and mirror rotation before falling back to Piper.
   - Enhanced `speak.sh` CLI fallback with HTTP 403 retry backoff.
   - Added unit test `test_edge_tts_auth_backoff_and_mirror_rotation` in `tests/test_tts_suite.py` with 57/57 tests passing.

2. **Reorderable Button Layout & Custom Drawer Display Order**:
   - Built reorderable drawer list in Settings Studio (`list_drawer_order`) with Move Up/Down controls.
   - Wired dynamic widget reordering in `FloatingPlayerWindow.apply_button_settings` using `hud_drawer_order`.
3. **Popup Opacity & Transparency Slider with Smart Hover Boost**:
   - Added slider (50% to 100%, default 95%) in Popup Buttons tab.
   - Implemented `enterEvent` boosting opacity to 100% on mouse hover and `leaveEvent` restoring configured opacity.
4. **Single-Key Keyboard Shortcuts Remapper**:
   - Added configurable hotkey inputs table in Settings Studio for Live Transcript, Bookmark, Anki, Queue, OCR, Sleep Timer, More Tools, and Stop.
   - Dynamically mapped in `FloatingPlayerWindow.keyPressEvent` from `custom_hotkeys`.
5. **Profile Switcher Shortcut (<kbd>Shift+P</kbd>)**:
   - Instant keyboard cycle through Minimal -> Reader -> Productivity -> Default button profiles with live HUD update.
6. **Audio Output Device Selector in Popup Drawer**:
   - Added `btn_audio_device` in drawer and `_show_audio_devices_menu` querying PipeWire (`wpctl status`) and PulseAudio (`pactl list short sinks`), allowing instantaneous output switching.
7. **Gapless Segment Audio Crossfading**:
   - Configured mpv playback flags (`--gapless-audio=yes --audio-stream-silence=yes --audio-buffer=0.25`) in `speak.sh` for artifact-free inter-segment transitions.
8. **Battery / AC Power Awareness**:
   - Added battery status monitor in `speak.sh` (`/sys/class/power_supply/BAT*/status`), reducing concurrency and throttling background chunk downloads when running on battery.
9. **Desktop Notification Transport Action Buttons**:
   - Added interactive action buttons (`--action="pause=Pause" --action="stop=Stop"`) to `notify-send` in `speak.sh`.
10. **Automatic Text Punctuation Restorer for Raw OCR Transcripts**:
    - Implemented `restore_punctuation_for_ocr` in `tts_text_processor.py` and `tts_ocr.py`, automatically healing missing sentence periods, detached punctuation marks, and capitalizing lines.
11. **Spoken History & Reading Session Exporter**:
    - Implemented `record_spoken_history`, `load_spoken_history`, `export_history_as_markdown`, and `export_history_as_csv` in `tts_text_processor.py`.
    - Added History Log section with one-click Markdown and CSV export buttons in Settings Studio.
12. **D-Bus & Socket IPC Rate Limiting & Input Sanitization**:
    - Hardened `AsyncSynthServer` in `tts_synth_worker.py` with `MAX_MESSAGE_BYTES = 1MB` payload cap and sliding-window rate-limiting.
13. **Wayland Native Screen Selection Fallback**:
    - Implemented `capture_region_with_wayland` using `slurp` + `grim` in `tts_ocr.py` for Wayland desktop sessions.
14. **Full Automated Verification**:
    - Expanded test suite to 57 tests in `tests/test_tts_suite.py` (57/57 passed in 5.0s).
    - ShellCheck passed on all shell scripts.
    - Verified `speak-aloud-daemon.service` is active and running cleanly.

## Work In Progress

None.

## Next Steps

Review post-implementation suggestions in final response.

## Files Modified/Created

- `speak-aloud-linux/tts_text_processor.py`
- `speak-aloud-linux/tts_hud.py`
- `speak-aloud-linux/tts-settings.py`
- `speak-aloud-linux/tts_synth_worker.py`
- `speak-aloud-linux/tts_ocr.py`
- `speak-aloud-linux/speak.sh`
- `speak-aloud-linux/tests/test_tts_suite.py`
- `speak-aloud-linux/README.md`
- `speak-aloud-linux/CHANGELOG.md`
- `.ai-sync/PROGRESS.md`
- `.ai-sync/HANDOFF.md`

## Blockers

None.
