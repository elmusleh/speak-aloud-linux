#!/usr/bin/env python3
"""
Speak a Loud Universal — Modern Settings & Test Bench GUI (PyQt6).

Provides a crafted dark-slate interface for configuring trilingual neural voices,
per-language rates, floating player HUD behavior, smart Markdown/symbol filtering,
translation, audio cache management, and a live Quick-Speak test bench.
"""

import glob
import json
import os
import re
import subprocess
import sys
import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import (
    CONFIG_DIR,
    TTSSettingsStore,
    atomic_write_text,
    get_analytics_summary,
    get_sanitized_env,
    import_community_lexicon,
    load_lexicon,
    save_lexicon,
)

CACHE_DIR = os.path.expanduser('~/.cache/speak-aloud')
SHARED_JSON = os.path.join(SCRIPT_DIR, 'tts-shared.json')
POSITIONS_FILE = os.path.join(CONFIG_DIR, 'ui_positions.json')


def load_shared() -> dict:
    try:
        with open(SHARED_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def read_cfg(name: str, default: str = '') -> str:
    try:
        with open(os.path.join(CONFIG_DIR, name), 'r', encoding='utf-8') as f:
            val = f.read().strip()
        return val if val != '' else default
    except Exception:
        return default


def read_bool_cfg(name: str, default: bool = False) -> bool:
    raw = read_cfg(name, 'yes' if default else 'no').lower()
    return raw in ('yes', 'true', '1', 'on')


def read_int_cfg(name: str, default: int = 0, strip_chars: str = '+%') -> int:
    raw = read_cfg(name, str(default)).strip(strip_chars)
    try:
        return int(float(raw))
    except Exception:
        return default


def write_cfg(name: str, value: str) -> None:
    atomic_write_text(os.path.join(CONFIG_DIR, name), str(value).strip() + '\n')


class TTSSettingsWindow(QDialog):
    def __init__(self):
        super().__init__(None)
        self.setWindowTitle('Speak a Loud — TTS Settings & Studio')
        self.setMinimumSize(620, 560)
        self.shared = load_shared()
        self.defaults = self.shared.get('defaults', {})

        self._build_ui()
        self._load_values()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # Header banner
        hdr = QFrame()
        hdr.setObjectName('settingsHeader')
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14, 12, 14, 12)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        t_lbl = QLabel('Speak a Loud Universal')
        t_lbl.setObjectName('headerTitle')
        s_lbl = QLabel('Trilingual Neural TTS · Floating HUD · Smart Markdown Filter · Queue')
        s_lbl.setObjectName('headerSub')
        title_col.addWidget(t_lbl)
        title_col.addWidget(s_lbl)
        hl.addLayout(title_col, 1)
        root.addWidget(hdr)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_voices_tab(), 'Voices && Speed')
        self.tabs.addTab(self._build_popup_buttons_tab(), 'Popup Buttons')
        self.tabs.addTab(self._build_player_filter_tab(), 'Floating HUD && Text Filter')
        self.tabs.addTab(self._build_translate_bench_tab(), 'Translation && Test Bench')
        self.tabs.addTab(self._build_analytics_tab(), 'Analytics && Productivity')
        root.addWidget(self.tabs, 1)

        # Bottom Action Row
        footer = QHBoxLayout()
        self.lbl_status = QLabel('')
        self.lbl_status.setObjectName('statusMsg')
        footer.addWidget(self.lbl_status, 1)

        btn_cancel = QPushButton('Close')
        btn_cancel.setObjectName('secondaryBtn')
        btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        btn_save = QPushButton('Save Settings')
        btn_save.setObjectName('primaryBtn')
        btn_save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_save.clicked.connect(self._on_save)
        footer.addWidget(btn_save)

        root.addLayout(footer)

        self.setStyleSheet("""
            QDialog {
                background-color: #12141A;
                color: #F3F4F6;
                font-size: 13px;
            }
            #settingsHeader {
                background-color: #1A1D26;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
            #headerTitle {
                color: #F9FAFB;
                font-size: 16px;
                font-weight: 700;
            }
            #headerSub {
                color: #9CA3AF;
                font-size: 12px;
            }
            QTabWidget::pane {
                background-color: #16181E;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
                padding: 10px;
            }
            QTabBar::tab {
                background-color: #16181E;
                color: #9CA3AF;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 8px 16px;
                margin-right: 4px;
                font-weight: 600;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #6366F1;
                color: #FFFFFF;
            }
            QLabel {
                color: #E5E7EB;
                font-size: 12px;
            }
            QLabel.sectionHeader {
                color: #A5B4FC;
                font-size: 12px;
                font-weight: 700;
                padding-top: 4px;
            }
            QComboBox, QLineEdit, QSpinBox, QPlainTextEdit {
                background-color: #0E1015;
                color: #F3F4F6;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {
                border-color: #6366F1;
            }
            QCheckBox {
                color: #E5E7EB;
                spacing: 8px;
                font-size: 12px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #0E1015;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #6366F1;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #F9FAFB;
                border: 2px solid #6366F1;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            #primaryBtn {
                background-color: #6366F1;
                color: #FFFFFF;
                border: 1px solid #818CF8;
                border-radius: 7px;
                padding: 7px 18px;
                font-weight: 600;
                font-size: 12px;
            }
            #primaryBtn:hover {
                background-color: #818CF8;
            }
            #secondaryBtn {
                background-color: #1E212B;
                color: #E5E7EB;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 7px;
                padding: 6px 14px;
                font-weight: 500;
                font-size: 12px;
            }
            #secondaryBtn:hover {
                background-color: #282C3A;
            }
            #statusMsg {
                color: #34D399;
                font-weight: 600;
                font-size: 12px;
            }
        """)

    def _set_status(self, msg: str, is_error: bool = False):
        if hasattr(self, 'lbl_status') and self.lbl_status is not None:
            self.lbl_status.setText(msg)
            if is_error:
                self.lbl_status.setStyleSheet("color: #F87171; font-weight: 600; font-size: 12px;")
            else:
                self.lbl_status.setStyleSheet("color: #34D399; font-weight: 600; font-size: 12px;")

    def _build_voices_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(11)
        form.setContentsMargins(8, 8, 8, 8)

        voices = self.shared.get('voices', {})

        # English
        self.cb_en_voice = QComboBox()
        for v in voices.get('english', []):
            self.cb_en_voice.addItem(f"{v['name']}  ({v['id']})", v['id'])
        en_row = QHBoxLayout()
        en_row.addWidget(self.cb_en_voice, 1)
        btn_test_en = QPushButton('Test EN')
        btn_test_en.setObjectName('secondaryBtn')
        btn_test_en.clicked.connect(lambda: self._test_voice('en'))
        en_row.addWidget(btn_test_en)
        form.addRow('English Voice:', en_row)

        self.sl_en_rate, self.lbl_en_rate = self._make_rate_slider()
        form.addRow('English Rate (+%):', self._wrap_slider(self.sl_en_rate, self.lbl_en_rate))

        # Arabic
        self.cb_ar_voice = QComboBox()
        for v in voices.get('arabic', []):
            self.cb_ar_voice.addItem(f"{v['name']}  ({v['id']})", v['id'])
        ar_row = QHBoxLayout()
        ar_row.addWidget(self.cb_ar_voice, 1)
        btn_test_ar = QPushButton('Test AR')
        btn_test_ar.setObjectName('secondaryBtn')
        btn_test_ar.clicked.connect(lambda: self._test_voice('ar'))
        ar_row.addWidget(btn_test_ar)
        form.addRow('Arabic Voice:', ar_row)

        self.sl_ar_rate, self.lbl_ar_rate = self._make_rate_slider()
        form.addRow('Arabic Rate (+%):', self._wrap_slider(self.sl_ar_rate, self.lbl_ar_rate))

        # German
        self.cb_de_voice = QComboBox()
        for v in voices.get('german', []):
            self.cb_de_voice.addItem(f"{v['name']}  ({v['id']})", v['id'])
        de_row = QHBoxLayout()
        de_row.addWidget(self.cb_de_voice, 1)
        btn_test_de = QPushButton('Test DE')
        btn_test_de.setObjectName('secondaryBtn')
        btn_test_de.clicked.connect(lambda: self._test_voice('de'))
        de_row.addWidget(btn_test_de)
        form.addRow('German Voice:', de_row)

        self.sl_de_rate, self.lbl_de_rate = self._make_rate_slider()
        form.addRow('German Rate (+%):', self._wrap_slider(self.sl_de_rate, self.lbl_de_rate))

        # Custom voice & source language
        self.ed_custom_voice = QLineEdit()
        self.ed_custom_voice.setPlaceholderText('Optional override (e.g., en-US-BrianMultilingualNeural)')
        form.addRow('Custom Voice ID:', self.ed_custom_voice)

        self.cb_source_lang = QComboBox()
        for lang in self.shared.get('source_languages', []):
            self.cb_source_lang.addItem(lang['name'], lang['code'])
        form.addRow('Language Detection:', self.cb_source_lang)

        # Global speed multiplier
        self.sl_speed = QSlider(Qt.Orientation.Horizontal)
        self.sl_speed.setRange(50, 200)
        self.sl_speed.setSingleStep(5)
        self.lbl_speed = QLabel('1.00x')
        self.lbl_speed.setMinimumWidth(48)
        self.sl_speed.valueChanged.connect(lambda v: self.lbl_speed.setText(f'{v / 100.0:.2f}x'))
        form.addRow('Player Speed Multiplier:', self._wrap_slider(self.sl_speed, self.lbl_speed))

        # Speech pitch modulation
        self.sl_pitch = QSlider(Qt.Orientation.Horizontal)
        self.sl_pitch.setRange(-25, 25)
        self.sl_pitch.setSingleStep(1)
        self.lbl_pitch = QLabel('+0Hz')
        self.lbl_pitch.setMinimumWidth(48)
        self.sl_pitch.valueChanged.connect(lambda v: self.lbl_pitch.setText(f'{v:+d}Hz'))
        form.addRow('Speech Pitch Offset:', self._wrap_slider(self.sl_pitch, self.lbl_pitch))

        return w

    def _apply_button_preset(self, preset: str):
        if preset == "minimal":
            self.chk_btn_speed.setChecked(True)
            self.chk_btn_mode_tag.setChecked(False)
            self.chk_btn_back.setChecked(False)
            self.chk_btn_fwd.setChecked(False)
            self.chk_btn_stop.setChecked(True)
            self.chk_show_progress.setChecked(True)
            self.chk_show_chunks.setChecked(False)
            self.chk_btn_close.setChecked(True)
            self.chk_btn_more.setChecked(False)
            self.chk_btn_transcript.setChecked(False)
            self.chk_btn_enqueue.setChecked(False)
            self.chk_btn_queue.setChecked(False)
            self.chk_btn_ocr.setChecked(False)
            self.chk_btn_save.setChecked(False)
            self.chk_btn_bookmark.setChecked(False)
            self.chk_btn_anki.setChecked(False)
            self.chk_btn_sleep.setChecked(False)
            self.chk_btn_settings.setChecked(False)
            self.chk_btn_audio_device.setChecked(False)
            self.chk_expand_drawer.setChecked(False)
            self._set_status("Applied 'Minimal' preset (essential playback controls only).")
        elif preset == "reader":
            self.chk_btn_speed.setChecked(True)
            self.chk_btn_mode_tag.setChecked(True)
            self.chk_btn_back.setChecked(True)
            self.chk_btn_fwd.setChecked(True)
            self.chk_btn_stop.setChecked(True)
            self.chk_show_progress.setChecked(True)
            self.chk_show_chunks.setChecked(True)
            self.chk_btn_close.setChecked(True)
            self.chk_btn_more.setChecked(True)
            self.chk_btn_transcript.setChecked(True)
            self.chk_btn_enqueue.setChecked(False)
            self.chk_btn_queue.setChecked(False)
            self.chk_btn_ocr.setChecked(False)
            self.chk_btn_save.setChecked(False)
            self.chk_btn_bookmark.setChecked(True)
            self.chk_btn_anki.setChecked(False)
            self.chk_btn_sleep.setChecked(True)
            self.chk_btn_settings.setChecked(False)
            self.chk_btn_audio_device.setChecked(False)
            self.chk_expand_drawer.setChecked(False)
            self._set_status("Applied 'Reader / Study' preset (speed, seek, transcript, notes & sleep).")
        elif preset == "productivity":
            self.chk_btn_speed.setChecked(True)
            self.chk_btn_mode_tag.setChecked(True)
            self.chk_btn_back.setChecked(True)
            self.chk_btn_fwd.setChecked(True)
            self.chk_btn_stop.setChecked(True)
            self.chk_show_progress.setChecked(True)
            self.chk_show_chunks.setChecked(True)
            self.chk_btn_close.setChecked(True)
            self.chk_btn_more.setChecked(True)
            self.chk_btn_transcript.setChecked(True)
            self.chk_btn_enqueue.setChecked(True)
            self.chk_btn_queue.setChecked(True)
            self.chk_btn_ocr.setChecked(True)
            self.chk_btn_save.setChecked(True)
            self.chk_btn_bookmark.setChecked(False)
            self.chk_btn_anki.setChecked(False)
            self.chk_btn_sleep.setChecked(False)
            self.chk_btn_settings.setChecked(True)
            self.chk_btn_audio_device.setChecked(True)
            self.chk_expand_drawer.setChecked(False)
            self._set_status("Applied 'Productivity' preset (queue, OCR, save MP3 & settings).")
        elif preset == "all":
            self.chk_btn_speed.setChecked(True)
            self.chk_btn_mode_tag.setChecked(True)
            self.chk_btn_back.setChecked(True)
            self.chk_btn_fwd.setChecked(True)
            self.chk_btn_stop.setChecked(True)
            self.chk_show_progress.setChecked(True)
            self.chk_show_chunks.setChecked(True)
            self.chk_btn_close.setChecked(True)
            self.chk_btn_more.setChecked(True)
            self.chk_btn_transcript.setChecked(True)
            self.chk_btn_enqueue.setChecked(True)
            self.chk_btn_queue.setChecked(True)
            self.chk_btn_ocr.setChecked(True)
            self.chk_btn_save.setChecked(True)
            self.chk_btn_bookmark.setChecked(True)
            self.chk_btn_anki.setChecked(True)
            self.chk_btn_sleep.setChecked(True)
            self.chk_btn_settings.setChecked(True)
            self.chk_btn_audio_device.setChecked(True)
            self.chk_expand_drawer.setChecked(False)
            self._set_status("Applied 'Enable All' preset (all buttons enabled).")

    def _build_popup_buttons_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header description
        desc_lbl = QLabel(
            "Manage which controls and action buttons appear in the Floating Player popup.\n"
            "Enable only what you need to keep the popup uncluttered and focused on your workflow."
        )
        desc_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; line-height: 1.4;")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        # Quick Presets Toolbar
        preset_box = QFrame()
        preset_box.setStyleSheet(
            "background-color: #141721; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 8px; padding: 4px;"
        )
        preset_layout = QHBoxLayout(preset_box)
        preset_layout.setContentsMargins(8, 6, 8, 6)
        preset_layout.setSpacing(6)

        pr_title = QLabel("Presets:")
        pr_title.setStyleSheet("font-weight: 600; font-size: 11px; color: #818CF8;")
        preset_layout.addWidget(pr_title)

        btn_pre_minimal = QPushButton("🎯 Minimal")
        btn_pre_minimal.setToolTip("Keep only Speed Pill, Play/Pause, Scrubber, Stop & Close")
        btn_pre_minimal.setObjectName("secondaryBtn")
        btn_pre_minimal.clicked.connect(lambda: self._apply_button_preset("minimal"))
        preset_layout.addWidget(btn_pre_minimal)

        btn_pre_reader = QPushButton("📖 Reader / Study")
        btn_pre_reader.setToolTip("Speed, Play/Pause, ±10s, Scrubber, Live Transcript, Bookmark & Sleep Timer")
        btn_pre_reader.setObjectName("secondaryBtn")
        btn_pre_reader.clicked.connect(lambda: self._apply_button_preset("reader"))
        preset_layout.addWidget(btn_pre_reader)

        btn_pre_tools = QPushButton("⚡ Productivity")
        btn_pre_tools.setToolTip("Speed, Play/Pause, Scrubber, Stop, Queue, OCR, Save MP3 & Settings")
        btn_pre_tools.setObjectName("secondaryBtn")
        btn_pre_tools.clicked.connect(lambda: self._apply_button_preset("productivity"))
        preset_layout.addWidget(btn_pre_tools)

        btn_pre_all = QPushButton("🌟 Enable All")
        btn_pre_all.setToolTip("Show all available player buttons and drawer tools")
        btn_pre_all.setObjectName("secondaryBtn")
        btn_pre_all.clicked.connect(lambda: self._apply_button_preset("all"))
        preset_layout.addWidget(btn_pre_all)

        preset_layout.addStretch(1)
        layout.addWidget(preset_box)

        # Section 1: Main Transport & Player Bar Controls
        sec1 = QLabel("Main Player Controls (Always-Visible Row)")
        sec1.setProperty("class", "sectionHeader")
        layout.addWidget(sec1)

        grid1 = QGridLayout()
        grid1.setSpacing(8)

        self.chk_btn_play_pause = QCheckBox("Play / Pause / Replay Button (Core playback button)")
        self.chk_btn_play_pause.setChecked(True)
        self.chk_btn_play_pause.setEnabled(False)
        self.chk_btn_play_pause.setToolTip("Essential core playback control (always enabled)")
        grid1.addWidget(self.chk_btn_play_pause, 0, 0)

        self.chk_btn_speed = QCheckBox("Speed Pill (e.g. 1.2x on the left, click/scroll to change rate)")
        grid1.addWidget(self.chk_btn_speed, 0, 1)

        self.chk_btn_back = QCheckBox("Jump Backward -10s Button (rotate-ccw)")
        grid1.addWidget(self.chk_btn_back, 1, 0)

        self.chk_btn_fwd = QCheckBox("Jump Forward +10s Button (rotate-cw)")
        grid1.addWidget(self.chk_btn_fwd, 1, 1)

        self.chk_btn_stop = QCheckBox("Stop Button (halt playback immediately)")
        grid1.addWidget(self.chk_btn_stop, 2, 0)

        self.chk_show_progress = QCheckBox("Timeline Scrubber (seek slider, current & total timecodes)")
        grid1.addWidget(self.chk_show_progress, 2, 1)

        self.chk_btn_mode_tag = QCheckBox("Speech Mode Badge (shows 'Speech' or 'Queued')")
        grid1.addWidget(self.chk_btn_mode_tag, 3, 0)

        self.chk_show_chunks = QCheckBox("Chunk Synthesis Progress Strip (pills & % indicator)")
        grid1.addWidget(self.chk_show_chunks, 3, 1)

        self.chk_btn_close = QCheckBox("Close / Hide Button (✕ dismiss popup)")
        grid1.addWidget(self.chk_btn_close, 4, 0)

        self.chk_btn_more = QCheckBox("More Tools Drawer Toggle Button (••• / Hotkey: M)")
        grid1.addWidget(self.chk_btn_more, 4, 1)

        layout.addLayout(grid1)

        # Section 2: Expandable More Tools Drawer
        sec2 = QLabel("More Tools Drawer Actions (Press M to expand)")
        sec2.setProperty("class", "sectionHeader")
        layout.addWidget(sec2)

        grid2 = QGridLayout()
        grid2.setSpacing(8)

        self.chk_btn_transcript = QCheckBox("📜 Live Transcript Drawer [T] (highlighting & word karaoke)")
        grid2.addWidget(self.chk_btn_transcript, 0, 0)

        self.chk_btn_enqueue = QCheckBox("➕ Add Selection to Queue [Space/Hotkey] (adds highlighted text)")
        grid2.addWidget(self.chk_btn_enqueue, 0, 1)

        self.chk_btn_queue = QCheckBox("📋 Playback Queue Window [Q] (reorder, loop & track items)")
        grid2.addWidget(self.chk_btn_queue, 1, 0)

        self.chk_btn_ocr = QCheckBox("📷 OCR Screen Region [O] (drag selection over screen to read)")
        grid2.addWidget(self.chk_btn_ocr, 1, 1)

        self.chk_btn_save = QCheckBox("💾 Save Audio as MP3 (export current speech to file)")
        grid2.addWidget(self.chk_btn_save, 2, 0)

        self.chk_btn_bookmark = QCheckBox("🔖 Bookmark Quote & Note [B] (saves quote with timestamp)")
        grid2.addWidget(self.chk_btn_bookmark, 2, 1)

        self.chk_btn_anki = QCheckBox("🗂️ Export to Anki Flashcard [A] (creates vocabulary note)")
        grid2.addWidget(self.chk_btn_anki, 3, 0)

        self.chk_btn_sleep = QCheckBox("🌙 Sleep / Pomodoro Timer [Z] (15m, 30m, 45m, 60m, end)")
        grid2.addWidget(self.chk_btn_sleep, 3, 1)

        self.chk_btn_settings = QCheckBox("⚙️ Settings Studio Shortcut (opens this settings window)")
        grid2.addWidget(self.chk_btn_settings, 4, 0)

        self.chk_btn_audio_device = QCheckBox("🎧 Audio Output Device [Switch speakers / headphones]")
        grid2.addWidget(self.chk_btn_audio_device, 4, 1)

        layout.addLayout(grid2)

        # Section 3: Popup Opacity & Transparency
        sec_op = QLabel("Popup Opacity & Transparency")
        sec_op.setProperty("class", "sectionHeader")
        layout.addWidget(sec_op)

        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("Window Opacity:"))
        self.sl_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_opacity.setRange(50, 100)
        self.sl_opacity.setValue(95)
        self.sl_opacity.setSingleStep(5)
        self.lbl_opacity = QLabel("95%")
        self.lbl_opacity.setMinimumWidth(40)
        self.sl_opacity.valueChanged.connect(lambda v: self.lbl_opacity.setText(f"{v}%"))
        op_row.addWidget(self.sl_opacity, 1)
        op_row.addWidget(self.lbl_opacity)
        layout.addLayout(op_row)

        op_hint = QLabel("When mouse hovers over the player, opacity automatically restores to 100%.")
        op_hint.setStyleSheet("color: #94A3B8; font-size: 10px; font-style: italic;")
        layout.addWidget(op_hint)

        # Section 4: Drawer Tools Display Order
        sec_ord = QLabel("Drawer Tools Left-to-Right Display Order")
        sec_ord.setProperty("class", "sectionHeader")
        layout.addWidget(sec_ord)

        ord_row = QHBoxLayout()
        self.list_drawer_order = QListWidget()
        self.list_drawer_order.setMaximumHeight(140)
        self.list_drawer_order.setStyleSheet(
            "background-color: #161822; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 4px;"
        )
        ord_row.addWidget(self.list_drawer_order, 1)

        btn_ord_col = QVBoxLayout()
        btn_ord_up = QPushButton("▲ Move Up")
        btn_ord_up.setObjectName("secondaryBtn")
        btn_ord_up.clicked.connect(lambda: self._move_drawer_item(-1))
        btn_ord_down = QPushButton("▼ Move Down")
        btn_ord_down.setObjectName("secondaryBtn")
        btn_ord_down.clicked.connect(lambda: self._move_drawer_item(1))
        btn_ord_col.addWidget(btn_ord_up)
        btn_ord_col.addWidget(btn_ord_down)
        btn_ord_col.addStretch(1)
        ord_row.addLayout(btn_ord_col)
        layout.addLayout(ord_row)

        # Section 5: Keyboard Shortcuts Remapper
        sec_hk = QLabel("Single-Key Hotkey Remapper")
        sec_hk.setProperty("class", "sectionHeader")
        layout.addWidget(sec_hk)

        hk_grid = QGridLayout()
        hk_grid.setSpacing(6)
        self.hk_inputs = {}
        hotkey_defs = [
            ('transcript', 'Live Transcript', 'T'),
            ('bookmark', 'Bookmark Note', 'B'),
            ('anki', 'Anki Export', 'A'),
            ('queue', 'Queue Window', 'Q'),
            ('ocr', 'OCR Region', 'O'),
            ('sleep', 'Sleep Timer', 'Z'),
            ('more', 'More Tools Toggle', 'M'),
            ('stop', 'Stop Playback', 'S'),
        ]
        for idx, (hk_key, label, def_val) in enumerate(hotkey_defs):
            r, c = divmod(idx, 2)
            lbl = QLabel(f"{label}:")
            lbl.setStyleSheet("font-size: 11px;")
            ed = QLineEdit()
            ed.setMaxLength(1)
            ed.setFixedWidth(40)
            ed.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ed.setText(def_val)
            self.hk_inputs[hk_key] = ed
            row_box = QHBoxLayout()
            row_box.addWidget(lbl)
            row_box.addWidget(ed)
            row_box.addStretch(1)
            hk_grid.addLayout(row_box, r, c)
        layout.addLayout(hk_grid)

        # Section 6: Drawer Behavior & Display Preferences
        sec3 = QLabel("Drawer Behavior & Automation")
        sec3.setProperty("class", "sectionHeader")
        layout.addWidget(sec3)

        self.chk_expand_drawer = QCheckBox("Always keep More Tools Drawer expanded by default on popup launch")
        layout.addWidget(self.chk_expand_drawer)

        layout.addStretch(1)
        scroll.setWidget(w)
        return scroll

    def _move_drawer_item(self, direction: int):
        row = self.list_drawer_order.currentRow()
        if row < 0:
            return
        new_row = row + direction
        if 0 <= new_row < self.list_drawer_order.count():
            item = self.list_drawer_order.takeItem(row)
            self.list_drawer_order.insertItem(new_row, item)
            self.list_drawer_order.setCurrentRow(new_row)

    def _build_player_filter_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(8, 8, 8, 8)

        sec1 = QLabel('Floating On-Screen Player HUD')
        sec1.setProperty('class', 'sectionHeader')
        layout.addWidget(sec1)

        self.chk_show_player = QCheckBox('Show Floating Player popup automatically when speaking')
        layout.addWidget(self.chk_show_player)

        self.chk_enable_replay = QCheckBox('Keep Floating Player open at end of speech for instant Replay / Seeking')
        layout.addWidget(self.chk_enable_replay)

        self.chk_duck_audio = QCheckBox('Automatically duck background audio (Spotify, Firefox) while speaking')
        layout.addWidget(self.chk_duck_audio)

        self.chk_crossfade = QCheckBox('Smooth inter-segment crossfade & gapless playback transitions')
        layout.addWidget(self.chk_crossfade)

        self.chk_auto_pause_bt = QCheckBox('Auto-pause playback when Bluetooth headphones disconnect (rewind 3s on reconnect)')
        layout.addWidget(self.chk_auto_pause_bt)

        self.chk_encrypt_cache = QCheckBox('Encrypt audio cache at rest (AES-256 via Keyring / Fernet)')
        layout.addWidget(self.chk_encrypt_cache)

        theme_row = QHBoxLayout()
        theme_lbl = QLabel('HUD Theme:')
        theme_lbl.setStyleSheet('font-size: 11px; font-weight: 500;')
        theme_row.addWidget(theme_lbl)
        self.cb_theme = QComboBox()
        self.cb_theme.addItem('System Default (Auto-detect)', 'auto')
        self.cb_theme.addItem('Studio Dark', 'dark')
        self.cb_theme.addItem('Clean Light', 'light')
        theme_row.addWidget(self.cb_theme)
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        pos_row = QHBoxLayout()
        btn_reset_pos = QPushButton('Reset Floating Player && Queue Positions')
        btn_reset_pos.setObjectName('secondaryBtn')
        btn_reset_pos.clicked.connect(self._reset_positions)
        pos_row.addWidget(btn_reset_pos)
        pos_row.addStretch(1)
        layout.addLayout(pos_row)

        sec2 = QLabel('Smart Text & Markdown Filtering (Obsidian-style)')
        sec2.setProperty('class', 'sectionHeader')
        layout.addWidget(sec2)

        self.chk_filter_md = QCheckBox('Clean Markdown formatting (headings, bold, links, tables, frontmatter)')
        layout.addWidget(self.chk_filter_md)

        self.chk_filter_urls = QCheckBox('Strip raw web URLs (https://…) so links are not spelled out')
        layout.addWidget(self.chk_filter_urls)

        self.chk_filter_code = QCheckBox('Skip fenced code blocks (```…```) when reading technical notes')
        layout.addWidget(self.chk_filter_code)

        self.chk_replace_symbols = QCheckBox('Speak comparison & math symbols naturally (>=, <=, 320x240, ≈)')
        layout.addWidget(self.chk_replace_symbols)

        self.chk_use_lexicon = QCheckBox('Apply Custom Pronunciation Lexicon (kubectl → kube control, PyQt6 → Pie Cute 6)')
        layout.addWidget(self.chk_use_lexicon)

        self.txt_lexicon = QPlainTextEdit()
        self.txt_lexicon.setPlaceholderText('term = spoken alias (one per line, e.g. kubectl = kube control)')
        self.txt_lexicon.setMaximumHeight(68)
        layout.addWidget(self.txt_lexicon)

        # Pronunciation verification & testing loop
        lex_test_box = QHBoxLayout()
        self.ed_lex_word = QLineEdit()
        self.ed_lex_word.setPlaceholderText('Word (e.g. CUDA)')
        self.ed_lex_alias = QLineEdit()
        self.ed_lex_alias.setPlaceholderText('Phonetic alias (e.g. koo-duh)')
        btn_speak_preview = QPushButton('🔊 Listen Preview')
        btn_speak_preview.setObjectName('secondaryBtn')
        btn_speak_preview.clicked.connect(self._speak_lexicon_preview)
        btn_test_pronounce = QPushButton('Verify with Whisper AI')
        btn_test_pronounce.setObjectName('secondaryBtn')
        btn_test_pronounce.clicked.connect(self._verify_with_whisper)
        btn_add_to_lexicon = QPushButton('Add to Lexicon')
        btn_add_to_lexicon.setObjectName('secondaryBtn')
        btn_add_to_lexicon.clicked.connect(self._add_to_lexicon)
        btn_import_lex = QPushButton('Import Dev Lexicon')
        btn_import_lex.setObjectName('secondaryBtn')
        btn_import_lex.clicked.connect(self._import_dev_lexicon)
        lex_test_box.addWidget(self.ed_lex_word)
        lex_test_box.addWidget(self.ed_lex_alias)
        lex_test_box.addWidget(btn_speak_preview)
        lex_test_box.addWidget(btn_test_pronounce)
        lex_test_box.addWidget(btn_add_to_lexicon)
        lex_test_box.addWidget(btn_import_lex)
        layout.addLayout(lex_test_box)

        # Obsidian Vault Integration for Bookmarks
        sec_obs = QLabel('Obsidian Knowledge Base Integration (Bookmark Export)')
        sec_obs.setProperty('class', 'sectionHeader')
        layout.addWidget(sec_obs)
        obs_row = QHBoxLayout()
        obs_lbl = QLabel('Obsidian Vault Path / Name:')
        obs_lbl.setStyleSheet('font-size: 11px;')
        obs_row.addWidget(obs_lbl)
        self.ed_obs_vault = QLineEdit()
        self.ed_obs_vault.setPlaceholderText('e.g. ~/Documents/Notes or Vault Name (leave blank to use bookmarks.md only)')
        obs_row.addWidget(self.ed_obs_vault, 1)
        layout.addLayout(obs_row)

        # Anki Spaced Repetition Integration
        sec_anki = QLabel('Anki Spaced Repetition Integration (Flashcards)')
        sec_anki.setProperty('class', 'sectionHeader')
        layout.addWidget(sec_anki)
        anki_row = QHBoxLayout()
        anki_deck_lbl = QLabel('Anki Deck:')
        anki_deck_lbl.setStyleSheet('font-size: 11px;')
        anki_row.addWidget(anki_deck_lbl)
        self.ed_anki_deck = QLineEdit()
        self.ed_anki_deck.setPlaceholderText('Default')
        anki_row.addWidget(self.ed_anki_deck)

        anki_model_lbl = QLabel('Card Model:')
        anki_model_lbl.setStyleSheet('font-size: 11px;')
        anki_row.addWidget(anki_model_lbl)
        self.ed_anki_model = QLineEdit()
        self.ed_anki_model.setPlaceholderText('Basic')
        anki_row.addWidget(self.ed_anki_model)
        layout.addLayout(anki_row)

        sec3 = QLabel('Cadence & Punctuation Pauses')
        sec3.setProperty('class', 'sectionHeader')
        layout.addWidget(sec3)

        self.chk_adaptive_prosody = QCheckBox('Adaptive Pitch & Speed Prosody (inflect parentheticals and bracketed notes)')
        layout.addWidget(self.chk_adaptive_prosody)

        self.chk_auto_scroll = QCheckBox('Auto-Scroll Active Window with Speech (xdotool Down key on paragraph boundaries)')
        layout.addWidget(self.chk_auto_scroll)

        self.chk_pause_punct = QCheckBox('Insert extra silence between major punctuation clauses')
        layout.addWidget(self.chk_pause_punct)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel('Inter-clause Pause Delay (ms):'))
        self.sp_pause_delay = QSpinBox()
        self.sp_pause_delay.setRange(0, 2000)
        self.sp_pause_delay.setSingleStep(25)
        delay_row.addWidget(self.sp_pause_delay)
        delay_row.addStretch(1)
        layout.addLayout(delay_row)

        sec_chunk = QLabel('Audio Chunking & Latency Tuning')
        sec_chunk.setProperty('class', 'sectionHeader')
        layout.addWidget(sec_chunk)

        chunk_row = QHBoxLayout()
        chunk_row.addWidget(QLabel('Initial Chunk Size (chars):'))
        self.sp_first_chunk = QSpinBox()
        self.sp_first_chunk.setRange(60, 400)
        self.sp_first_chunk.setValue(140)
        self.sp_first_chunk.setSingleStep(10)
        self.sp_first_chunk.setToolTip('Smaller size (<150 chars) starts playback in <0.4s while subsequent chunks synthesize in parallel')
        chunk_row.addWidget(self.sp_first_chunk)

        chunk_row.addWidget(QLabel('Segment Size (chars):'))
        self.sp_max_segment = QSpinBox()
        self.sp_max_segment.setRange(100, 800)
        self.sp_max_segment.setValue(280)
        self.sp_max_segment.setSingleStep(20)
        self.sp_max_segment.setToolTip('Size of subsequent speech segments processed in parallel')
        chunk_row.addWidget(self.sp_max_segment)
        chunk_row.addStretch(1)
        layout.addLayout(chunk_row)

        sec_audio_eq = QLabel('Voice Clarity & Audio Enhancement')
        sec_audio_eq.setProperty('class', 'sectionHeader')
        layout.addWidget(sec_audio_eq)

        self.chk_voice_clarity = QCheckBox('Enable Voice Clarity EQ Profile (vocal presence boost & 100Hz rumble highpass filter)')
        layout.addWidget(self.chk_voice_clarity)

        layout.addStretch(1)
        return w

    def _build_translate_bench_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(8, 8, 8, 8)

        sec1 = QLabel('Optional Pre-Speech Translation')
        sec1.setProperty('class', 'sectionHeader')
        layout.addWidget(sec1)

        self.chk_trans_enabled = QCheckBox('Translate text before speaking')
        layout.addWidget(self.chk_trans_enabled)

        form = QFormLayout()
        form.setSpacing(8)
        self.cb_trans_provider = QComboBox()
        for p in self.shared.get('translation_providers', []):
            self.cb_trans_provider.addItem(p['name'], p['code'])
        form.addRow('Provider:', self.cb_trans_provider)

        self.cb_trans_target = QComboBox()
        for t in self.shared.get('translation_languages', []):
            self.cb_trans_target.addItem(t['name'], t['code'])
        form.addRow('Target Language:', self.cb_trans_target)

        self.ed_trans_key = QLineEdit()
        self.ed_trans_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_trans_key.setPlaceholderText('DeepL or Google Cloud API Key')
        form.addRow('API Key:', self.ed_trans_key)
        layout.addLayout(form)

        # Quick Speak & Export Test Bench
        sec2 = QLabel('Quick Speak & MP3 Export Bench')
        sec2.setProperty('class', 'sectionHeader')
        layout.addWidget(sec2)

        self.txt_bench = QPlainTextEdit()
        self.txt_bench.setPlaceholderText(
            'Paste or type any English, Arabic, or German text here to test speaking or export directly to MP3…'
        )
        self.txt_bench.setMaximumHeight(95)
        layout.addWidget(self.txt_bench)

        bench_btns = QHBoxLayout()
        btn_speak_now = QPushButton('▶  Speak Text')
        btn_speak_now.setObjectName('primaryBtn')
        btn_speak_now.clicked.connect(self._on_bench_speak)
        bench_btns.addWidget(btn_speak_now)

        btn_queue_now = QPushButton('+  Add to Queue')
        btn_queue_now.setObjectName('secondaryBtn')
        btn_queue_now.clicked.connect(self._on_bench_queue)
        bench_btns.addWidget(btn_queue_now)

        btn_export_mp3 = QPushButton('⬇  Export MP3…')
        btn_export_mp3.setObjectName('secondaryBtn')
        btn_export_mp3.clicked.connect(self._on_bench_export)
        bench_btns.addWidget(btn_export_mp3)

        btn_ocr_region = QPushButton('📷  OCR Screen Region')
        btn_ocr_region.setObjectName('secondaryBtn')
        btn_ocr_region.clicked.connect(self._on_bench_ocr)
        bench_btns.addWidget(btn_ocr_region)
        bench_btns.addStretch(1)
        layout.addLayout(bench_btns)

        # Cache Management
        sec3 = QLabel('Audio Cache (~/.cache/speak-aloud)')
        sec3.setProperty('class', 'sectionHeader')
        layout.addWidget(sec3)

        cache_row = QHBoxLayout()
        self.lbl_cache_info = QLabel('')
        cache_row.addWidget(self.lbl_cache_info, 1)
        btn_clear_cache = QPushButton('Clear Audio Cache')
        btn_clear_cache.setObjectName('secondaryBtn')
        btn_clear_cache.clicked.connect(self._clear_cache)
        cache_row.addWidget(btn_clear_cache)
        layout.addLayout(cache_row)

        self._refresh_cache_stats()
        layout.addStretch(1)
        return w

    def _make_rate_slider(self):
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(0, 100)
        sl.setSingleStep(5)
        lbl = QLabel('+50%')
        lbl.setMinimumWidth(48)
        sl.valueChanged.connect(lambda v: lbl.setText(f'+{v}%'))
        return sl, lbl

    def _wrap_slider(self, slider: QSlider, label: QLabel) -> QWidget:
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        hl.addWidget(slider, 1)
        hl.addWidget(label)
        return w

    def _set_combo_by_data(self, cb: QComboBox, value: str):
        for i in range(cb.count()):
            if cb.itemData(i) == value:
                cb.setCurrentIndex(i)
                return
        # If custom voice not in list, add it
        if value:
            cb.insertItem(0, f'{value} (Custom)', value)
            cb.setCurrentIndex(0)

    def _load_values(self):
        en_v = read_cfg('voice', self.defaults.get('voice', 'en-US-ChristopherNeural'))
        ar_v = read_cfg('arabic_voice', self.defaults.get('arabic_voice', 'ar-SA-HamedNeural'))
        de_v = read_cfg('german_voice', self.defaults.get('german_voice', 'de-DE-ConradNeural'))
        self._set_combo_by_data(self.cb_en_voice, en_v)
        self._set_combo_by_data(self.cb_ar_voice, ar_v)
        self._set_combo_by_data(self.cb_de_voice, de_v)

        en_r = max(0, min(100, read_int_cfg('rate', self.defaults.get('rate', 50))))
        ar_r = max(0, min(100, read_int_cfg('arabic_rate', self.defaults.get('arabic_rate', 30))))
        de_r = max(0, min(100, read_int_cfg('german_rate', self.defaults.get('german_rate', 30))))
        self.sl_en_rate.setValue(en_r)
        self.lbl_en_rate.setText(f'+{en_r}%')
        self.sl_ar_rate.setValue(ar_r)
        self.lbl_ar_rate.setText(f'+{ar_r}%')
        self.sl_de_rate.setValue(de_r)
        self.lbl_de_rate.setText(f'+{de_r}%')

        src_lang = read_cfg('source_language', self.defaults.get('source_language', 'auto'))
        self._set_combo_by_data(self.cb_source_lang, src_lang)

        try:
            spd = float(read_cfg('global_speed', str(self.defaults.get('global_speed', 1.0))))
        except ValueError:
            spd = 1.0
        self.sl_speed.setValue(max(50, min(200, int(round(spd * 100)))))

        pitch_str = read_cfg('pitch', '+0Hz')
        try:
            m = re.match(r'^([+-]?[0-9]+)', pitch_str)
            p_val = int(m.group(1)) if m else 0
        except Exception:
            p_val = 0
        self.sl_pitch.setValue(max(-25, min(25, p_val)))
        self.lbl_pitch.setText(f'{p_val:+d}Hz')

        self.chk_show_player.setChecked(read_bool_cfg('show_hud', read_bool_cfg('show_floating_player', True)))
        self.chk_enable_replay.setChecked(read_bool_cfg('replay_at_end', read_bool_cfg('enable_replay', True)))
        self.chk_duck_audio.setChecked(read_bool_cfg('duck_background_audio', False))
        self.chk_crossfade.setChecked(read_bool_cfg('crossfade_audio', True))
        self.chk_auto_pause_bt.setChecked(read_bool_cfg('auto_pause_bluetooth', True))
        self.chk_encrypt_cache.setChecked(read_bool_cfg('encrypt_audio_cache', False))
        theme_val = read_cfg('ui_theme', 'auto').strip().lower()
        if theme_val not in ('auto', 'dark', 'light'):
            theme_val = 'auto'
        self._set_combo_by_data(self.cb_theme, theme_val)
        self.ed_obs_vault.setText(read_cfg('obsidian_vault', ''))
        self.ed_anki_deck.setText(read_cfg('anki_deck', 'Default'))
        self.ed_anki_model.setText(read_cfg('anki_model', 'Basic'))
        self.chk_adaptive_prosody.setChecked(read_bool_cfg('adaptive_prosody', True))
        self.chk_auto_scroll.setChecked(read_bool_cfg('auto_scroll_window', False))
        self.sp_first_chunk.setValue(max(60, min(400, read_int_cfg('first_chunk_max', 140, strip_chars=''))))
        self.sp_max_segment.setValue(max(100, min(800, read_int_cfg('max_segment_chars', 280, strip_chars=''))))
        self.chk_voice_clarity.setChecked(read_bool_cfg('voice_clarity_eq', False))
        # Popup Player Buttons
        self.chk_btn_speed.setChecked(read_bool_cfg('hud_btn_speed', True))
        self.chk_btn_mode_tag.setChecked(read_bool_cfg('hud_btn_mode_tag', True))
        self.chk_btn_back.setChecked(read_bool_cfg('hud_btn_back', True))
        self.chk_btn_fwd.setChecked(read_bool_cfg('hud_btn_fwd', True))
        self.chk_btn_stop.setChecked(read_bool_cfg('hud_btn_stop', True))
        self.chk_show_progress.setChecked(read_bool_cfg('hud_show_progress', True))
        self.chk_show_chunks.setChecked(read_bool_cfg('hud_show_chunks', True))
        self.chk_btn_close.setChecked(read_bool_cfg('hud_btn_close', True))
        self.chk_btn_more.setChecked(read_bool_cfg('hud_btn_more', True))
        self.chk_btn_transcript.setChecked(read_bool_cfg('hud_btn_transcript', True))
        self.chk_btn_enqueue.setChecked(read_bool_cfg('hud_btn_enqueue', True))
        self.chk_btn_queue.setChecked(read_bool_cfg('hud_btn_queue', True))
        self.chk_btn_ocr.setChecked(read_bool_cfg('hud_btn_ocr', True))
        self.chk_btn_save.setChecked(read_bool_cfg('hud_btn_save', True))
        self.chk_btn_bookmark.setChecked(read_bool_cfg('hud_btn_bookmark', True))
        self.chk_btn_anki.setChecked(read_bool_cfg('hud_btn_anki', True))
        self.chk_btn_sleep.setChecked(read_bool_cfg('hud_btn_sleep', True))
        self.chk_btn_settings.setChecked(read_bool_cfg('hud_btn_settings', True))
        self.chk_btn_audio_device.setChecked(read_bool_cfg('hud_btn_audio_device', True))
        self.chk_expand_drawer.setChecked(read_bool_cfg('hud_expand_tools_drawer', False))

        # Popup Opacity & Transparency
        try:
            op_val = float(read_cfg('hud_opacity', '0.95'))
            self.sl_opacity.setValue(max(50, min(100, int(round(op_val * 100)))))
        except Exception:
            self.sl_opacity.setValue(95)
        self.lbl_opacity.setText(f"{self.sl_opacity.value()}%")

        # Drawer Display Order
        drawer_labels = {
            'enqueue': '+ Queue Current',
            'transcript': 'Live Transcript',
            'queue': 'Queue Manager',
            'ocr': 'OCR Region',
            'save': 'Save Passage',
            'bookmark': 'Bookmark Note',
            'anki': 'Anki Flashcard',
            'sleep': 'Sleep Timer',
            'audio_device': 'Audio Device',
            'settings': 'Settings Studio',
        }
        order_str = read_cfg('hud_drawer_order', '').strip()
        custom_keys = [k.strip() for k in order_str.split(',') if k.strip() in drawer_labels] if order_str else []
        default_keys = ['enqueue', 'transcript', 'queue', 'ocr', 'save', 'bookmark', 'anki', 'sleep', 'audio_device', 'settings']
        final_keys = custom_keys + [k for k in default_keys if k not in custom_keys]

        self.list_drawer_order.clear()
        for k in final_keys:
            item = QListWidgetItem(drawer_labels.get(k, k))
            item.setData(Qt.ItemDataRole.UserRole, k)
            self.list_drawer_order.addItem(item)

        # Single-Key Hotkeys
        raw_hk = read_cfg('custom_hotkeys', '')
        parsed_hk = {}
        if raw_hk:
            try:
                parsed_hk = json.loads(raw_hk)
            except Exception:
                pass
        for k, ed in self.hk_inputs.items():
            if k in parsed_hk and parsed_hk[k]:
                ed.setText(str(parsed_hk[k]).upper()[:1])

        self.chk_filter_md.setChecked(read_bool_cfg('filter_markdown', True))
        self.chk_filter_urls.setChecked(read_bool_cfg('filter_urls', True))
        self.chk_filter_code.setChecked(read_bool_cfg('skip_code', read_bool_cfg('filter_code', False)))
        self.chk_replace_symbols.setChecked(read_bool_cfg('replace_symbols', True))
        self.chk_use_lexicon.setChecked(read_bool_cfg('use_lexicon', True))
        lex = load_lexicon()
        self.txt_lexicon.setPlainText('\n'.join(f'{k} = {v}' for k, v in lex.items()))
        self.chk_pause_punct.setChecked(read_bool_cfg('pause_punctuation', False))
        self.sp_pause_delay.setValue(max(0, min(2000, read_int_cfg('pause_delay', read_int_cfg('pause_delay_ms', 100, strip_chars=''), strip_chars=''))))

        self.chk_trans_enabled.setChecked(read_bool_cfg('translate_enabled', False))
        self._set_combo_by_data(self.cb_trans_provider, read_cfg('translate_provider', 'deepl'))
        self._set_combo_by_data(self.cb_trans_target, read_cfg('translate_to', read_cfg('translate_target', '')))
        self.ed_trans_key.setText(read_cfg('translate_api_key', ''))

    def _save_all(self):
        en_v = self.ed_custom_voice.text().strip() or self.cb_en_voice.currentData()
        ar_v = self.cb_ar_voice.currentData()
        de_v = self.cb_de_voice.currentData()
        en_rate = f'+{self.sl_en_rate.value()}%'
        ar_rate = f'+{self.sl_ar_rate.value()}%'
        de_rate = f'+{self.sl_de_rate.value()}%'
        src_lang = self.cb_source_lang.currentData() or 'auto'
        spd_str = f'{self.sl_speed.value() / 100.0:.2f}'
        show_hud = 'yes' if self.chk_show_player.isChecked() else 'no'
        replay = 'yes' if self.chk_enable_replay.isChecked() else 'no'
        duck_bg = 'yes' if self.chk_duck_audio.isChecked() else 'no'
        crossfade = 'yes' if self.chk_crossfade.isChecked() else 'no'
        pause_bt = 'yes' if self.chk_auto_pause_bt.isChecked() else 'no'
        encrypt_c = 'yes' if self.chk_encrypt_cache.isChecked() else 'no'
        ui_theme = self.cb_theme.currentData() or 'auto'
        pitch_val = f'{self.sl_pitch.value():+d}Hz'
        obs_vault = self.ed_obs_vault.text().strip()
        anki_deck = self.ed_anki_deck.text().strip() or 'Default'
        anki_model = self.ed_anki_model.text().strip() or 'Basic'
        adaptive_prosody = 'yes' if self.chk_adaptive_prosody.isChecked() else 'no'
        auto_scroll = 'yes' if self.chk_auto_scroll.isChecked() else 'no'
        first_chunk = str(self.sp_first_chunk.value())
        max_seg = str(self.sp_max_segment.value())
        clarity_eq = 'yes' if self.chk_voice_clarity.isChecked() else 'no'

        # Popup Button Visibility
        btn_speed = 'yes' if self.chk_btn_speed.isChecked() else 'no'
        btn_mode_tag = 'yes' if self.chk_btn_mode_tag.isChecked() else 'no'
        btn_back = 'yes' if self.chk_btn_back.isChecked() else 'no'
        btn_fwd = 'yes' if self.chk_btn_fwd.isChecked() else 'no'
        btn_stop = 'yes' if self.chk_btn_stop.isChecked() else 'no'
        show_prog = 'yes' if self.chk_show_progress.isChecked() else 'no'
        show_chunks = 'yes' if self.chk_show_chunks.isChecked() else 'no'
        btn_close = 'yes' if self.chk_btn_close.isChecked() else 'no'
        btn_more = 'yes' if self.chk_btn_more.isChecked() else 'no'
        btn_transcript = 'yes' if self.chk_btn_transcript.isChecked() else 'no'
        btn_enqueue = 'yes' if self.chk_btn_enqueue.isChecked() else 'no'
        btn_queue = 'yes' if self.chk_btn_queue.isChecked() else 'no'
        btn_ocr = 'yes' if self.chk_btn_ocr.isChecked() else 'no'
        btn_save = 'yes' if self.chk_btn_save.isChecked() else 'no'
        btn_bookmark = 'yes' if self.chk_btn_bookmark.isChecked() else 'no'
        btn_anki = 'yes' if self.chk_btn_anki.isChecked() else 'no'
        btn_sleep = 'yes' if self.chk_btn_sleep.isChecked() else 'no'
        btn_settings = 'yes' if self.chk_btn_settings.isChecked() else 'no'
        btn_audio_device = 'yes' if self.chk_btn_audio_device.isChecked() else 'no'
        expand_drawer = 'yes' if self.chk_expand_drawer.isChecked() else 'no'
        hud_opacity = f"{self.sl_opacity.value() / 100.0:.2f}"

        drawer_order_keys = []
        for i in range(self.list_drawer_order.count()):
            item = self.list_drawer_order.item(i)
            k = item.data(Qt.ItemDataRole.UserRole)
            if k:
                drawer_order_keys.append(str(k))
        drawer_order_str = ','.join(drawer_order_keys)

        saved_hk = {}
        for k, ed in self.hk_inputs.items():
            val = ed.text().strip().upper()
            if val:
                saved_hk[k] = val
        custom_hotkeys_str = json.dumps(saved_hk)

        f_md = 'yes' if self.chk_filter_md.isChecked() else 'no'
        f_urls = 'yes' if self.chk_filter_urls.isChecked() else 'no'
        f_code = 'yes' if self.chk_filter_code.isChecked() else 'no'
        r_sym = 'yes' if self.chk_replace_symbols.isChecked() else 'no'
        u_lex = 'yes' if self.chk_use_lexicon.isChecked() else 'no'
        p_punct = 'yes' if self.chk_pause_punct.isChecked() else 'no'
        p_delay = str(self.sp_pause_delay.value())
        t_en = 'yes' if self.chk_trans_enabled.isChecked() else 'no'
        t_prov = self.cb_trans_provider.currentData() or 'deepl'
        t_targ = self.cb_trans_target.currentData() or 'en'
        t_key = self.ed_trans_key.text().strip()

        # Validate and atomically write settings.json + schema keys
        store = TTSSettingsStore()
        store.save({
            'voice_en': en_v,
            'voice_ar': ar_v,
            'voice_de': de_v,
            'custom_voice': self.ed_custom_voice.text().strip(),
            'rate_en': en_rate,
            'rate_ar': ar_rate,
            'rate_de': de_rate,
            'global_speed': spd_str,
            'pitch': pitch_val,
            'show_hud': show_hud,
            'replay_at_end': replay,
            'filter_markdown': f_md,
            'filter_urls': f_urls,
            'skip_code': f_code,
            'replace_symbols': r_sym,
            'use_lexicon': u_lex,
            'pause_punctuation': p_punct,
            'pause_delay': p_delay,
            'translate_enabled': t_en,
            'translate_provider': t_prov,
            'translate_to': t_targ,
            'translate_from': src_lang,
            'translate_api_key': t_key,
            'duck_background_audio': duck_bg,
            'crossfade_audio': crossfade,
            'auto_pause_bluetooth': pause_bt,
            'encrypt_audio_cache': encrypt_c,
            'ui_theme': ui_theme,
            'obsidian_vault': obs_vault,
            'anki_deck': anki_deck,
            'anki_model': anki_model,
            'adaptive_prosody': adaptive_prosody,
            'auto_scroll_window': auto_scroll,
            'first_chunk_max': first_chunk,
            'max_segment_chars': max_seg,
            'voice_clarity_eq': clarity_eq,
            'hud_btn_speed': btn_speed,
            'hud_btn_mode_tag': btn_mode_tag,
            'hud_btn_back': btn_back,
            'hud_btn_play_pause': 'yes',
            'hud_btn_fwd': btn_fwd,
            'hud_btn_stop': btn_stop,
            'hud_show_progress': show_prog,
            'hud_show_chunks': show_chunks,
            'hud_btn_close': btn_close,
            'hud_btn_more': btn_more,
            'hud_btn_transcript': btn_transcript,
            'hud_btn_enqueue': btn_enqueue,
            'hud_btn_queue': btn_queue,
            'hud_btn_ocr': btn_ocr,
            'hud_btn_save': btn_save,
            'hud_btn_bookmark': btn_bookmark,
            'hud_btn_anki': btn_anki,
            'hud_btn_sleep': btn_sleep,
            'hud_btn_settings': btn_settings,
            'hud_btn_audio_device': btn_audio_device,
            'hud_expand_tools_drawer': expand_drawer,
            'hud_opacity': hud_opacity,
            'hud_drawer_order': drawer_order_str,
            'custom_hotkeys': custom_hotkeys_str,
        })

        # Also maintain legacy flat filenames for backward compatibility
        write_cfg('voice', en_v)
        write_cfg('arabic_voice', ar_v)
        write_cfg('german_voice', de_v)
        write_cfg('rate', en_rate)
        write_cfg('arabic_rate', ar_rate)
        write_cfg('german_rate', de_rate)
        write_cfg('source_language', src_lang)
        write_cfg('show_floating_player', show_hud)
        write_cfg('enable_replay', replay)
        write_cfg('filter_code', f_code)
        write_cfg('pause_delay_ms', p_delay)
        write_cfg('translate_target', self.cb_trans_target.currentData() or '')
        write_cfg('ui_theme', ui_theme)
        write_cfg('pitch', pitch_val)
        write_cfg('obsidian_vault', obs_vault)
        write_cfg('anki_deck', anki_deck)
        write_cfg('anki_model', anki_model)
        write_cfg('adaptive_prosody', adaptive_prosody)
        write_cfg('auto_scroll_window', auto_scroll)
        write_cfg('first_chunk_max', first_chunk)
        write_cfg('max_segment_chars', max_seg)
        write_cfg('voice_clarity_eq', clarity_eq)
        write_cfg('crossfade_audio', crossfade)
        write_cfg('auto_pause_bluetooth', pause_bt)
        write_cfg('encrypt_audio_cache', encrypt_c)
        write_cfg('hud_btn_speed', btn_speed)
        write_cfg('hud_btn_stop', btn_stop)
        write_cfg('hud_show_progress', show_prog)
        write_cfg('hud_btn_more', btn_more)
        write_cfg('hud_btn_transcript', btn_transcript)
        write_cfg('hud_btn_ocr', btn_ocr)
        write_cfg('hud_btn_audio_device', btn_audio_device)
        write_cfg('hud_opacity', hud_opacity)
        write_cfg('hud_drawer_order', drawer_order_str)
        write_cfg('custom_hotkeys', custom_hotkeys_str)

    def _speak_lexicon_preview(self):
        word = self.ed_lex_word.text().strip()
        alias = self.ed_lex_alias.text().strip()
        if not word and not alias:
            self._set_status('Please enter a word or phonetic alias to listen.', is_error=True)
            return
        test_txt = alias if alias else word
        self._set_status(f"🔊 Playing pronunciation: '{test_txt}'...")
        speak_sh = os.path.join(SCRIPT_DIR, 'speak.sh')
        subprocess.Popen(
            ['bash', speak_sh, '--text', test_txt],
            env=get_sanitized_env(),
        )

    def _verify_with_whisper(self):
        word = self.ed_lex_word.text().strip()
        alias = self.ed_lex_alias.text().strip()
        if not word and not alias:
            self._set_status('Please enter a word or phonetic alias to verify with Whisper AI.', is_error=True)
            return

        spoken_text = alias if alias else word
        self._set_status(f"Whisper AI verifying '{spoken_text}'...")

        def _worker():
            try:
                from tts_whisper import verify_pronunciation_with_whisper
                active_lang = self.cb_source_lang.currentData() or 'en'
                active_voice = self.cb_en_voice.currentData() or 'en-US-ChristopherNeural'
                res = verify_pronunciation_with_whisper(
                    text=spoken_text,
                    voice=active_voice,
                    lang=active_lang if active_lang != 'auto' else 'en',
                )
                score = res.get('match_score', 0.0)
                trans = res.get('transcribed', '')
                is_match = res.get('is_match', False)
                if res.get('success'):
                    status_msg = f"Whisper STT: '{trans}' | Accuracy: {score}% {'(✓ Match)' if is_match else '(Needs adjustment)'}"
                    QTimer.singleShot(0, lambda: self._set_status(status_msg, is_error=not is_match))
                else:
                    err = res.get('error', 'Failed')
                    QTimer.singleShot(0, lambda: self._set_status(f"Whisper error: {err}", is_error=True))
            except Exception as e:
                QTimer.singleShot(0, lambda: self._set_status(f"Whisper error: {e}", is_error=True))

        threading.Thread(target=_worker, daemon=True).start()

    def _verify_pronunciation(self):
        word = self.ed_lex_word.text().strip()
        alias = self.ed_lex_alias.text().strip()
        if not word and not alias:
            self._set_status('Please enter a word or phonetic alias to test.', is_error=True)
            return
        test_txt = alias if alias else word
        self._set_status(f"Testing pronunciation: '{test_txt}'...")
        speak_sh = os.path.join(SCRIPT_DIR, 'speak.sh')
        subprocess.Popen(
            ['bash', speak_sh, '--text', f"Testing pronunciation: {test_txt}"],
            env=get_sanitized_env(),
        )

    def _add_to_lexicon(self):
        word = self.ed_lex_word.text().strip()
        alias = self.ed_lex_alias.text().strip()
        if not word or not alias:
            self._set_status('Enter both Word and Phonetic Alias to add to lexicon.', is_error=True)
            return
        cur_text = self.txt_lexicon.toPlainText().strip()
        line = f'{word} = {alias}'
        new_text = f"{cur_text}\n{line}" if cur_text else line
        self.txt_lexicon.setPlainText(new_text)
        self.ed_lex_word.clear()
        self.ed_lex_alias.clear()
        self._set_status(f"Added '{word} = {alias}' to lexicon (remember to click Save).")

        # Save lexicon.json atomically
        lex_map = {}
        for line in self.txt_lexicon.toPlainText().splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                if k.strip() and v.strip():
                    lex_map[k.strip()] = v.strip()
        if lex_map:
            save_lexicon(lex_map)

    def _import_dev_lexicon(self):
        added = import_community_lexicon()
        lex = load_lexicon()
        self.txt_lexicon.setPlainText('\n'.join(f'{k} = {v}' for k, v in sorted(lex.items())))
        self._set_status(f"Imported {added} tech terms into custom lexicon.")

    def _build_analytics_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        hdr = QLabel('Reading Analytics & Productivity Tracker')
        hdr.setProperty('class', 'sectionHeader')
        layout.addWidget(hdr)

        self.stats_card = QFrame()
        self.stats_card.setStyleSheet("""
            QFrame {
                background-color: #12141A;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 14px;
            }
        """)
        stats_layout = QVBoxLayout(self.stats_card)
        stats_layout.setSpacing(8)

        self.lbl_analytics_today = QLabel('Today: 0.0 minutes listened (0 words)')
        self.lbl_analytics_today.setStyleSheet('font-size: 13px; font-weight: 600; color: #F3F4F6;')
        stats_layout.addWidget(self.lbl_analytics_today)

        self.lbl_analytics_week = QLabel('Past 7 Days: 0.0 minutes (0 words)')
        self.lbl_analytics_week.setStyleSheet('font-size: 12px; color: #9CA3AF;')
        stats_layout.addWidget(self.lbl_analytics_week)

        self.lbl_analytics_saved = QLabel('Estimated Reading Time Saved: ~0.0 minutes')
        self.lbl_analytics_saved.setStyleSheet('font-size: 12px; color: #34D399; font-weight: 500;')
        stats_layout.addWidget(self.lbl_analytics_saved)

        layout.addWidget(self.stats_card)

        btn_refresh = QPushButton('Refresh Analytics')
        btn_refresh.setObjectName('secondaryBtn')
        btn_refresh.clicked.connect(self._refresh_analytics)
        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        sec_hist = QLabel('Spoken Reading History Log')
        sec_hist.setProperty('class', 'sectionHeader')
        layout.addWidget(sec_hist)

        hist_box = QHBoxLayout()
        btn_export_hist_csv = QPushButton('Export History (.csv)')
        btn_export_hist_csv.setObjectName('secondaryBtn')
        btn_export_hist_csv.clicked.connect(self._export_history_csv)
        hist_box.addWidget(btn_export_hist_csv)

        btn_export_hist_md = QPushButton('Export History (.md)')
        btn_export_hist_md.setObjectName('secondaryBtn')
        btn_export_hist_md.clicked.connect(self._export_history_md)
        hist_box.addWidget(btn_export_hist_md)
        hist_box.addStretch(1)
        layout.addLayout(hist_box)

        layout.addStretch(1)
        self._refresh_analytics()
        return w

    def _export_history_csv(self):
        default_path = os.path.expanduser('~/Downloads/spoken_history.csv')
        dest, _ = QFileDialog.getSaveFileName(self, 'Export Spoken History as CSV', default_path, 'CSV Files (*.csv)')
        if dest:
            from tts_text_processor import export_history_as_csv
            if export_history_as_csv(dest):
                self._set_status(f"Exported history to {os.path.basename(dest)}")
            else:
                self._set_status("No spoken history entries found to export.", is_error=True)

    def _export_history_md(self):
        default_path = os.path.expanduser('~/Downloads/spoken_history.md')
        dest, _ = QFileDialog.getSaveFileName(self, 'Export Spoken History as Markdown', default_path, 'Markdown Files (*.md)')
        if dest:
            from tts_text_processor import export_history_as_markdown
            if export_history_as_markdown(dest):
                self._set_status(f"Exported history to {os.path.basename(dest)}")
            else:
                self._set_status("No spoken history entries found to export.", is_error=True)

    def _refresh_analytics(self):
        try:
            data = get_analytics_summary()
            self.lbl_analytics_today.setText(f"Today: {data['today_minutes']} minutes listened ({data['today_words']:,} words)")
            self.lbl_analytics_week.setText(f"Past 7 Days: {data['week_minutes']} minutes ({data['week_words']:,} words)")
            self.lbl_analytics_saved.setText(f"Estimated Reading Time Saved: ~{data['saved_minutes']} minutes (listening vs visual reading)")
        except Exception:
            pass

    def _on_save(self):
        self._save_all()
        self.lbl_status.setText('✓ Settings saved to ~/.config/tts_settings')
        self.accept()

    def _test_voice(self, lang: str):
        self._save_all()
        samples = {
            'en': 'Speak a Loud Universal is ready with neural speech and live timeline controls.',
            'ar': 'مرحبا بكم، نظام القراءة الصوتية الذكي يعمل الآن بجودة عالية.',
            'de': 'Willkommen! Die Sprachausgabe funktioniert jetzt schnell und zuverlässig.',
        }
        subprocess.Popen(
            ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--text', samples[lang], '--source-lang', lang],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.lbl_status.setText(f'▶ Playing {lang.upper()} sample…')

    def _on_bench_speak(self):
        self._save_all()
        text = self.txt_bench.toPlainText().strip()
        if not text:
            self.lbl_status.setText('Enter some text in the box first.')
            return
        subprocess.Popen(
            ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--text', text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.lbl_status.setText('▶ Speaking bench text…')

    def _on_bench_queue(self):
        self._save_all()
        text = self.txt_bench.toPlainText().strip()
        if not text:
            self.lbl_status.setText('Enter some text in the box first.')
            return
        subprocess.Popen(
            ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--queue', '--text', text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.lbl_status.setText('✓ Added bench text to Playback Queue.')

    def _on_bench_export(self):
        self._save_all()
        text = self.txt_bench.toPlainText().strip()
        if not text:
            self.lbl_status.setText('Enter some text in the box first.')
            return
        dest, _ = QFileDialog.getSaveFileName(
            self,
            'Export Speech to MP3',
            os.path.expanduser('~/Downloads/speech.mp3'),
            'MP3 Audio (*.mp3)',
        )
        if dest:
            if not dest.lower().endswith('.mp3'):
                dest += '.mp3'
            subprocess.Popen(
                ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--text', text, '--generate-only', '--output', dest],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.lbl_status.setText(f'⟳ Generating MP3 to {os.path.basename(dest)}…')

    def _on_bench_ocr(self):
        self._save_all()
        self.lbl_status.setText('📷 Drag a rectangle on screen to read via OCR…')
        subprocess.Popen(
            ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--ocr'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _reset_positions(self):
        try:
            if os.path.exists(POSITIONS_FILE):
                os.remove(POSITIONS_FILE)
            self.lbl_status.setText('✓ Floating Player & Queue positions reset.')
        except OSError:
            pass

    def _refresh_cache_stats(self):
        files = glob.glob(os.path.join(CACHE_DIR, '*.mp3'))
        total_bytes = sum(os.path.getsize(f) for f in files if os.path.exists(f))
        mb = total_bytes / (1024 * 1024)
        self.lbl_cache_info.setText(f'{len(files)} cached audio segments ({mb:.1f} MB)')

    def _clear_cache(self):
        files = glob.glob(os.path.join(CACHE_DIR, '*.mp3'))
        for f in files:
            try:
                os.remove(f)
            except OSError:
                pass
        self._refresh_cache_stats()
        self.lbl_status.setText('✓ Audio cache cleared.')


def main():
    app = QApplication(sys.argv)
    win = TTSSettingsWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
