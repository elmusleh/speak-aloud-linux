# Speak a Loud Universal

A desktop Text-to-Speech (TTS) studio for Linux with seamless trilingual (**English, Arabic, and German**) support, a draggable **Floating On-Screen Player HUD** (inspired by `obsidian-edge-tts`), real-time **Chunked Generation Progress**, a **Playback Queue Manager**, and **Smart Markdown & Technical Text Filtering** — powered by Microsoft Edge neural voices (`edge-tts`) with zero API keys required.

## 🚀 Key Features

- **Floating On-Screen Player HUD (`tts_hud.py`)**:
  - Draggable, frameless, always-on-top dark-slate HUD that appears automatically when speaking starts and remembers its screen position.
  - **Compact Essentials & More Tools Drawer**: Essential controls (prominent **Speed Pill**, Play/Pause/Replay, Jump ±10s, Stop, Scrubber, Volume, Close) are always visible and uncluttered. Advanced actions (Enqueue, Transcript, Queue, OCR, Save MP3, Bookmark, Anki, Sleep, Settings) are neatly tucked into an expandable drawer toggled with `...` or <kbd>M</kbd>.
  - **Prominent Speed Pill & Multilingual Rates**: Displays the active speaking speed (e.g., `1.2x`) clearly on the left without squeezing; clicking cycles through preset speed multipliers while preserving per-language rate scaling.
  - **Unified Timeline Scrubber with Micro-Tooltips**: Displays elapsed time (`0:16`), total duration (`0:43`), live sentence preview tooltips on mouse hover, and an interactive click-and-drag seek bar across all segments.
  - **Live Interactive Sentence & Word-Level Karaoke Drawer**: Click Transcript (<kbd>T</kbd>) to view all sentences, auto-scroll with the speech, and see the active word highlight in real-time. Click any sentence to jump directly to it.
- **Configurable Chunk Sizing & Parallel Synthesis Tuning**:
  - Fine-tune time-to-first-sound by setting initial chunk size (`first_chunk_max`, default ~140 chars) and standard segment size (`max_segment_chars`, default ~280 chars) in Settings Studio or CLI (`--first-chunk-max`, `--max-segment-chars`).
  - Staggered parallel synthesis starts chunk 0 with 0ms delay while subsequent chunks synthesize in parallel.
- **Rock-Solid Screen-Region & Image OCR (`tts_ocr.py` / `speak.sh --ocr`)**:
  - High-precision screen-region capture with automatic image preprocessing (2.5x Lanczos upscale, grayscale conversion, contrast enhancement) and multi-PSM fallback (`--psm 6`, `--psm 3`, `--psm 11`) to read small terminal and IDE fonts.
- **AI Text Condenser & Summarizer (`speak.sh --summarize`)**:
  - Condense long articles or documents into concise, speech-ready bullet summaries before speaking using local Ollama models or fast extractive frequency ranking.
- **Voice Clarity & Frequency Compensation EQ**:
  - Optional vocal enhancement chain (`highpass=f=100`, `equalizer=3kHz +3.5dB`, dynamic audio compressor) enabled in Settings Studio to make speech crisp and clear on built-in laptop speakers.
- **Per-Application Speech Profiles (`~/.config/tts_settings/app_rules.json`)**:
  - Automatically apply custom voice, rate, and language profiles depending on the focused window (e.g. read code in IDE at 1.5x with technical filters, read PDFs at 1.1x with expressive prosody).
- **Calibre eBook Library Integration (`speak.sh --calibre`)**:
  - Search, extract, and queue books directly from your local Calibre eBook library.
- **Persistent Async Synthesis Worker & Local Piper Fallback (`tts_synth_worker.py`)**:
  - Keeps `edge_tts` warm in memory inside `tts-daemon.py` over `$XDG_RUNTIME_DIR/speak-aloud-$UID/synth.sock` (`0600`), eliminating ~350ms of Python/TLS startup latency per segment.
  - Automatically falls back to local `piper-tts` ONNX neural voice models when offline or rate-limited before falling back to `espeak-ng`.
