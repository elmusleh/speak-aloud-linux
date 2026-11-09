# Speak a Loud Universal

A desktop Text-to-Speech (TTS) studio for Linux with seamless trilingual (**English, Arabic, and German**) support, a draggable **Floating On-Screen Player HUD** (inspired by `obsidian-edge-tts`), real-time **Chunked Generation Progress**, a **Playback Queue Manager**, and **Smart Markdown & Technical Text Filtering** — powered by Microsoft Edge neural voices (`edge-tts`) with zero API keys required.

## 🚀 Key Features

- **Floating On-Screen Player HUD (`tts_hud.py`)**:
  - Draggable, frameless, always-on-top dark-slate HUD that appears automatically when speaking starts and remembers its screen position.
  - **Unified Timeline Scrubber**: Displays elapsed time (`0:16`), total duration (`0:43`), and an interactive click-and-drag seek bar across all segments.
  - **Manual Transport Controls & Hotkeys**: Jump Backward `-10s`, Play / Pause / **Replay at End**, Jump Forward `+10s`, Stop, live **Speed Selector Pill (`0.75x` – `2.0x`)**, and keyboard hotkeys (`Space` play/pause, `←`/`→` seek, `[`/`]` speed, `T` transcript, `Q` queue, `O` OCR, `Esc` hide).
  - **Live Interactive Sentence Transcript Drawer**: Click the Transcript button (`T`) to view all segmented sentences, auto-scroll with the active highlighted sentence, and click any sentence to jump directly to it.
  - **One-Click MP3 Export, OCR & Queue Controls**: Save the current speech as MP3 (`⬇`), read unselectable screen regions via OCR (`📷`), add highlighted text to the queue (`+`), or open the Playback Queue (`☰`).
- **Persistent Async Synthesis Worker & Local Piper Fallback (`tts_synth_worker.py`)**:
  - Keeps `edge_tts` warm in memory inside `tts-daemon.py` over `$XDG_RUNTIME_DIR/speak-aloud-$UID/synth.sock` (`0600`), eliminating ~350ms of Python/TLS startup latency per segment.
  - Automatically falls back to local `piper-tts` ONNX neural voice models when offline or rate-limited before falling back to `espeak-ng`.
- **OCR Screen-Region Speak Mode (`tts_ocr.py` / `speak.sh --ocr`)**:
  - Drag a selection rectangle over any image, scanned PDF, or video frame to extract text with `tesseract` and immediately speak or queue it.
- **Pronunciation Lexicon & Schema-Validated Settings (`lexicon.json` & `settings.json`)**:
  - Configurable pronunciation alias dictionary (`kubectl` → `kube control`, `PyQt6` → `Pie Cute 6`, `nginx` → `engine X`) editable directly in the Settings Studio, backed by atomic writes (`TTSSettingsStore`).
- **Chunked Generation Progress (`ChunkedProgressUI`)**:
  - Visualizes multi-chunk neural synthesis in real time with overall percentage, progress bar, and per-chunk status pills (`1 ✓`, `2 ⟳`, `3 ○`).
  - **Fast-First-Chunk Optimization**: Caps the initial chunk (`seg_000`) at ~220 characters so speech begins in under a second while longer chunks (~480 chars) synthesize in the background.
- **Playback Queue Manager (`QueueManagerUI`)**:
  - Companion floating window to queue multiple selections (`speak.sh --queue` or `+` button), reorder items (Move Up / Down), toggle Loop, jump to any queued passage, or clear the queue.
- **Smart Markdown & Technical Text Filtering (`tts_text_processor.py`)**:
  - Automatically cleans Markdown syntax (YAML frontmatter, headings, bold/italics, `[links](url)`, tables, HTML comments), strips raw URLs, normalizes bullet lists into clean sentences, and converts math/comparison symbols (`320 x 240` → `320 by 240`, `>=`, `<=`, `≈`) into natural speech.
- **Trilingual Intelligence (EN / AR / DE)**:
  - Detects English, Arabic (with embedded number merging), and German segments within the same passage and switches voices seamlessly without misclassifying English technical jargon.
- **Modern Settings & Studio Test Bench (`tts-settings.py`)**:
  - Tabbed PyQt6 dark-slate settings window with voice dropdowns, live **Test EN / AR / DE** buttons, per-language rate sliders, Markdown filter toggles, translation settings, a **Quick Speak & MP3 Export Bench**, and audio cache management.

## 🛠️ Dependencies

- **Python 3** + **PyQt6** (`python3-pyqt6`, `python3-pyqt6.qtsvg`)
- **edge-tts 7.3.1+** (installed via `pipx`)
- **mpv** (included locally in `bin/` or system `mpv`) + **socat**
- **xsel** / **wl-clipboard** + **xdotool** / **xprop** (for selection & office app support)

## 📦 Installation & Setup

```bash
cd speak-aloud-linux
chmod +x install.sh
./install.sh
```

## ⌨️ Keyboard Shortcuts & CLI Usage

| Action | Command | Recommended Shortcut |
| :--- | :--- | :--- |
| **Speak Selection** | `./speak.sh` | `Super + S` |
| **Pause / Resume** | `./speak-pause.sh` | `Super + P` |
| **Stop Speech** | `./speak-stop.sh` | `Shift + Super + S` |
| **TTS Settings & Studio** | `./tts-settings.sh` | `Super + T` |
| **Add Selection to Queue** | `./speak.sh --queue` | *(Optional shortcut or `+` on HUD)* |

### CLI Examples
```bash
./speak.sh --text "Hello مرحبا Grüße"          # Speak explicit trilingual text
./speak.sh --ocr                               # Drag screen region to OCR and speak aloud
./speak.sh --ocr-image page.png                # OCR an image file and speak aloud
./speak.sh --queue                             # Add current selection to Playback Queue
./speak.sh --output out.mp3                    # Speak and export concatenated MP3
./speak.sh --generate-only --output out.mp3    # Generate MP3 silently without playing
systemctl --user status speak-aloud-daemon     # Check background HUD & synthesis daemon
```

## 🧪 Testing

```bash
python3 -m pytest -v tests/test_tts_suite.py
```
