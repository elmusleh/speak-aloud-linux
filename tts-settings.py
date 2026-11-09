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
import subprocess
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
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
        self.tabs.addTab(self._build_player_filter_tab(), 'Floating HUD && Text Filter')
        self.tabs.addTab(self._build_translate_bench_tab(), 'Translation && Test Bench')
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

        return w

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

        sec3 = QLabel('Cadence & Punctuation Pauses')
        sec3.setProperty('class', 'sectionHeader')
        layout.addWidget(sec3)

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

        self.chk_show_player.setChecked(read_bool_cfg('show_hud', read_bool_cfg('show_floating_player', True)))
        self.chk_enable_replay.setChecked(read_bool_cfg('replay_at_end', read_bool_cfg('enable_replay', True)))
        self.chk_duck_audio.setChecked(read_bool_cfg('duck_background_audio', False))
        theme_val = read_cfg('ui_theme', 'auto').strip().lower()
        if theme_val not in ('auto', 'dark', 'light'):
            theme_val = 'auto'
        self._set_combo_by_data(self.cb_theme, theme_val)
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
        ui_theme = self.cb_theme.currentData() or 'auto'
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
            'ui_theme': ui_theme,
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

        # Save lexicon.json atomically
        lex_map = {}
        for line in self.txt_lexicon.toPlainText().splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                if k.strip() and v.strip():
                    lex_map[k.strip()] = v.strip()
        if lex_map:
            save_lexicon(lex_map)

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