- **OCR Screen-Region Speak Mode (`tts_ocr.py` / `speak.sh --ocr`)**:
  - Drag a selection rectangle over any image, scanned PDF, or video frame to extract text with `tesseract` and immediately speak or queue it.
- **Pronunciation Lexicon & Schema-Validated Settings (`lexicon.json` & `settings.json`)**:
  - Configurable pronunciation alias dictionary (`kubectl` → `kube control`, `PyQt6` → `Pie Cute 6`, `nginx` → `engine X`) editable directly in the Settings Studio, backed by atomic writes (`TTSSettingsStore`).
- **MPRIS2 D-Bus Media Player (`org.mpris.MediaPlayer2.SpeakALoud`)**:
  - Full Linux desktop media player integration for Cinnamon/GNOME/KDE sound applets, lock screens, KDE Connect remote controls, and Bluetooth headset playback/pause/seek controls.
- **Word-Level Karaoke Highlighting**:
  - Precision millisecond word tracking via `edge_tts` `WordBoundary` metadata; the active word glows with a high-contrast pill highlight in the Live Transcript Drawer as it is spoken.
- **Smart PipeWire / PulseAudio Audio Ducking**:
  - Automatically lowers background media players (Spotify, Firefox) by 60% while TTS is actively speaking and seamlessly restores volume on pause or completion.
- **System Keyring Integration (`secret-tool`)**:
  - Encrypts sensitive DeepL/OpenAI translation API keys in the user's desktop keyring rather than storing plaintext credentials on disk.
- **Direct PDF, EPUB & Web Article Reader (`tts_reader.py` / `--file` & `--url`)**:
  - Reads whole documents, specific PDF pages (`--page 12`), or web articles, cleanly stripping boilerplate/nav/ads and loading chapters directly into the Playback Queue.
- **Bookmark & Timestamped Audio Note Saver (`~/.local/share/speak-aloud/bookmarks.md`)**:
  - Press `B` or click the Bookmark icon (`★`) on the HUD to instantly save the active spoken sentence and timestamped audio location to markdown notes.
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
  - **Dedicated "Popup Buttons" Management Tab**: Customize which buttons and actions are shown or hidden in the Floating Player popup. Features one-click presets (**Minimal**, **Reader / Study**, **Productivity**, **Enable All**), granular checkboxes for both primary transport controls and drawer tools, auto-hiding of the drawer toggle when all tools are disabled, **reorderable drawer tool items**, **window opacity & transparency slider** with smart hover boost, a **single-key hotkey remapper**, and real-time live synchronization without daemon restarts.
- **Audio Output Device Selector & Profile Switcher (<kbd>Shift+P</kbd>)**:
  - Seamlessly switch between headphones, external speakers, and Bluetooth sinks on demand via PipeWire (`wpctl`) and PulseAudio (`pactl`).
  - Press <kbd>Shift+P</kbd> to cycle instantly between Minimal, Reader, Productivity, and Default player layouts.
- **Spoken Reading History Log & Exporter**:
  - Automatically records spoken passages with character/word metrics and timestamps to `~/.local/share/speak-aloud/history.jsonl`, with one-click export to formatted Markdown tables and CSV spreadsheets in Settings Studio.
- **Battery Saver Awareness & Gapless Audio Transitions**:
  - Automatically reduces background parallel chunk concurrency when running on battery (`/sys/class/power_supply/BAT*/status`).
  - Configures mpv gapless audio buffer flags for continuous crossfading between sentences.

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
./speak.sh --summarize                         # Condense selection into a summary before speaking
./speak.sh --first-chunk-max 120               # Override initial chunk size for ultra-fast startup
./speak.sh --file manual.pdf --page 4          # Read specific page from PDF
./speak.sh --file book.epub --queue            # Load all EPUB chapters into Playback Queue
./speak.sh --calibre "Clean Code"              # Read book from local Calibre library
./speak.sh --url https://example.com/essay     # Read cleaned web article aloud
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
