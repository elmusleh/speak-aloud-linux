#!/usr/bin/env python3
"""
Floating Player HUD, Chunked Progress UI, and Playback Queue Manager
for Speak a Loud Universal.

Inspired by obsidian-edge-tts (FloatingPlayerUI.tsx, ChunkedProgressUI.tsx,
QueueManagerUI.tsx) and crafted for Linux desktops using PyQt6 + QtSvg.
"""

import html
import json
import os
import shutil
import socket
import subprocess
import time
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    QByteArray,
    QPoint,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
import sys
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import (
    CONFIG_DIR,
    TTSSettingsStore,
    atomic_write_json,
    atomic_write_text,
    ensure_runtime_symlinks,
    get_effective_theme,
    get_mpv_socket_path,
    get_queue_path,
    get_sanitized_env,
    get_status_path,
    get_work_dir,
    invalidate_theme_cache,
    parse_rate_multiplier,
    read_str_config,
)

ensure_runtime_symlinks()
TEMP_DIR = os.environ.get('TMPDIR', '/tmp')
WORK_DIR = get_work_dir()
MPV_SOCKET = get_mpv_socket_path()
STATUS_FILE = get_status_path()
QUEUE_FILE = get_queue_path()
POSITIONS_FILE = os.path.join(CONFIG_DIR, 'ui_positions.json')

# ── Lucide SVG Paths (matching obsidian-edge-tts icons) ───────────────────────

LUCIDE_SVGS: Dict[str, str] = {
    'play': '<polygon points="6 3 20 12 6 21 6 3" fill="{color}" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/>',
    'pause': '<rect x="6" y="4" width="4" height="16" rx="1" fill="{color}"/><rect x="14" y="4" width="4" height="16" rx="1" fill="{color}"/>',
    'square': '<rect x="5" y="5" width="14" height="14" rx="2" fill="{color}"/>',
    'rotate-ccw': (
        '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M3 3v5h5" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    'rotate-cw': (
        '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M21 3v5h-5" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    'x': '<path d="M18 6 6 18M6 6l12 12" stroke="{color}" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    'list-music': (
        '<path d="M21 15V6M18.5 18a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM12 12H3M16 6H3M12 18H3" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    'plus': '<path d="M12 5v14M5 12h14" stroke="{color}" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    'trash-2': (
        '<path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    'repeat': (
        '<path d="m17 2 4 4-4 4" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M3 11v-1a4 4 0 0 1 4-4h14" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="m7 22-4-4 4-4" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M21 13v1a4 4 0 0 1-4 4H3" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    'scroll-text': (
        '<path d="M8 21h12a2 2 0 0 0 2-2v-2H10v2a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v3h4" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M19 17V5a2 2 0 0 0-2-2H4" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M15 8h-5M15 12h-5" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    'chevron-up': '<path d="m18 15-6-6-6 6" stroke="{color}" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    'chevron-down': '<path d="m6 9 6 6 6-6" stroke="{color}" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    'volume-2': (
        '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="{color}" stroke="{color}" stroke-width="1.5" stroke-linejoin="round"/>'
        '<path d="M15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"/>'
    ),
    'download': (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    'bookmark': (
        '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    'sliders': (
        '<line x1="4" x2="4" y1="21" y2="14" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="4" x2="4" y1="10" y2="3" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="12" x2="12" y1="21" y2="12" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="12" x2="12" y1="8" y2="3" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="20" x2="20" y1="21" y2="16" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="20" x2="20" y1="12" y2="3" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="2" x2="6" y1="14" y2="14" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="10" x2="14" y1="8" y2="8" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="18" x2="22" y1="16" y2="16" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
    ),
    'check': '<path d="M20 6 9 17l-5-5" stroke="{color}" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    'clock': '<circle cx="12" cy="12" r="9" stroke="{color}" stroke-width="2" fill="none"/><polyline points="12 7 12 12 15 15" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"/>',
    'loader-2': '<path d="M21 12a9 9 0 1 1-6.219-8.56" stroke="{color}" stroke-width="2.4" fill="none" stroke-linecap="round"/>',
    'alert-circle': '<circle cx="12" cy="12" r="9" stroke="{color}" stroke-width="2" fill="none"/><line x1="12" x2="12" y1="8" y2="12" stroke="{color}" stroke-width="2" stroke-linecap="round"/><line x1="12" x2="12.01" y1="16" y2="16" stroke="{color}" stroke-width="2" stroke-linecap="round"/>',
    'scan': (
        '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" '
        'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<line x1="7" x2="17" y1="12" y2="12" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
    ),
    'more-horizontal': (
        '<circle cx="12" cy="12" r="1.5" fill="{color}" stroke="{color}" stroke-width="1.5"/>'
        '<circle cx="19" cy="12" r="1.5" fill="{color}" stroke="{color}" stroke-width="1.5"/>'
        '<circle cx="5" cy="12" r="1.5" fill="{color}" stroke="{color}" stroke-width="1.5"/>'
    ),
}

_ICON_CACHE: Dict[Tuple[str, str, int], QIcon] = {}


def lucide_pixmap(name: str, color: str = '#F3F4F6', size: int = 18) -> QPixmap:
    body = LUCIDE_SVGS.get(name, LUCIDE_SVGS['play']).format(color=color)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24">{body}</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))
    px = QPixmap(size * 2, size * 2)
    px.fill(QColor(0, 0, 0, 0))
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * 2, size * 2))
    painter.end()
    px.setDevicePixelRatio(2.0)
    return px


def lucide_icon(name: str, color: str = '#F3F4F6', size: int = 18) -> QIcon:
    key = (name, color, size)
    if key not in _ICON_CACHE:
        _ICON_CACHE[key] = QIcon(lucide_pixmap(name, color, size))
    return _ICON_CACHE[key]


def format_time(seconds: float) -> str:
    if seconds is None or seconds < 0 or seconds == float('inf'):
        return '0:00'
    total = int(round(seconds))
    mins = total // 60
    secs = total % 60
    return f'{mins}:{secs:02d}'


def load_ui_positions() -> dict:
    try:
        with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_ui_position(key: str, x: int, y: int) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    data = load_ui_positions()
    data[key] = {'x': int(x), 'y': int(y)}
    tmp = POSITIONS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, POSITIONS_FILE)
    except Exception:
        pass


def read_bool_config(filename: str, default: bool = True) -> bool:
    try:
        with open(os.path.join(CONFIG_DIR, filename), 'r', encoding='utf-8') as f:
            val = f.read().strip().lower()
        return val in ('yes', 'true', '1', 'on')
    except Exception:
        return default


def read_float_config(filename: str, default: float = 1.0) -> float:
    try:
        with open(os.path.join(CONFIG_DIR, filename), 'r', encoding='utf-8') as f:
            return max(0.25, min(3.0, float(f.read().strip())))
    except Exception:
        return default


def write_config(filename: str, value: str) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(os.path.join(CONFIG_DIR, filename), 'w', encoding='utf-8') as f:
            f.write(str(value).strip() + '\n')
    except Exception:
        pass


def get_player_hud_stylesheet(theme: str = 'dark') -> str:
    """Return dark or light stylesheet for FloatingPlayerWindow based on active theme."""
    if theme == 'light':
        return """
            #playerDeck {
                background-color: #FFFFFF;
                border: 1px solid rgba(0, 0, 0, 0.14);
                border-radius: 14px;
            }
            #statusDot {
                color: #10B981;
                font-size: 11px;
                padding-right: 2px;
            }
            #playerTitle {
                color: #1E293B;
                font-size: 12px;
                font-weight: 600;
            }
            #speedPill {
                background-color: #EEF2FF;
                color: #4338CA;
                border: 1px solid rgba(99, 102, 241, 0.45);
                border-radius: 6px;
                font-size: 12px;
                font-weight: 700;
                padding: 3px 9px;
                min-height: 22px;
                min-width: 52px;
            }
            #speedPill:hover {
                background-color: #E0E7FF;
                border-color: #6366F1;
                color: #312E81;
            }
            #modeTag {
                color: #64748B;
                font-size: 11px;
                font-weight: 500;
            }
            #toolsDrawer {
                background-color: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 7px;
                margin-top: 2px;
                margin-bottom: 2px;
            }
            QPushButton.utilBtn {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                padding: 0px;
            }
            QPushButton.utilBtn:hover {
                background-color: rgba(0, 0, 0, 0.06);
                border-color: rgba(0, 0, 0, 0.10);
            }
            QPushButton.utilBtn:checked {
                background-color: rgba(99, 102, 241, 0.15);
                border-color: #6366F1;
            }
            #queueBanner {
                background-color: rgba(99, 102, 241, 0.10);
                color: #4338CA;
                border-radius: 5px;
                padding: 2px 6px;
                font-size: 11px;
                font-weight: 600;
            }
            #timeLabel {
                color: #475569;
                font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
                font-size: 11px;
                font-weight: 500;
                min-width: 34px;
            }
            #seekSlider::groove:horizontal {
                height: 6px;
                background: #E2E8F0;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 3px;
            }
            #seekSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366F1, stop:1 #4F46E5);
                border-radius: 3px;
            }
            #seekSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #6366F1;
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 7px;
            }
            #seekSlider::handle:horizontal:hover {
                background: #F8FAFC;
                border-color: #4F46E5;
            }
            #stateHint {
                color: #64748B;
                font-size: 11px;
                font-weight: 500;
                min-width: 68px;
            }
            #transportSecondaryBtn {
                background-color: #F8FAFC;
                border: 1px solid rgba(0, 0, 0, 0.10);
                border-radius: 8px;
                min-width: 34px;
                max-width: 34px;
                min-height: 32px;
                max-height: 32px;
            }
            #transportSecondaryBtn:hover {
                background-color: #F1F5F9;
                border-color: rgba(0, 0, 0, 0.18);
            }
            #transportSecondaryBtn:disabled {
                opacity: 0.4;
            }
            #transportHeroBtn {
                background-color: #6366F1;
                border: 1px solid #4F46E5;
                border-radius: 10px;
                min-width: 44px;
                max-width: 44px;
                min-height: 36px;
                max-height: 36px;
            }
            #transportHeroBtn:hover {
                background-color: #4F46E5;
            }
            #transportStopBtn {
                background-color: rgba(244, 63, 94, 0.10);
                border: 1px solid rgba(244, 63, 94, 0.25);
                border-radius: 8px;
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
            }
            #transportStopBtn:hover {
                background-color: rgba(244, 63, 94, 0.20);
                border-color: #F43F5E;
            }
            #chunkBox {
                background-color: #F8FAFC;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 8px;
            }
            #chunkPhaseText {
                color: #475569;
                font-size: 11px;
                font-weight: 500;
            }
            #chunkPctText {
                color: #4338CA;
                font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
                font-size: 11px;
                font-weight: 600;
            }
            #chunkBarBg {
                background-color: #E2E8F0;
                border-radius: 2px;
            }
            #chunkBarFill {
                background-color: #6366F1;
                border-radius: 2px;
            }
            QLabel.chunkPillPending {
                background-color: #F1F5F9;
                color: #64748B;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                padding: 1px 5px;
            }
            QLabel.chunkPillProcessing {
                background-color: rgba(99, 102, 241, 0.15);
                color: #4338CA;
                border: 1px solid #6366F1;
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                padding: 1px 5px;
            }
            QLabel.chunkPillCompleted {
                background-color: rgba(16, 185, 129, 0.14);
                color: #059669;
                border: 1px solid rgba(16, 185, 129, 0.35);
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                padding: 1px 5px;
            }
            QLabel.chunkPillFailed {
                background-color: rgba(244, 63, 94, 0.12);
                color: #E11D48;
                border: 1px solid rgba(244, 63, 94, 0.35);
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                padding: 1px 5px;
            }
            #transcriptBox {
                background-color: #F8FAFC;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 8px;
            }
            #transcriptHeader {
                color: #4338CA;
                font-size: 10px;
                font-weight: 600;
            }
            #hotkeyHint {
                color: #94A3B8;
                font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
                font-size: 9px;
            }
            #transcriptScroll, #transcriptListWidget {
                background: transparent;
                border: none;
            }
            QFrame.transcriptRow {
                background-color: #F1F5F9;
                border: 1px solid rgba(0, 0, 0, 0.06);
                border-radius: 6px;
            }
            QFrame.transcriptRow:hover {
                background-color: #E2E8F0;
                border-color: rgba(0, 0, 0, 0.12);
            }
            QFrame.transcriptRowActive {
                background-color: rgba(99, 102, 241, 0.12);
                border: 1px solid #6366F1;
                border-radius: 6px;
            }
            QLabel.transcriptText {
                color: #334155;
                font-size: 11px;
            }
            QLabel.transcriptTextActive {
                color: #0F172A;
                font-size: 11px;
                font-weight: 600;
            }
            QMenu {
                background-color: #FFFFFF;
                color: #1E293B;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 5px 16px;
                border-radius: 4px;
                font-size: 11px;
                color: #1E293B;
            }
            QMenu::item:selected {
                background-color: #6366F1;
                color: #FFFFFF;
            }
        """

    # Default Dark (Studio Obsidian Slate)
    return """
        #playerDeck {
            background-color: #16181E;
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 14px;
        }
        #statusDot {
            color: #10B981;
            font-size: 11px;
            padding-right: 2px;
        }
        #playerTitle {
            color: #F3F4F6;
            font-size: 12px;
            font-weight: 600;
        }
        #speedPill {
            background-color: #1E212B;
            color: #A5B4FC;
            border: 1px solid rgba(99, 102, 241, 0.45);
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
            padding: 3px 9px;
            min-height: 22px;
            min-width: 52px;
        }
        #speedPill:hover {
            background-color: rgba(99, 102, 241, 0.28);
            border-color: #818CF8;
            color: #FFFFFF;
        }
        #modeTag {
            color: #94A3B8;
            font-size: 11px;
            font-weight: 500;
        }
        #toolsDrawer {
            background-color: rgba(30, 33, 43, 0.7);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 7px;
            margin-top: 2px;
            margin-bottom: 2px;
        }
        QPushButton.utilBtn {
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            min-width: 24px;
            max-width: 24px;
            min-height: 24px;
            max-height: 24px;
            padding: 0px;
        }
        QPushButton.utilBtn:hover {
            background-color: rgba(255, 255, 255, 0.09);
            border-color: rgba(255, 255, 255, 0.12);
        }
        QPushButton.utilBtn:checked {
            background-color: rgba(99, 102, 241, 0.25);
            border-color: #6366F1;
        }
        #queueBanner {
            background-color: rgba(99, 102, 241, 0.12);
            color: #A5B4FC;
            border-radius: 5px;
            padding: 2px 6px;
            font-size: 11px;
            font-weight: 500;
        }
        #timeLabel {
            color: #9CA3AF;
            font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
            font-size: 11px;
            font-weight: 500;
            min-width: 34px;
        }
        #seekSlider::groove:horizontal {
            height: 6px;
            background: #0E1015;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 3px;
        }
        #seekSlider::sub-page:horizontal {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366F1, stop:1 #818CF8);
            border-radius: 3px;
        }
        #seekSlider::handle:horizontal {
            background: #F9FAFB;
            border: 2px solid #6366F1;
            width: 12px;
            height: 12px;
            margin: -4px 0;
            border-radius: 7px;
        }
        #seekSlider::handle:horizontal:hover {
            background: #FFFFFF;
            border-color: #818CF8;
        }
        #stateHint {
            color: #6B7280;
            font-size: 11px;
            font-weight: 500;
            min-width: 68px;
        }
        #transportSecondaryBtn {
            background-color: #1E212B;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            min-width: 34px;
            max-width: 34px;
            min-height: 32px;
            max-height: 32px;
        }
        #transportSecondaryBtn:hover {
            background-color: #282C3A;
            border-color: rgba(255, 255, 255, 0.18);
        }
        #transportSecondaryBtn:disabled {
            opacity: 0.4;
        }
        #transportHeroBtn {
            background-color: #6366F1;
            border: 1px solid #818CF8;
            border-radius: 10px;
            min-width: 44px;
            max-width: 44px;
            min-height: 36px;
            max-height: 36px;
        }
        #transportHeroBtn:hover {
            background-color: #818CF8;
        }
        #transportStopBtn {
            background-color: rgba(244, 63, 94, 0.12);
            border: 1px solid rgba(244, 63, 94, 0.28);
            border-radius: 8px;
            min-width: 32px;
            max-width: 32px;
            min-height: 32px;
            max-height: 32px;
        }
        #transportStopBtn:hover {
            background-color: rgba(244, 63, 94, 0.24);
            border-color: #F43F5E;
        }
        #chunkBox {
            background-color: #111318;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
        }
        #chunkPhaseText {
            color: #9CA3AF;
            font-size: 11px;
            font-weight: 500;
        }
        #chunkPctText {
            color: #818CF8;
            font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
            font-size: 11px;
            font-weight: 600;
        }
        #chunkBarBg {
            background-color: #1E212B;
            border-radius: 2px;
        }
        #chunkBarFill {
            background-color: #6366F1;
            border-radius: 2px;
        }
        QLabel.chunkPillPending {
            background-color: #1C1F28;
            color: #6B7280;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
            padding: 1px 5px;
        }
        QLabel.chunkPillProcessing {
            background-color: rgba(99, 102, 241, 0.22);
            color: #A5B4FC;
            border: 1px solid #6366F1;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
            padding: 1px 5px;
        }
        QLabel.chunkPillCompleted {
            background-color: rgba(16, 185, 129, 0.16);
            color: #34D399;
            border: 1px solid rgba(16, 185, 129, 0.40);
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
            padding: 1px 5px;
        }
        QLabel.chunkPillFailed {
            background-color: rgba(244, 63, 94, 0.18);
            color: #FB7185;
            border: 1px solid rgba(244, 63, 94, 0.45);
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
            padding: 1px 5px;
        }
        #transcriptBox {
            background-color: #111318;
            border: 1px solid rgba(255, 255, 255, 0.07);
            border-radius: 8px;
        }
        #transcriptHeader {
            color: #A5B4FC;
            font-size: 10px;
            font-weight: 600;
        }
        #hotkeyHint {
            color: #6B7280;
            font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
            font-size: 9px;
        }
        #transcriptScroll, #transcriptListWidget {
            background: transparent;
            border: none;
        }
        QFrame.transcriptRow {
            background-color: #181B24;
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 6px;
        }
        QFrame.transcriptRow:hover {
            background-color: #222634;
            border-color: rgba(255, 255, 255, 0.14);
        }
        QFrame.transcriptRowActive {
            background-color: rgba(99, 102, 241, 0.18);
            border: 1px solid #6366F1;
            border-radius: 6px;
        }
        QLabel.transcriptText {
            color: #D1D5DB;
            font-size: 11px;
        }
        QLabel.transcriptTextActive {
            color: #FFFFFF;
            font-size: 11px;
            font-weight: 600;
        }
        QMenu {
            background-color: #16181E;
            color: #F3F4F6;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            padding: 4px;
        }
        QMenu::item {
            padding: 5px 16px;
            border-radius: 4px;
            font-size: 11px;
            color: #F3F4F6;
        }
        QMenu::item:selected {
            background-color: #6366F1;
            color: #FFFFFF;
        }
    """


def get_queue_stylesheet(theme: str = 'dark') -> str:
    """Return dark or light stylesheet for QueueManagerWindow based on active theme."""
    if theme == 'light':
        return """
            #queueCard {
                background-color: #FFFFFF;
                border: 1px solid rgba(0, 0, 0, 0.14);
                border-radius: 12px;
            }
            #queueHeader {
                background-color: #F8FAFC;
                border-top-left-radius: 11px;
                border-top-right-radius: 11px;
                border-bottom: 1px solid rgba(0, 0, 0, 0.08);
            }
            #queueHeaderTitle {
                color: #1E293B;
                font-size: 12px;
                font-weight: 600;
            }
            #queueCountBadge {
                background-color: rgba(99, 102, 241, 0.15);
                color: #4338CA;
                font-size: 11px;
                font-weight: 600;
                padding: 1px 7px;
                border-radius: 9px;
            }
            QPushButton.queueMiniBtn {
                background-color: #F1F5F9;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 5px;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                padding: 0px;
            }
            QPushButton.queueMiniBtn:hover {
                background-color: #E2E8F0;
                border-color: rgba(0, 0, 0, 0.15);
            }
            QPushButton.queueMiniBtn:checked, QPushButton.queueMiniBtnAccent {
                background-color: #6366F1;
                border-color: #4F46E5;
            }
            QPushButton.queueMiniBtnAccent:hover {
                background-color: #4F46E5;
            }
            #queueScroll, #queueListContainer {
                background: transparent;
                border: none;
            }
            QFrame.queueItem {
                background-color: #F8FAFC;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 8px;
            }
            QFrame.queueItem:hover {
                background-color: #F1F5F9;
                border-color: rgba(0, 0, 0, 0.16);
            }
            QFrame.queueItemPlaying {
                background-color: rgba(99, 102, 241, 0.10);
                border: 1.5px solid #6366F1;
                border-radius: 8px;
            }
            QLabel.queueBadge {
                background-color: #E2E8F0;
                color: #475569;
                font-size: 11px;
                font-weight: 600;
                border-radius: 12px;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
            }
            QLabel.queueBadgePlaying {
                background-color: #6366F1;
                color: #FFFFFF;
                border-radius: 12px;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
            }
            QLabel.queueItemTitle {
                color: #1E293B;
                font-size: 12px;
            }
            QLabel.queueItemTitlePlaying {
                color: #4338CA;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel.queueEmptyTitle {
                color: #1E293B;
                font-weight: 600;
                font-size: 12px;
            }
            QLabel.queueEmptySub {
                color: #64748B;
                font-size: 11px;
            }
        """

    # Default Dark
    return """
        #queueCard {
            background-color: #16181E;
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 12px;
        }
        #queueHeader {
            background-color: #1E212B;
            border-top-left-radius: 11px;
            border-top-right-radius: 11px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.07);
        }
        #queueHeaderTitle {
            color: #F3F4F6;
            font-size: 12px;
            font-weight: 600;
        }
        #queueCountBadge {
            background-color: rgba(99, 102, 241, 0.20);
            color: #A5B4FC;
            font-size: 11px;
            font-weight: 600;
            padding: 1px 7px;
            border-radius: 9px;
        }
        QPushButton.queueMiniBtn {
            background-color: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 5px;
            min-width: 24px;
            max-width: 24px;
            min-height: 24px;
            max-height: 24px;
            padding: 0px;
        }
        QPushButton.queueMiniBtn:hover {
            background-color: rgba(255, 255, 255, 0.12);
            border-color: rgba(255, 255, 255, 0.18);
        }
        QPushButton.queueMiniBtn:checked, QPushButton.queueMiniBtnAccent {
            background-color: #6366F1;
            border-color: #818CF8;
        }
        QPushButton.queueMiniBtnAccent:hover {
            background-color: #818CF8;
        }
        #queueScroll, #queueListContainer {
            background: transparent;
            border: none;
        }
        QFrame.queueItem {
            background-color: #1C1F28;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
        }
        QFrame.queueItem:hover {
            background-color: #232733;
            border-color: rgba(255, 255, 255, 0.14);
        }
        QFrame.queueItemPlaying {
            background-color: rgba(99, 102, 241, 0.14);
            border: 1.5px solid #6366F1;
            border-radius: 8px;
        }
        QLabel.queueBadge {
            background-color: #252936;
            color: #9CA3AF;
            font-size: 11px;
            font-weight: 600;
            border-radius: 12px;
            min-width: 24px;
            max-width: 24px;
            min-height: 24px;
            max-height: 24px;
        }
        QLabel.queueBadgePlaying {
            background-color: #6366F1;
            color: #FFFFFF;
            border-radius: 12px;
            min-width: 24px;
            max-width: 24px;
            min-height: 24px;
            max-height: 24px;
        }
        QLabel.queueItemTitle {
            color: #E5E7EB;
            font-size: 12px;
        }
        QLabel.queueItemTitlePlaying {
            color: #A5B4FC;
            font-size: 12px;
            font-weight: 600;
        }
        QLabel.queueEmptyTitle {
            color: #F3F4F6;
            font-weight: 600;
            font-size: 12px;
        }
        QLabel.queueEmptySub {
            color: #9CA3AF;
            font-size: 11px;
        }
    """


# ── Direct mpv IPC & Multi-Segment Timeline Controller ────────────────────────

class MPVTimelineController:
    """
    Manages communication with mpv via Unix domain socket and computes a unified
    timeline across all chunks in /tmp/speak-aloud-work.
    """

    def __init__(self):
        self.session_id: str = ''
        self.title: str = 'Speak a Loud'
        self.total_chunks: int = 0
        self.chunks_meta: List[dict] = []
        self.chunk_durations: List[float] = []
        self.chunk_statuses: List[str] = []
        self.active_segment_index: int = 0
        self.current_time: float = 0.0
        self.total_duration: float = 0.0
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.is_loading: bool = False
        self.is_at_end: bool = False
        self.speed: float = 1.0
        self.had_active_session: bool = False

    def mpv_command(self, cmd: list, timeout: float = 0.08) -> Optional[dict]:
        if not os.path.exists(MPV_SOCKET):
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(MPV_SOCKET)
                payload = json.dumps({'command': cmd}) + '\n'
                s.sendall(payload.encode('utf-8'))
                buf = b''
                while b'\n' not in buf:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                for line in buf.decode('utf-8', errors='ignore').splitlines():
                    line = line.strip()
                    if line:
                        msg = json.loads(line)
                        if 'error' in msg:
                            return msg
        except Exception:
            return None
        return None

    def mpv_get(self, prop: str, default=None):
        res = self.mpv_command(['get_property', prop])
        if res and res.get('error') == 'success':
            return res.get('data', default)
        return default

    def mpv_set(self, prop: str, value) -> bool:
        res = self.mpv_command(['set_property', prop, value])
        return bool(res and res.get('error') == 'success')

    def refresh(self, daemon_state: str) -> dict:
        """Poll workdir manifest, segment statuses, and mpv IPC for live state."""
        manifest_path = os.path.join(WORK_DIR, 'manifest.json')
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    m = json.load(f)
                new_sid = m.get('session_id', '')
                if new_sid != self.session_id:
                    self.session_id = new_sid
                    self.title = m.get('title', 'Speak a Loud')
                    self.chunks_meta = m.get('chunks', [])
                    self.total_chunks = len(self.chunks_meta)
                    self.chunk_durations = [float(c.get('est_duration', 2.0)) for c in self.chunks_meta]
                    self.chunk_statuses = ['pending'] * self.total_chunks
                    self.current_time = 0.0
                    self.is_at_end = False
                    self.had_active_session = True
                    self.speed = 1.0
            except Exception:
                pass

        # Update per-chunk status and exact MP3 durations (CBR 48kbps = 6000 bytes/s)
        completed_count = 0
        failed_count = 0
        for i in range(self.total_chunks):
            pad = f'{i:03d}'
            st_file = os.path.join(WORK_DIR, f'seg_{pad}.status')
            mp3_file = os.path.join(WORK_DIR, f'seg_{pad}.mp3')
            st = 'pending'
            if os.path.exists(st_file):
                try:
                    with open(st_file, 'r', encoding='utf-8') as f:
                        st = f.read().strip() or 'pending'
                except Exception:
                    pass
            if os.path.exists(mp3_file):
                try:
                    sz = os.path.getsize(mp3_file)
                    if sz > 256:
                        self.chunk_durations[i] = max(0.25, sz / 6000.0)
                        st = 'completed'
                except Exception:
                    pass
            if i < len(self.chunk_statuses):
                self.chunk_statuses[i] = st
            if st == 'completed':
                completed_count += 1
            elif st == 'failed':
                failed_count += 1

        self.total_duration = sum(self.chunk_durations) if self.chunk_durations else 0.0

        # Query mpv live state if socket is active
        socket_alive = os.path.exists(MPV_SOCKET)
        mpv_paused = None
        if socket_alive:
            mpv_paused = self.mpv_get('pause', None)

        if mpv_paused is not None:
            self.is_loading = False
            self.is_paused = bool(mpv_paused)
            self.is_playing = not self.is_paused
            self.is_at_end = False

            pl_pos = self.mpv_get('playlist-pos', 0)
            t_pos = self.mpv_get('time-pos', 0.0)
            cur_speed = self.mpv_get('speed', None)
            if cur_speed is not None:
                try:
                    self.speed = round(float(cur_speed), 2)
                except Exception:
                    pass

            # Note: if silence files are interleaved in mpv playlist, map playlist filename to seg_XXX
            filename = self.mpv_get('filename', '')
            seg_idx = 0
            if isinstance(filename, str) and filename.startswith('seg_') and filename.endswith('.mp3'):
                try:
                    seg_idx = int(filename[4:7])
                except Exception:
                    seg_idx = int(pl_pos or 0)
            elif isinstance(pl_pos, int) and pl_pos >= 0:
                seg_idx = min(pl_pos, max(0, self.total_chunks - 1))

            prefix_dur = sum(self.chunk_durations[:seg_idx]) if seg_idx <= len(self.chunk_durations) else 0.0
            local_t = float(t_pos or 0.0)
            self.active_segment_index = seg_idx
            self.current_time = min(self.total_duration, prefix_dur + local_t)
            self.had_active_session = True
        else:
            if daemon_state in ('GENERATING', 'RETRYING'):
                self.is_loading = True
                self.is_playing = False
                self.is_paused = False
                self.is_at_end = False
                self.active_segment_index = 0
                self.current_time = 0.0
                self.had_active_session = True
            elif daemon_state == 'PLAYING':
                self.is_loading = completed_count == 0
                self.is_playing = completed_count > 0
                self.is_paused = False
            elif daemon_state == 'PAUSED':
                self.is_loading = False
                self.is_playing = False
                self.is_paused = True
            else:
                # IDLE
                self.is_loading = False
                self.is_playing = False
                self.is_paused = True
                if self.had_active_session and self.total_duration > 0 and completed_count > 0:
                    self.is_at_end = True
                    self.active_segment_index = max(0, self.total_chunks - 1)
                    self.current_time = self.total_duration

        overall_pct = 0
        if self.total_chunks > 0:
            proc_count = sum(1 for s in self.chunk_statuses if s == 'processing')
            overall_pct = int(min(100, round(((completed_count + 0.35 * proc_count) / self.total_chunks) * 100)))

        if self.total_chunks == 0:
            phase = 'splitting'
        elif completed_count + failed_count < self.total_chunks and daemon_state in ('GENERATING', 'RETRYING', 'PLAYING'):
            phase = 'generating'
        else:
            phase = 'completed'

        # Compute accurate language-aware speaking speed
        active_lang = 'en'
        active_rate_str = '+0%'
        active_rate_mult = 1.0
        active_idx = self.active_segment_index
        if 0 <= active_idx < len(self.chunks_meta):
            cm = self.chunks_meta[active_idx]
            active_lang = cm.get('lang', 'en')
            active_rate_str = cm.get('rate', '')
            active_rate_mult = float(cm.get('rate_multiplier', 0.0) or 0.0)

        if not active_rate_str or active_rate_mult <= 0.0:
            rate_file = os.path.join(WORK_DIR, f'seg_{active_idx:03d}.rate')
            if os.path.isfile(rate_file):
                try:
                    with open(rate_file, 'r', encoding='utf-8') as rf:
                        active_rate_str = rf.read().strip()
                except Exception:
                    pass
            if not active_rate_str:
                try:
                    from tts_text_processor import TTSSettingsStore
                    st = TTSSettingsStore().load()
                    active_rate_str = st.get(f'rate_{active_lang}', st.get('rate_en', '+0%'))
                except Exception:
                    active_rate_str = '+0%'
            active_rate_mult = parse_rate_multiplier(active_rate_str)

        effective_speed = round(active_rate_mult * self.speed, 2)
        self.effective_speed = effective_speed
        self.active_lang = active_lang
        self.active_rate = active_rate_str
        self.active_rate_mult = active_rate_mult

        return {
            'title': self.title,
            'current_time': self.current_time,
            'duration': self.total_duration,
            'is_playing': self.is_playing,
            'is_paused': self.is_paused,
            'is_loading': self.is_loading,
            'is_at_end': self.is_at_end,
            'speed': self.speed,
            'effective_speed': effective_speed,
            'active_lang': active_lang,
            'active_rate': active_rate_str,
            'rate_multiplier': active_rate_mult,
            'total_chunks': self.total_chunks,
            'completed_chunks': completed_count,
            'failed_chunks': failed_count,
            'chunk_statuses': list(self.chunk_statuses),
            'chunks_meta': list(self.chunks_meta),
            'active_segment_index': self.active_segment_index,
            'overall_progress': overall_pct,
            'phase': phase,
        }

    def jump_to_segment(self, seg_idx: int) -> None:
        if not self.chunk_durations:
            return
        seg_idx = max(0, min(seg_idx, len(self.chunk_durations) - 1))
        self.active_segment_index = seg_idx
        target_time = sum(self.chunk_durations[:seg_idx])
        self.seek_global(target_time)

    def _target_segment_and_offset(self, target_time: float) -> Tuple[int, float]:
        target_time = max(0.0, min(target_time, self.total_duration))
        acc = 0.0
        for idx, dur in enumerate(self.chunk_durations):
            if acc + dur >= target_time or idx == len(self.chunk_durations) - 1:
                return idx, max(0.0, target_time - acc)
            acc += dur
        return 0, 0.0

    def seek_global(self, target_time: float) -> None:
        if self.total_duration <= 0:
            return
        target_time = max(0.0, min(target_time, self.total_duration))
        target_seg, local_offset = self._target_segment_and_offset(target_time)

        if os.path.exists(MPV_SOCKET) and self.mpv_get('pause', None) is not None:
            # Find playlist index matching seg_{target_seg:03d}.mp3
            pl_count = self.mpv_get('playlist-count', 1) or 1
            target_pl_idx = target_seg
            target_name = f'seg_{target_seg:03d}.mp3'
            for p_i in range(int(pl_count)):
                fname = self.mpv_get(f'playlist/{p_i}/filename', '')
                if isinstance(fname, str) and fname.endswith(target_name):
                    target_pl_idx = p_i
                    break

            cur_pl = self.mpv_get('playlist-pos', 0)
            if cur_pl != target_pl_idx:
                self.mpv_set('playlist-pos', target_pl_idx)
                time.sleep(0.03)
            self.mpv_command(['seek', round(local_offset, 2), 'absolute'])
            self.current_time = target_time
            self.is_at_end = False
        else:
            # mpv already finished — relaunch from existing workdir segments at target_time!
            self._relaunch_from_workdir(start_seg=target_seg, start_offset=local_offset)

    def _relaunch_from_workdir(self, start_seg: int = 0, start_offset: float = 0.0) -> None:
        seg_files = sorted(
            os.path.join(WORK_DIR, f)
            for f in os.listdir(WORK_DIR)
            if f.startswith('seg_') and f.endswith('.mp3')
        ) if os.path.isdir(WORK_DIR) else []
        if not seg_files:
            return

        start_seg = max(0, min(start_seg, len(seg_files) - 1))
        try:
            if os.path.exists(MPV_SOCKET):
                os.unlink(MPV_SOCKET)
        except OSError:
            pass

        try:
            with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                f.write('PLAYING')
        except OSError:
            pass

        cmd = [
            'mpv',
            *seg_files,
            f'--playlist-start={start_seg}',
            f'--start=+{start_offset:.2f}',
            '--no-terminal',
            '--gapless-audio=yes',
            '--audio-buffer=0.2',
            f'--speed={self.speed}',
            f'--input-ipc-server={MPV_SOCKET}',
        ]
        env = os.environ.copy()
        env['PATH'] = f"{os.path.join(SCRIPT_DIR, 'bin')}:{env.get('PATH', '')}"

        def _watch():
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            proc.wait()
            try:
                if os.path.exists(MPV_SOCKET):
                    os.unlink(MPV_SOCKET)
            except OSError:
                pass
            # Only set IDLE if speak.sh isn't running
            if subprocess.run(['pgrep', '-f', 'speak.sh'], capture_output=True).returncode != 0:
                try:
                    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                        f.write('IDLE')
                except OSError:
                    pass

        import threading
        threading.Thread(target=_watch, daemon=True).start()
        self.is_at_end = False
        self.is_paused = False
        self.is_playing = True

    def toggle_pause(self) -> None:
        if self.is_at_end or not os.path.exists(MPV_SOCKET):
            # If paused/finished without active mpv, relaunch from current_time (or 0 if at end)
            start_t = 0.0 if self.is_at_end else self.current_time
            seg, off = self._target_segment_and_offset(start_t)
            self._relaunch_from_workdir(start_seg=seg, start_offset=off)
            return
        subprocess.Popen(
            ['bash', os.path.join(SCRIPT_DIR, 'speak-pause.sh')],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def replay(self) -> None:
        self.current_time = 0.0
        self.is_at_end = False
        if os.path.exists(MPV_SOCKET) and self.mpv_get('pause', None) is not None:
            self.mpv_set('playlist-pos', 0)
            time.sleep(0.03)
            self.mpv_command(['seek', 0, 'absolute'])
            self.mpv_set('pause', False)
            try:
                with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                    f.write('PLAYING')
            except OSError:
                pass
        else:
            self._relaunch_from_workdir(start_seg=0, start_offset=0.0)

    def jump_backward(self, delta: float = 10.0) -> None:
        self.seek_global(max(0.0, self.current_time - delta))

    def jump_forward(self, delta: float = 10.0) -> None:
        if self.total_duration > 0:
            self.seek_global(min(self.total_duration - 0.2, self.current_time + delta))

    def stop(self) -> None:
        self.had_active_session = False
        self.is_at_end = False
        self.is_playing = False
        self.is_paused = True
        self.current_time = 0.0
        subprocess.Popen(
            ['bash', os.path.join(SCRIPT_DIR, 'speak-stop.sh')],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def set_speed(self, new_speed: float) -> None:
        self.speed = round(max(0.5, min(2.5, new_speed)), 2)
        write_config('global_speed', f'{self.speed}')
        if os.path.exists(MPV_SOCKET):
            self.mpv_set('speed', self.speed)

    def export_mp3(self, dest_path: str) -> bool:
        full_mp3 = os.path.join(WORK_DIR, 'full.mp3')
        if os.path.exists(full_mp3) and os.path.getsize(full_mp3) > 0:
            shutil.copyfile(full_mp3, dest_path)
            return True
        seg_files = sorted(
            os.path.join(WORK_DIR, f)
            for f in os.listdir(WORK_DIR)
            if f.startswith('seg_') and f.endswith('.mp3')
        ) if os.path.isdir(WORK_DIR) else []
        if not seg_files:
            return False
        with open(dest_path, 'wb') as out_f:
            for sf in seg_files:
                with open(sf, 'rb') as in_f:
                    shutil.copyfileobj(in_f, out_f)
        return True


# ── Custom Click-to-Seek Slider ───────────────────────────────────────────────

class ClickJumpSlider(QSlider):
    """QSlider that supports direct click-anywhere-to-seek, smooth dragging, and sentence hover preview."""
    seekRequested = pyqtSignal(float)
    hoverPreview = pyqtSignal(float, QPoint)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, 1000)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMouseTracking(True)
        self._dragging = False

    def is_dragging(self) -> bool:
        return self._dragging

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            val = self._pixel_pos_to_value(event.pos())
            self.setValue(val)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        val = self._pixel_pos_to_value(event.pos())
        ratio = max(0.0, min(1.0, val / 1000.0))
        pt = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
        self.hoverPreview.emit(ratio, pt)
        if self._dragging:
            self.setValue(val)
            event.accept()
            return
        try:
            super().mouseMoveEvent(event)
        except TypeError:
            pass

    def leaveEvent(self, event):
        from PyQt6.QtWidgets import QToolTip
        QToolTip.hideText()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            val = self._pixel_pos_to_value(event.pos())
            self.setValue(val)
            self.seekRequested.emit(val / 1000.0)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _pixel_pos_to_value(self, pos: QPoint) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        gr = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            opt,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        sr = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            opt,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )
        slider_len = sr.width()
        slider_min = gr.x() + slider_len // 2
        slider_max = gr.right() - slider_len // 2 + 1
        span = max(1, slider_max - slider_min)
        rel = max(0.0, min(1.0, (pos.x() - slider_min) / float(span)))
        return int(round(rel * (self.maximum() - self.minimum()) + self.minimum()))


# ── Playback Queue Manager Window (inspired by QueueManagerUI.tsx) ────────────

class QueueManagerWindow(QWidget):
    """
    Draggable floating Playback Queue manager matching obsidian-edge-tts's QueueManagerUI.
    """
    playItemRequested = pyqtSignal(int)
    playQueueRequested = pyqtSignal()

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(368)
        self.setMinimumHeight(230)
        self.setMaximumHeight(420)

        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self.auto_scroll = True
        self.loop_enabled = False
        self.queue_items: List[dict] = []
        self.current_index: int = -1
        self.is_playing_from_queue: bool = False
        self._last_mtime: float = 0.0
        self._current_theme: str = ''

        self._build_ui()
        self._restore_position()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self.card = QFrame(self)
        self.card.setObjectName('queueCard')
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # Header (Draggable)
        self.header = QFrame(self.card)
        self.header.setObjectName('queueHeader')
        self.header.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(12, 8, 8, 8)
        h_layout.setSpacing(6)

        self.header_icon_lbl = QLabel()
        self.header_icon_lbl.setPixmap(lucide_pixmap('list-music', '#4F46E5' if getattr(self, '_current_theme', '') == 'light' else '#818CF8', 16))
        h_layout.addWidget(self.header_icon_lbl)

        title_lbl = QLabel('Playback Queue')
        title_lbl.setObjectName('queueHeaderTitle')
        h_layout.addWidget(title_lbl)

        self.count_badge = QLabel('0')
        self.count_badge.setObjectName('queueCountBadge')
        h_layout.addWidget(self.count_badge)
        h_layout.addStretch(1)

        # Header Mini Buttons
        self.btn_add = self._make_mini_btn('plus', 'Add current selection to queue')
        self.btn_add.clicked.connect(self.enqueue_current_selection)
        h_layout.addWidget(self.btn_add)

        self.btn_play_all = self._make_mini_btn('play', 'Play Queue from Beginning', accent=True)
        self.btn_play_all.clicked.connect(lambda: self.playQueueRequested.emit())
        h_layout.addWidget(self.btn_play_all)

        self.btn_loop = self._make_mini_btn('repeat', 'Toggle Loop Queue')
        self.btn_loop.setCheckable(True)
        self.btn_loop.clicked.connect(self._toggle_loop)
        h_layout.addWidget(self.btn_loop)

        self.btn_clear = self._make_mini_btn('trash-2', 'Clear Queue')
        self.btn_clear.clicked.connect(self.clear_queue)
        h_layout.addWidget(self.btn_clear)

        self.btn_close = self._make_mini_btn('x', 'Close Queue')
        self.btn_close.clicked.connect(self.hide)
        h_layout.addWidget(self.btn_close)

        card_layout.addWidget(self.header)

        # Scrollable Content Area
        self.scroll = QScrollArea(self.card)
        self.scroll.setObjectName('queueScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.list_container = QWidget()
        self.list_container.setObjectName('queueListContainer')
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(10, 10, 10, 10)
        self.list_layout.setSpacing(6)
        self.scroll.setWidget(self.list_container)

        card_layout.addWidget(self.scroll, 1)
        outer.addWidget(self.card)

        self.apply_theme()
        self.load_from_disk(force=True)

    def apply_theme(self, theme: Optional[str] = None):
        target = theme or get_effective_theme(QApplication.instance())
        if target == getattr(self, '_current_theme', None) and getattr(self, '_theme_applied', False):
            return
        self._current_theme = target
        self._theme_applied = True
        self.setStyleSheet(get_queue_stylesheet(target))
        for btn in (
            getattr(self, 'btn_add', None),
            getattr(self, 'btn_play_all', None),
            getattr(self, 'btn_loop', None),
            getattr(self, 'btn_clear', None),
            getattr(self, 'btn_close', None),
        ):
            if btn is not None:
                iname = btn.property('iconName')
                is_acc = btn.property('isAccent')
                if iname:
                    color = '#FFFFFF' if is_acc else ('#475569' if target == 'light' else '#D1D5DB')
                    btn.setIcon(lucide_icon(iname, color, 13))

        if hasattr(self, 'header_icon_lbl'):
            self.header_icon_lbl.setPixmap(lucide_pixmap('list-music', '#4F46E5' if target == 'light' else '#818CF8', 16))
        self._rebuild_list()

    def _make_mini_btn(self, icon_name: str, tooltip: str, accent: bool = False) -> QPushButton:
        btn = QPushButton()
        btn.setProperty('class', 'queueMiniBtn queueMiniBtnAccent' if accent else 'queueMiniBtn')
        btn.setProperty('iconName', icon_name)
        btn.setProperty('isAccent', accent)
        icon_color = '#FFFFFF' if accent else ('#475569' if getattr(self, '_current_theme', '') == 'light' else '#D1D5DB')
        btn.setIcon(lucide_icon(icon_name, icon_color, 13))
        btn.setIconSize(QSize(13, 13))
        btn.setToolTip(tooltip)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        return btn

    def _restore_position(self):
        pos = load_ui_positions().get('queue_window')
        primary = QApplication.primaryScreen()
        avail = primary.availableGeometry() if primary else QRect(0, 0, 1920, 1080)
        target_x = max(40, int(avail.right()) - 380)
        target_y = max(180, int(avail.bottom()) - 520)

        if pos and isinstance(pos.get('x'), int) and isinstance(pos.get('y'), int):
            px, py = pos['x'], pos['y']
            target_pt = QPoint(px, py)
            screen = QApplication.screenAt(target_pt) or primary
            if screen is not None:
                s_avail = screen.availableGeometry()
                target_x = max(int(s_avail.left()) + 10, min(px, int(s_avail.right()) - self.width() - 10))
                target_y = max(int(s_avail.top()) + 10, min(py, int(s_avail.bottom()) - 260))
        self.move(target_x, target_y)

    def load_from_disk(self, force: bool = False) -> bool:
        try:
            mtime = os.path.getmtime(QUEUE_FILE) if os.path.exists(QUEUE_FILE) else 0.0
        except OSError:
            mtime = 0.0
        if not force and mtime == self._last_mtime:
            return False
        self._last_mtime = mtime
        try:
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.queue_items = data.get('items', [])
            self.loop_enabled = bool(data.get('loop', False))
            self.btn_loop.setChecked(self.loop_enabled)
        except Exception:
            self.queue_items = []
        self._rebuild_list()
        return True

    def save_to_disk(self):
        data = {
            'items': self.queue_items,
            'current_index': self.current_index,
            'is_playing': self.is_playing_from_queue,
            'loop': self.loop_enabled,
        }
        tmp = QUEUE_FILE + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, QUEUE_FILE)
            self._last_mtime = os.path.getmtime(QUEUE_FILE)
        except Exception:
            pass

    def _rebuild_list(self):
        while self.list_layout.count():
            child = self.list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self.count_badge.setText(str(len(self.queue_items)))
        has_items = len(self.queue_items) > 0
        self.btn_play_all.setVisible(has_items)
        self.btn_clear.setVisible(has_items)

        if not has_items:
            empty = QWidget()
            el = QVBoxLayout(empty)
            el.setContentsMargins(12, 18, 12, 18)
            el.setSpacing(6)
            ic = QLabel()
            is_light = getattr(self, '_current_theme', '') == 'light'
            ic.setPixmap(lucide_pixmap('list-music', '#94A3B8' if is_light else '#4B5563', 32))
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(ic)
            t1 = QLabel('Queue is empty')
            t1.setProperty('class', 'queueEmptyTitle')
            t1.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(t1)
            t2 = QLabel('Highlight text anywhere and click +\nor run: speak.sh --queue')
            t2.setProperty('class', 'queueEmptySub')
            t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(t2)
            self.list_layout.addWidget(empty)
            return

        for idx, item in enumerate(self.queue_items):
            playing_this = self.is_playing_from_queue and idx == self.current_index
            row = QFrame()
            row.setProperty('class', 'queueItemPlaying' if playing_this else 'queueItem')
            row.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 7, 8, 7)
            rl.setSpacing(8)

            badge = QLabel()
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setProperty('class', 'queueBadgePlaying' if playing_this else 'queueBadge')
            if playing_this:
                badge.setPixmap(lucide_pixmap('volume-2', '#FFFFFF', 13))
            else:
                badge.setText(str(idx + 1))
            rl.addWidget(badge)

            title_text = item.get('title') or 'Untitled Selection'
            display_title = title_text[:22] + '…' if len(title_text) > 23 else title_text
            lbl = QLabel(display_title)
            lbl.setProperty('class', 'queueItemTitlePlaying' if playing_this else 'queueItemTitle')
            lbl.setToolTip(item.get('text', '')[:300])
            rl.addWidget(lbl, 1)

            if idx > 0:
                b_up = self._make_mini_btn('chevron-up', 'Move Up')
                b_up.clicked.connect(lambda _, i=idx: self.move_item(i, i - 1))
                rl.addWidget(b_up)
            if idx < len(self.queue_items) - 1:
                b_dn = self._make_mini_btn('chevron-down', 'Move Down')
                b_dn.clicked.connect(lambda _, i=idx: self.move_item(i, i + 1))
                rl.addWidget(b_dn)

            b_rm = self._make_mini_btn('x', 'Remove from Queue')
            b_rm.clicked.connect(lambda _, i=idx: self.remove_item(i))
            rl.addWidget(b_rm)

            row.mousePressEvent = lambda e, i=idx: (
                self.playItemRequested.emit(i) if e.button() == Qt.MouseButton.LeftButton else None
            )
            self.list_layout.addWidget(row)

        self.list_layout.addStretch(1)

    def _toggle_loop(self):
        self.loop_enabled = self.btn_loop.isChecked()
        self.save_to_disk()

    def enqueue_current_selection(self):
        subprocess.Popen(
            ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--queue'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def move_item(self, from_idx: int, to_idx: int):
        if 0 <= from_idx < len(self.queue_items) and 0 <= to_idx < len(self.queue_items):
            item = self.queue_items.pop(from_idx)
            self.queue_items.insert(to_idx, item)
            if self.current_index == from_idx:
                self.current_index = to_idx
            elif from_idx < self.current_index <= to_idx:
                self.current_index -= 1
            elif to_idx <= self.current_index < from_idx:
                self.current_index += 1
            self.save_to_disk()
            self._rebuild_list()

    def remove_item(self, idx: int):
        if 0 <= idx < len(self.queue_items):
            self.queue_items.pop(idx)
            if idx < self.current_index:
                self.current_index -= 1
            elif idx == self.current_index:
                self.is_playing_from_queue = False
                self.current_index = -1
            self.save_to_disk()
            self._rebuild_list()

    def clear_queue(self):
        self.queue_items.clear()
        self.current_index = -1
        self.is_playing_from_queue = False
        self.save_to_disk()
        self._rebuild_list()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.header.underMouse():
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.header.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.header.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            save_ui_position('queue_window', self.x(), self.y())
            event.accept()


# ── Floating On-Screen Player HUD (FloatingPlayerUI + ChunkedProgressUI) ──────

class FloatingPlayerWindow(QWidget):
    """
    Always-on-top, frameless, draggable floating TTS player HUD inspired by
    obsidian-edge-tts's FloatingPlayerUI.tsx and ChunkedProgressUI.tsx.
    """

    SPEED_STEPS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]

    def __init__(self, controller: MPVTimelineController, queue_window: QueueManagerWindow):
        super().__init__(None)
        self.controller = controller
        self.queue_window = queue_window
        self.user_dismissed_session: str = ''
        self._spin_angle: int = 0
        self._chunks_completed_at: float = 0.0
        self._transcript_session_id: str = ''
        self._transcript_active_idx: int = -1
        self._transcript_rows: List[QFrame] = []
        self._segment_words: Dict[int, List[Dict[str, Any]]] = {}
        self._current_theme: str = ''
        self.manually_opened: bool = False
        self.sleep_timer_seconds: int = 0
        self.sleep_timer_mode: int = 0
        self._last_sleep_tick: float = time.time()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(384)

        self._dragging = False
        self._drag_offset = QPoint(0, 0)

        self._build_ui()
        self._restore_position()

        # Connect Queue signals
        self.queue_window.playItemRequested.connect(self._play_queue_item)
        self.queue_window.playQueueRequested.connect(lambda: self._play_queue_item(0))

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        self.deck = QFrame(self)
        self.deck.setObjectName('playerDeck')
        deck_layout = QVBoxLayout(self.deck)
        deck_layout.setContentsMargins(14, 11, 14, 12)
        deck_layout.setSpacing(8)

        # ── Row 1: Header Bar (Prominent Speed + Clean Tag + More Tools + Close) ──
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        # Internal labels kept for backward compatibility and text referencing
        self.status_dot = QLabel('')
        self.title_lbl = QLabel('Speak a Loud')

        # Speed Pill Button (0.75x .. 2.0x) - Prominently displayed on the left
        self.btn_speed = QPushButton(f'{self.controller.speed:g}x')
        self.btn_speed.setObjectName('speedPill')
        self.btn_speed.setToolTip('Playback Speed (click to cycle, scroll to adjust, right-click for menu)')
        self.btn_speed.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_speed.clicked.connect(self._cycle_speed)
        self.btn_speed.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_speed.customContextMenuRequested.connect(self._show_speed_menu)
        self.btn_speed.wheelEvent = self._on_speed_wheel
        header_row.addWidget(self.btn_speed)

        self.lbl_mode_tag = QLabel('Speech')
        self.lbl_mode_tag.setObjectName('modeTag')
        header_row.addWidget(self.lbl_mode_tag)

        header_row.addStretch(1)

        # More Tools expand/collapse button (•••)
        self.btn_more = self._make_util_btn('more-horizontal', 'More Tools (Q, O, B, A, Z, T) • Hotkey: M')
        self.btn_more.setCheckable(True)
        self.btn_more.clicked.connect(self._toggle_tools_drawer)
        header_row.addWidget(self.btn_more)

        # Close Floating Player (x)
        self.btn_close = self._make_util_btn('x', 'Hide Floating Player')
        self.btn_close.clicked.connect(self._on_close_clicked)
        header_row.addWidget(self.btn_close)

        deck_layout.addLayout(header_row)

        # ── Expandable More Tools Drawer (collapsed by default) ───────────────
        self.tools_box = QFrame(self.deck)
        self.tools_box.setObjectName('toolsDrawer')
        self.tools_layout = QHBoxLayout(self.tools_box)
        self.tools_layout.setContentsMargins(6, 4, 6, 4)
        self.tools_layout.setSpacing(6)

        # Add Selection to Queue (+)
        self.btn_enqueue = self._make_util_btn('plus', 'Add highlighted selection to Queue')
        self.btn_enqueue.clicked.connect(self.queue_window.enqueue_current_selection)
        self.tools_layout.addWidget(self.btn_enqueue)

        # Toggle Live Interactive Transcript (scroll-text)
        self.btn_transcript = self._make_util_btn('scroll-text', 'Show / Hide Live Transcript (Hotkey: T)')
        self.btn_transcript.setCheckable(True)
        self.btn_transcript.clicked.connect(self._toggle_transcript)
        self.tools_layout.addWidget(self.btn_transcript)

        # Toggle Queue Window (list-music)
        self.btn_queue = self._make_util_btn('list-music', 'Show / Hide Playback Queue (Hotkey: Q)')
        self.btn_queue.setCheckable(True)
        self.btn_queue.clicked.connect(self._toggle_queue_window)
        self.tools_layout.addWidget(self.btn_queue)

        # OCR Screen Region Speak (scan)
        self.btn_ocr = self._make_util_btn('scan', 'Read Screen Region via OCR (Hotkey: O)')
        self.btn_ocr.clicked.connect(self._on_ocr_clicked)
        self.tools_layout.addWidget(self.btn_ocr)

        # Save Audio as MP3 (download)
        self.btn_save = self._make_util_btn('download', 'Save spoken audio as MP3')
        self.btn_save.clicked.connect(self._on_save_mp3)
        self.tools_layout.addWidget(self.btn_save)

        # Bookmark Passage (bookmark)
        self.btn_bookmark = self._make_util_btn('bookmark', 'Bookmark current passage & timestamp (Hotkey: B)')
        self.btn_bookmark.clicked.connect(self._on_bookmark_clicked)
        self.tools_layout.addWidget(self.btn_bookmark)

        # Export to Anki Flashcard (layers)
        self.btn_anki = self._make_util_btn('layers', 'Export passage to Anki Flashcard (Hotkey: A)')
        self.btn_anki.clicked.connect(self._on_anki_clicked)
        self.tools_layout.addWidget(self.btn_anki)

        # Sleep / Pomodoro Timer (moon)
        self.btn_sleep = self._make_util_btn('moon', 'Sleep Timer: Off (Click / Z to cycle: 15m, 30m, 45m, 60m, End)')
        self.btn_sleep.setCheckable(True)
        self.btn_sleep.clicked.connect(self._cycle_sleep_timer)
        self.btn_sleep.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_sleep.customContextMenuRequested.connect(self._show_sleep_context_menu)
        self.tools_layout.addWidget(self.btn_sleep)

        # Open Settings (sliders)
        self.btn_settings = self._make_util_btn('sliders', 'Open TTS Settings')
        self.btn_settings.clicked.connect(self._on_open_settings)
        self.tools_layout.addWidget(self.btn_settings)

        self.tools_box.setVisible(False)
        deck_layout.addWidget(self.tools_box)

        # ── Row 2: Queue Context Banner (only visible when playing from Queue) ─
        self.queue_banner = QLabel('')
        self.queue_banner.setObjectName('queueBanner')
        self.queue_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.queue_banner.setVisible(False)
        deck_layout.addWidget(self.queue_banner)

        # ── Row 3: Timeline Scrubber (Current Time | Seek Slider | Duration) ──
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 2, 0, 2)
        progress_row.setSpacing(8)

        self.lbl_current = QLabel('0:00')
        self.lbl_current.setObjectName('timeLabel')
        self.lbl_current.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        progress_row.addWidget(self.lbl_current)

        self.slider = ClickJumpSlider(self.deck)
        self.slider.setObjectName('seekSlider')
        self.slider.valueChanged.connect(self._on_slider_moved)
        self.slider.seekRequested.connect(self._on_seek_requested)
        self.slider.hoverPreview.connect(self._on_slider_hover_preview)
        progress_row.addWidget(self.slider, 1)

        self.lbl_duration = QLabel('0:00')
        self.lbl_duration.setObjectName('timeLabel')
        self.lbl_duration.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_row.addWidget(self.lbl_duration)

        deck_layout.addLayout(progress_row)

        # ── Row 4: Main Transport Controls (-10s | Play/Pause/Replay | +10s | Stop)
        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(10)

        self.lbl_state_hint = QLabel('Ready')
        self.lbl_state_hint.setObjectName('stateHint')
        controls_row.addWidget(self.lbl_state_hint)

        controls_row.addStretch(1)

        # Jump Backward 10s
        self.btn_back = QPushButton()
        self.btn_back.setObjectName('transportSecondaryBtn')
        self.btn_back.setIcon(lucide_icon('rotate-ccw', '#E5E7EB', 17))
        self.btn_back.setIconSize(QSize(17, 17))
        self.btn_back.setToolTip('Jump Backward 10s')
        self.btn_back.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_back.clicked.connect(lambda: self.controller.jump_backward(10.0))
        controls_row.addWidget(self.btn_back)

        # Hero Play / Pause / Replay Button
        self.btn_play_pause = QPushButton()
        self.btn_play_pause.setObjectName('transportHeroBtn')
        self.btn_play_pause.setIcon(lucide_icon('pause', '#FFFFFF', 19))
        self.btn_play_pause.setIconSize(QSize(19, 19))
        self.btn_play_pause.setToolTip('Pause / Resume')
        self.btn_play_pause.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_play_pause.clicked.connect(self._on_hero_clicked)
        controls_row.addWidget(self.btn_play_pause)

        # Jump Forward 10s
        self.btn_fwd = QPushButton()
        self.btn_fwd.setObjectName('transportSecondaryBtn')
        self.btn_fwd.setIcon(lucide_icon('rotate-cw', '#E5E7EB', 17))
        self.btn_fwd.setIconSize(QSize(17, 17))
        self.btn_fwd.setToolTip('Jump Forward 10s')
        self.btn_fwd.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_fwd.clicked.connect(lambda: self.controller.jump_forward(10.0))
        controls_row.addWidget(self.btn_fwd)

        controls_row.addStretch(1)

        # Stop Button
        self.btn_stop = QPushButton()
        self.btn_stop.setObjectName('transportStopBtn')
        self.btn_stop.setIcon(lucide_icon('square', '#FCA5A5', 15))
        self.btn_stop.setIconSize(QSize(15, 15))
        self.btn_stop.setToolTip('Stop Playback')
        self.btn_stop.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        controls_row.addWidget(self.btn_stop)

        deck_layout.addLayout(controls_row)

        # ── Row 5: Chunked Generation Progress Strip (ChunkedProgressUI) ──────
        self.chunk_box = QFrame(self.deck)
        self.chunk_box.setObjectName('chunkBox')
        cb_layout = QVBoxLayout(self.chunk_box)
        cb_layout.setContentsMargins(10, 8, 10, 8)
        cb_layout.setSpacing(6)

        cb_top = QHBoxLayout()
        cb_top.setContentsMargins(0, 0, 0, 0)
        self.lbl_chunk_phase = QLabel('Generating audio chunks…')
        self.lbl_chunk_phase.setObjectName('chunkPhaseText')
        cb_top.addWidget(self.lbl_chunk_phase)
        cb_top.addStretch(1)
        self.lbl_chunk_pct = QLabel('0%')
        self.lbl_chunk_pct.setObjectName('chunkPctText')
        cb_top.addWidget(self.lbl_chunk_pct)
        cb_layout.addLayout(cb_top)

        # Overall chunk progress bar
        self.chunk_bar_bg = QFrame()
        self.chunk_bar_bg.setObjectName('chunkBarBg')
        self.chunk_bar_bg.setFixedHeight(5)
        self.chunk_bar_fill = QFrame(self.chunk_bar_bg)
        self.chunk_bar_fill.setObjectName('chunkBarFill')
        self.chunk_bar_fill.setGeometry(0, 0, 0, 5)
        cb_layout.addWidget(self.chunk_bar_bg)

        # Individual chunk status pills row
        self.chunk_pills_widget = QWidget()
        self.chunk_pills_layout = QHBoxLayout(self.chunk_pills_widget)
        self.chunk_pills_layout.setContentsMargins(0, 0, 0, 0)
        self.chunk_pills_layout.setSpacing(4)
        cb_layout.addWidget(self.chunk_pills_widget)

        self.chunk_box.setVisible(False)
        deck_layout.addWidget(self.chunk_box)

        # ── Row 6: Collapsible Interactive Transcript Drawer ──────────────────
        self.transcript_box = QFrame(self.deck)
        self.transcript_box.setObjectName('transcriptBox')
        tb_layout = QVBoxLayout(self.transcript_box)
        tb_layout.setContentsMargins(8, 8, 8, 8)
        tb_layout.setSpacing(6)

        tb_hdr = QHBoxLayout()
        tb_hdr.setContentsMargins(2, 0, 2, 0)
        self.lbl_transcript_hdr = QLabel('Live Transcript (click sentence to jump)')
        self.lbl_transcript_hdr.setObjectName('transcriptHeader')
        tb_hdr.addWidget(self.lbl_transcript_hdr)
        tb_hdr.addStretch(1)
        self.lbl_hotkey_hint = QLabel('Space · ←/→ · [/]')
        self.lbl_hotkey_hint.setObjectName('hotkeyHint')
        tb_hdr.addWidget(self.lbl_hotkey_hint)
        tb_layout.addLayout(tb_hdr)

        self.transcript_scroll = QScrollArea(self.transcript_box)
        self.transcript_scroll.setObjectName('transcriptScroll')
        self.transcript_scroll.setWidgetResizable(True)
        self.transcript_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript_scroll.setFixedHeight(130)

        self.transcript_list_widget = QWidget()
        self.transcript_list_widget.setObjectName('transcriptListWidget')
        self.transcript_list_layout = QVBoxLayout(self.transcript_list_widget)
        self.transcript_list_layout.setContentsMargins(2, 2, 2, 2)
        self.transcript_list_layout.setSpacing(4)
        self.transcript_scroll.setWidget(self.transcript_list_widget)
        tb_layout.addWidget(self.transcript_scroll)

        self.transcript_box.setVisible(False)
        deck_layout.addWidget(self.transcript_box)

        outer.addWidget(self.deck)
        self.apply_theme()
        self.apply_button_settings()

    def apply_button_settings(self, st: Optional[dict] = None):
        """Show/hide individual HUD and drawer buttons according to user settings."""
        if st is None:
            try:
                st = TTSSettingsStore().load()
            except Exception:
                st = {}

        # Primary / Essential Row Controls
        if hasattr(self, 'btn_speed'):
            self.btn_speed.setVisible(st.get('hud_btn_speed', 'yes') == 'yes')
        if hasattr(self, 'lbl_mode_tag'):
            self.lbl_mode_tag.setVisible(st.get('hud_btn_mode_tag', 'yes') == 'yes')
        if hasattr(self, 'btn_close'):
            self.btn_close.setVisible(st.get('hud_btn_close', 'yes') == 'yes')
        if hasattr(self, 'btn_back'):
            self.btn_back.setVisible(st.get('hud_btn_back', 'yes') == 'yes')
        if hasattr(self, 'btn_play_pause'):
            self.btn_play_pause.setVisible(st.get('hud_btn_play_pause', 'yes') == 'yes')
        if hasattr(self, 'btn_fwd'):
            self.btn_fwd.setVisible(st.get('hud_btn_fwd', 'yes') == 'yes')
        if hasattr(self, 'btn_stop'):
            self.btn_stop.setVisible(st.get('hud_btn_stop', 'yes') == 'yes')

        # Scrubber / Timeline Row
        show_prog = (st.get('hud_show_progress', 'yes') == 'yes')
        if hasattr(self, 'lbl_current'):
            self.lbl_current.setVisible(show_prog)
        if hasattr(self, 'slider'):
            self.slider.setVisible(show_prog)
        if hasattr(self, 'lbl_duration'):
            self.lbl_duration.setVisible(show_prog)

        # Chunk progress strip
        self._cfg_show_chunks = (st.get('hud_show_chunks', 'yes') == 'yes')
        if hasattr(self, 'chunk_box'):
            if not self._cfg_show_chunks:
                self.chunk_box.setVisible(False)

        # More Tools Drawer Buttons
        drawer_buttons = [
            (getattr(self, 'btn_enqueue', None), st.get('hud_btn_enqueue', 'yes') == 'yes'),
            (getattr(self, 'btn_transcript', None), st.get('hud_btn_transcript', 'yes') == 'yes'),
            (getattr(self, 'btn_queue', None), st.get('hud_btn_queue', 'yes') == 'yes'),
            (getattr(self, 'btn_ocr', None), st.get('hud_btn_ocr', 'yes') == 'yes'),
            (getattr(self, 'btn_save', None), st.get('hud_btn_save', 'yes') == 'yes'),
            (getattr(self, 'btn_bookmark', None), st.get('hud_btn_bookmark', 'yes') == 'yes'),
            (getattr(self, 'btn_anki', None), st.get('hud_btn_anki', 'yes') == 'yes'),
            (getattr(self, 'btn_sleep', None), st.get('hud_btn_sleep', 'yes') == 'yes'),
            (getattr(self, 'btn_settings', None), st.get('hud_btn_settings', 'yes') == 'yes'),
        ]
        visible_drawer_count = 0
        for btn, is_vis in drawer_buttons:
            if btn is not None:
                btn.setVisible(is_vis)
                if is_vis:
                    visible_drawer_count += 1

        # More Tools Drawer Toggle (•••)
        show_more_cfg = (st.get('hud_btn_more', 'yes') == 'yes')
        can_show_more = show_more_cfg and (visible_drawer_count > 0)
        if hasattr(self, 'btn_more'):
            self.btn_more.setVisible(can_show_more)

        auto_expand = (st.get('hud_expand_tools_drawer', 'no') == 'yes')
        if hasattr(self, 'tools_box'):
            if visible_drawer_count == 0 or not can_show_more:
                self.tools_box.setVisible(False)
                if hasattr(self, 'btn_more'):
                    self.btn_more.setChecked(False)
            elif auto_expand:
                self.tools_box.setVisible(True)
                if hasattr(self, 'btn_more'):
                    self.btn_more.setChecked(True)
            elif not getattr(self, '_user_toggled_drawer', False):
                self.tools_box.setVisible(False)
                if hasattr(self, 'btn_more'):
                    self.btn_more.setChecked(False)
        self.adjustSize()

    def _on_speed_wheel(self, event):
        """Allow fine-grained speed adjustment via scroll wheel over the speed pill (+/- 0.05x)."""
        delta = event.angleDelta().y()
        if delta != 0:
            step = 0.05 if delta > 0 else -0.05
            new_spd = round(self.controller.speed + step, 2)
            self.controller.set_speed(new_spd)
            eff = round(new_spd * getattr(self.controller, 'active_rate_mult', 1.0), 2)
            self.btn_speed.setText(f"{eff:g}x")
            event.accept()
            return
        event.ignore()

    def apply_theme(self, theme: Optional[str] = None):
        target = theme or get_effective_theme(QApplication.instance())
        if target == getattr(self, '_current_theme', None) and getattr(self, '_theme_applied', False):
            return
        self._current_theme = target
        self._theme_applied = True
        self.setStyleSheet(get_player_hud_stylesheet(target))

        icon_color = '#475569' if target == 'light' else '#9CA3AF'
        sec_color = '#334155' if target == 'light' else '#E5E7EB'
        stop_color = '#E11D48' if target == 'light' else '#FCA5A5'

        for btn in (
            getattr(self, 'btn_more', None),
            getattr(self, 'btn_enqueue', None),
            getattr(self, 'btn_transcript', None),
            getattr(self, 'btn_queue', None),
            getattr(self, 'btn_ocr', None),
            getattr(self, 'btn_save', None),
            getattr(self, 'btn_bookmark', None),
            getattr(self, 'btn_anki', None),
            getattr(self, 'btn_sleep', None),
            getattr(self, 'btn_settings', None),
            getattr(self, 'btn_close', None),
        ):
            if btn is not None:
                iname = btn.property('iconName')
                if iname:
                    btn.setIcon(lucide_icon(iname, icon_color, 14))

        if hasattr(self, 'btn_back'):
            self.btn_back.setIcon(lucide_icon('rotate-ccw', sec_color, 17))
        if hasattr(self, 'btn_fwd'):
            self.btn_fwd.setIcon(lucide_icon('rotate-cw', sec_color, 17))
        if hasattr(self, 'btn_stop'):
            self.btn_stop.setIcon(lucide_icon('square', stop_color, 15))

        # Re-polish any active transcript rows to match theme
        if hasattr(self, '_transcript_rows'):
            for row in self._transcript_rows:
                try:
                    row.style().unpolish(row)
                    row.style().polish(row)
                    lbl = row.findChild(QLabel)
                    if lbl is not None:
                        lbl.style().unpolish(lbl)
                        lbl.style().polish(lbl)
                except Exception:
                    pass

    def _make_util_btn(self, icon_name: str, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setProperty('class', 'utilBtn')
        btn.setProperty('iconName', icon_name)
        color = '#475569' if getattr(self, '_current_theme', '') == 'light' else '#9CA3AF'
        btn.setIcon(lucide_icon(icon_name, color, 14))
        btn.setIconSize(QSize(14, 14))
        btn.setToolTip(tooltip)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        return btn

    def _restore_position(self):
        pos = load_ui_positions().get('floating_player')
        primary = QApplication.primaryScreen()
        avail = primary.availableGeometry() if primary else QRect(0, 0, 1920, 1080)
        target_x = max(10, int(avail.right()) - self.width() - 28)
        target_y = max(10, int(avail.bottom()) - 185)
        if pos and isinstance(pos.get('x'), int) and isinstance(pos.get('y'), int):
            px, py = pos['x'], pos['y']
            target_pt = QPoint(px, py)
            screen = QApplication.screenAt(target_pt) or primary
            if screen is not None:
                s_avail = screen.availableGeometry()
                target_x = max(int(s_avail.left()) + 10, min(px, int(s_avail.right()) - self.width() - 10))
                target_y = max(int(s_avail.top()) + 10, min(py, int(s_avail.bottom()) - 150))
        self.move(target_x, target_y)

    def update_from_state(self, daemon_state: str):
        try:
            self._update_from_state_internal(daemon_state)
        except Exception as exc:
            logger.debug("update_from_state error: %s", exc)

    def _update_from_state_internal(self, daemon_state: str):
        # Poll and adapt to OS or user theme changes (system default / dark / light)
        target_theme = get_effective_theme(QApplication.instance())
        if target_theme != getattr(self, '_current_theme', 'dark'):
            self.apply_theme(target_theme)
            if hasattr(self, 'queue_window'):
                self.queue_window.apply_theme(target_theme)

        # Dynamic live button settings reload when settings.json changes
        try:
            settings_file = os.path.join(CONFIG_DIR, 'settings.json')
            if os.path.isfile(settings_file):
                mtime = os.path.getmtime(settings_file)
                if getattr(self, '_last_settings_mtime', 0.0) != mtime:
                    self._last_settings_mtime = mtime
                    self.apply_button_settings()
        except Exception:
            pass

        # Sleep Timer countdown & auto-fade logic
        now = time.time()
        elapsed = now - getattr(self, '_last_sleep_tick', now)
        if elapsed >= 1.0:
            self._last_sleep_tick = now
            if getattr(self, 'sleep_timer_seconds', 0) > 0:
                self.sleep_timer_seconds -= int(elapsed)
                if self.sleep_timer_seconds <= 30 and self.sleep_timer_seconds > 0:
                    fade_vol = max(10, int(self.sleep_timer_seconds * 3.3))
                    self.controller.mpv_set('volume', fade_vol)
                elif self.sleep_timer_seconds <= 0:
                    self.sleep_timer_seconds = 0
                    self.sleep_timer_mode = 0
                    self.controller.stop()
                    self._update_sleep_btn_ui()
                    subprocess.Popen(
                        ['notify-send', 'Speak a Loud', 'Sleep timer expired — playback stopped.'],
                        env=get_sanitized_env(),
                    )
            elif getattr(self, 'sleep_timer_mode', 0) == -1:
                self._update_sleep_btn_ui()

        # Poll queue file changes
        self.queue_window.load_from_disk()
        self.btn_queue.setChecked(self.queue_window.isVisible())

        data = self.controller.refresh(daemon_state)
        sid = self.controller.session_id

        # If sleep timer mode is 'End of passage' (-1) and playback reached end:
        if getattr(self, 'sleep_timer_mode', 0) == -1 and data.get('is_at_end'):
            self.sleep_timer_mode = 0
            self.sleep_timer_seconds = 0
            self.controller.stop()
            self._update_sleep_btn_ui()
            subprocess.Popen(
                ['notify-send', 'Speak a Loud', 'Reached end of passage — playback stopped.'],
                env=get_sanitized_env(),
            )

        # Check if we should auto-advance the queue when an item finishes
        if data['is_at_end'] and self.queue_window.is_playing_from_queue:
            next_idx = self.queue_window.current_index + 1
            if next_idx < len(self.queue_window.queue_items):
                self._play_queue_item(next_idx)
                return
            elif self.queue_window.loop_enabled and len(self.queue_window.queue_items) > 0:
                self._play_queue_item(0)
                return
            else:
                self.queue_window.is_playing_from_queue = False
                self.queue_window.save_to_disk()
                self.queue_window._rebuild_list()

        # Auto-show floating player when active or in replay state (if enabled in settings)
        show_pref = read_bool_config('show_hud', read_bool_config('show_floating_player', True))
        replay_pref = read_bool_config('replay_at_end', read_bool_config('enable_replay', True))

        is_active = daemon_state in ('GENERATING', 'RETRYING', 'PLAYING', 'PAUSED') or data['is_playing']
        should_stay_for_replay = replay_pref and data['is_at_end'] and sid != '' and sid != self.user_dismissed_session

        if is_active and sid != self.user_dismissed_session and show_pref:
            if not self.isVisible():
                self.show()
                self.raise_()
        elif not is_active and not should_stay_for_replay and not getattr(self, 'manually_opened', False):
            if self.isVisible() and not self.queue_window.isVisible():
                self.hide()

        if not self.isVisible():
            return

        title = data.get('title', 'Speech')
        self.title_lbl.setText(title)
        self.title_lbl.setToolTip(title)

        if hasattr(self, 'lbl_mode_tag'):
            raw_title = str(title).strip()
            if not raw_title or raw_title.lower() in ('speak a loud', 'speech', 'selected text'):
                tag_txt = 'Speech'
            elif len(raw_title) > 16:
                tag_txt = raw_title[:15].rstrip() + '…'
            else:
                tag_txt = raw_title
            self.lbl_mode_tag.setText(tag_txt)
            self.lbl_mode_tag.setToolTip(raw_title)

        eff_spd = data.get('effective_speed', data.get('speed', 1.0))
        raw_spd = data.get('speed', 1.0)
        a_lang = data.get('active_lang', 'en').upper()
        a_rate = data.get('active_rate', '+0%')
        self.btn_speed.setText(f"{eff_spd:g}x")
        self.btn_speed.setToolTip(
            f"Speaking Speed: {eff_spd:g}x [{a_lang} rate {a_rate} × player {raw_spd:g}x]\n"
            f"Scroll wheel: ±0.05x | Click: cycle | Right-click: menu"
        )

        if self.queue_window.is_playing_from_queue and self.queue_window.queue_items:
            q_idx = self.queue_window.current_index + 1
            q_tot = len(self.queue_window.queue_items)
            self.queue_banner.setText(f'Queue: {q_idx}/{q_tot}')
            self.queue_banner.setVisible(True)
        else:
            self.queue_banner.setVisible(False)

        # Update Timeline Scrubber
        dur = data['duration']
        cur = data['current_time']
        self.lbl_duration.setText(format_time(dur))
        if not self.slider.is_dragging():
            self.lbl_current.setText(format_time(cur))
            if dur > 0:
                ratio = max(0.0, min(1.0, cur / dur))
                self.slider.blockSignals(True)
                self.slider.setValue(int(round(ratio * 1000)))
                self.slider.blockSignals(False)
            else:
                self.slider.blockSignals(True)
                self.slider.setValue(0)
                self.slider.blockSignals(False)

        self.slider.setEnabled(dur > 0 and not data['is_loading'])
        self.btn_back.setEnabled(dur > 0 and not data['is_loading'])
        self.btn_fwd.setEnabled(dur > 0 and not data['is_loading'])

        # Update Hero Button & State Hint
        if data['is_loading']:
            self.status_dot.setStyleSheet('color: #F59E0B; font-size: 11px;')
            self.lbl_state_hint.setText('Synthesizing…')
            self.btn_play_pause.setIcon(lucide_icon('loader-2', '#FFFFFF', 19))
            self.btn_play_pause.setToolTip('Generating neural speech…')
        elif data['is_at_end']:
            self.status_dot.setStyleSheet('color: #818CF8; font-size: 11px;')
            self.lbl_state_hint.setText('Finished')
            self.btn_play_pause.setIcon(lucide_icon('rotate-cw', '#FFFFFF', 19))
            self.btn_play_pause.setToolTip('Replay from beginning')
        elif data['is_playing']:
            self.status_dot.setStyleSheet('color: #10B981; font-size: 11px;')
            self.lbl_state_hint.setText('Playing')
            self.btn_play_pause.setIcon(lucide_icon('pause', '#FFFFFF', 19))
            self.btn_play_pause.setToolTip('Pause (Super+P)')
        else:
            self.status_dot.setStyleSheet('color: #F97316; font-size: 11px;')
            self.lbl_state_hint.setText('Paused')
            self.btn_play_pause.setIcon(lucide_icon('play', '#FFFFFF', 19))
            self.btn_play_pause.setToolTip('Resume (Super+P)')

        # Update Chunked Progress UI section
        tot_c = data['total_chunks']
        comp_c = data['completed_chunks']
        fail_c = data['failed_chunks']
        pct = data['overall_progress']

        if tot_c > 0 and (comp_c + fail_c < tot_c or data['is_loading']):
            self._chunks_completed_at = 0.0
            if getattr(self, '_cfg_show_chunks', True) and not self.chunk_box.isVisible():
                self.chunk_box.setVisible(True)
                self.adjustSize()
            self.lbl_chunk_phase.setText(f'Generating chunks ({comp_c}/{tot_c})…')
            self.lbl_chunk_pct.setText(f'{pct}%')
            bar_w = max(4, int((self.chunk_bar_bg.width() or 290) * (pct / 100.0)))
            self.chunk_bar_fill.setGeometry(0, 0, bar_w, 5)
            self._sync_chunk_pills(data['chunk_statuses'])
        else:
            if self._chunks_completed_at == 0.0 and tot_c > 1:
                self._chunks_completed_at = time.time()
                self.lbl_chunk_phase.setText(f'All {tot_c} chunks ready')
                self.lbl_chunk_pct.setText('100%')
                self.chunk_bar_fill.setGeometry(0, 0, self.chunk_bar_bg.width() or 290, 5)
                self._sync_chunk_pills(data['chunk_statuses'])
            if self._chunks_completed_at > 0 and (time.time() - self._chunks_completed_at) > 1.8:
                if self.chunk_box.isVisible():
                    self.chunk_box.setVisible(False)
                    self.adjustSize()
            elif tot_c <= 1 and not data['is_loading']:
                if self.chunk_box.isVisible():
                    self.chunk_box.setVisible(False)
                    self.adjustSize()

        # Sync Interactive Transcript Drawer if visible
        if self.transcript_box.isVisible():
            self._sync_transcript(sid, data.get('chunks_meta', []), data.get('active_segment_index', 0))

    def _sync_transcript(self, session_id: str, chunks_meta: List[dict], active_idx: int):
        if session_id != self._transcript_session_id or len(self._transcript_rows) != len(chunks_meta):
            self._transcript_session_id = session_id
            self._transcript_active_idx = -1
            self._transcript_rows.clear()
            self._segment_words.clear()
            while self.transcript_list_layout.count():
                child = self.transcript_list_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            for idx, cmeta in enumerate(chunks_meta):
                row = QFrame()
                row.setProperty('class', 'transcriptRow')
                row.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                rl = QHBoxLayout(row)
                rl.setContentsMargins(7, 5, 7, 5)
                rl.setSpacing(6)

                lang_tag = str(cmeta.get('lang', 'en')).upper()
                txt = str(cmeta.get('text', '')).strip()
                snippet = txt if len(txt) <= 85 else txt[:82].rstrip() + '…'
                lbl = QLabel(f'{idx + 1}. [{lang_tag}] {snippet}')
                lbl.setObjectName(f'transcriptLbl_{idx}')
                lbl.setProperty('class', 'transcriptText')
                lbl.setWordWrap(True)
                lbl.setToolTip(txt)
                rl.addWidget(lbl, 1)

                row.mousePressEvent = lambda e, i=idx: (
                    self.controller.jump_to_segment(i) if e.button() == Qt.MouseButton.LeftButton else None
                )
                self.transcript_list_layout.addWidget(row)
                self._transcript_rows.append(row)
            self.transcript_list_layout.addStretch(1)

        # Update row active state styling when segment index changes
        if active_idx != self._transcript_active_idx and 0 <= active_idx < len(self._transcript_rows):
            prev_idx = self._transcript_active_idx
            self._transcript_active_idx = active_idx
            if 0 <= prev_idx < len(self._transcript_rows) and prev_idx < len(chunks_meta):
                prev_row = self._transcript_rows[prev_idx]
                prev_row.setProperty('class', 'transcriptRow')
                prev_row.style().unpolish(prev_row)
                prev_row.style().polish(prev_row)
                prev_lbl = prev_row.findChild(QLabel)
                if prev_lbl is not None:
                    prev_lang = str(chunks_meta[prev_idx].get('lang', 'en')).upper()
                    prev_txt = str(chunks_meta[prev_idx].get('text', '')).strip()
                    prev_snip = prev_txt if len(prev_txt) <= 85 else prev_txt[:82].rstrip() + '…'
                    prev_lbl.setText(f'{prev_idx + 1}. [{prev_lang}] {prev_snip}')
                    prev_lbl.setProperty('class', 'transcriptText')
                    prev_lbl.style().unpolish(prev_lbl)
                    prev_lbl.style().polish(prev_lbl)

            act_row = self._transcript_rows[active_idx]
            act_row.setProperty('class', 'transcriptRowActive')
            act_row.style().unpolish(act_row)
            act_row.style().polish(act_row)
            act_lbl = act_row.findChild(QLabel)
            if act_lbl is not None:
                act_lbl.setProperty('class', 'transcriptTextActive')
                act_lbl.style().unpolish(act_lbl)
                act_lbl.style().polish(act_lbl)
            self.transcript_scroll.ensureWidgetVisible(self._transcript_rows[active_idx], 0, 24)

        # Dynamic word-level karaoke tracking within the active segment
        if 0 <= active_idx < len(self._transcript_rows) and active_idx < len(chunks_meta):
            if active_idx not in self._segment_words:
                pad = f'{active_idx:03d}'
                wpath = os.path.join(WORK_DIR, f'seg_{pad}.words.json')
                if os.path.isfile(wpath):
                    try:
                        with open(wpath, 'r', encoding='utf-8') as wf:
                            self._segment_words[active_idx] = json.load(wf)
                    except Exception:
                        self._segment_words[active_idx] = []
                else:
                    self._segment_words[active_idx] = []

            words = self._segment_words.get(active_idx, [])
            if words:
                seg_start = 0.0
                for i in range(active_idx):
                    if i < len(self.controller.chunk_durations):
                        seg_start += self.controller.chunk_durations[i]
                sec_in_seg = max(0.0, self.controller.current_time - seg_start)

                active_w_idx = -1
                for widx, w in enumerate(words):
                    if w.get('start', 0.0) <= sec_in_seg <= w.get('end', 0.0):
                        active_w_idx = widx
                        break

                act_lbl = self._transcript_rows[active_idx].findChild(QLabel)
                if act_lbl is not None and active_w_idx >= 0:
                    lang_tag = str(chunks_meta[active_idx].get('lang', 'en')).upper()
                    is_light = getattr(self, '_current_theme', '') == 'light'
                    hdr_color = '#4338CA' if is_light else '#818CF8'
                    past_color = '#64748B' if is_light else '#9CA3AF'
                    future_color = '#1E293B' if is_light else '#F3F4F6'
                    parts = [f"<span style='color: {hdr_color}; font-weight: 600;'>{active_idx + 1}. [{lang_tag}]</span>"]
                    for widx, w in enumerate(words):
                        w_esc = html.escape(str(w.get('word', '')))
                        if widx < active_w_idx:
                            parts.append(f"<span style='color: {past_color};'>{w_esc}</span>")
                        elif widx == active_w_idx:
                            parts.append(f"<span style='background-color: #4F46E5; color: #FFFFFF; font-weight: 700; border-radius: 3px; padding: 1px 4px;'>{w_esc}</span>")
                        else:
                            parts.append(f"<span style='color: {future_color};'>{w_esc}</span>")
                    act_lbl.setText(' '.join(parts))

    def _sync_chunk_pills(self, statuses: List[str]):
        while self.chunk_pills_layout.count():
            item = self.chunk_pills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        max_pills = min(len(statuses), 10)
        for idx in range(max_pills):
            st = statuses[idx]
            if st == 'completed':
                sym, cls = '✓', 'chunkPillCompleted'
            elif st == 'processing':
                sym, cls = '⟳', 'chunkPillProcessing'
            elif st == 'failed':
                sym, cls = '✕', 'chunkPillFailed'
            else:
                sym, cls = '○', 'chunkPillPending'
            pill = QLabel(f'{idx + 1} {sym}')
            pill.setProperty('class', cls)
            pill.setToolTip(f'Chunk {idx + 1}: {st}')
            self.chunk_pills_layout.addWidget(pill)
        if len(statuses) > max_pills:
            more = QLabel(f'+{len(statuses) - max_pills}')
            more.setProperty('class', 'chunkPillPending')
            self.chunk_pills_layout.addWidget(more)
        self.chunk_pills_layout.addStretch(1)

    def _on_slider_moved(self, val: int):
        if self.slider.is_dragging() and self.controller.total_duration > 0:
            preview_t = (val / 1000.0) * self.controller.total_duration
            self.lbl_current.setText(format_time(preview_t))

    def _on_seek_requested(self, ratio: float):
        if self.controller.total_duration > 0:
            target = ratio * self.controller.total_duration
            self.controller.seek_global(target)

    def _on_hero_clicked(self):
        if self.controller.is_loading:
            return
        if self.controller.is_at_end:
            self.controller.replay()
        else:
            self.controller.toggle_pause()

    def _on_stop_clicked(self):
        self.manually_opened = False
        self.user_dismissed_session = self.controller.session_id
        self.queue_window.is_playing_from_queue = False
        self.queue_window.save_to_disk()
        self.controller.stop()
        self.hide()

    def _on_close_clicked(self):
        self.manually_opened = False
        self.user_dismissed_session = self.controller.session_id
        save_ui_position('floating_player', self.x(), self.y())
        self.hide()

    def _cycle_speed(self):
        cur = self.controller.speed
        next_speed = 1.0
        for s in self.SPEED_STEPS:
            if s > cur + 0.02:
                next_speed = s
                break
        else:
            next_speed = self.SPEED_STEPS[0]
        self.controller.set_speed(next_speed)
        eff = round(next_speed * getattr(self.controller, 'active_rate_mult', 1.0), 2)
        self.btn_speed.setText(f"{eff:g}x")

    def _show_speed_menu(self, pos: QPoint):
        is_light = getattr(self, '_current_theme', '') == 'light'
        bg_color = '#FFFFFF' if is_light else '#1E212B'
        text_color = '#1E293B' if is_light else '#F3F4F6'
        border_color = 'rgba(0, 0, 0, 0.12)' if is_light else 'rgba(255, 255, 255, 0.12)'
        sub_color = '#64748B' if is_light else '#9CA3AF'

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {bg_color};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 5px 16px;
                border-radius: 4px;
                font-size: 11px;
                color: {text_color};
            }}
            QMenu::item:selected {{
                background-color: #6366F1;
                color: #FFFFFF;
            }}
            QMenu::separator {{
                height: 1px;
                background: {border_color};
                margin: 4px 8px;
            }}
        """)
        active_mult = getattr(self.controller, 'active_rate_mult', 1.0)
        active_lang = getattr(self.controller, 'active_lang', 'en').upper()
        active_rate = getattr(self.controller, 'active_rate', '+0%')

        hdr = menu.addAction(f"Speaking Speed [{active_lang} {active_rate}]")
        hdr.setEnabled(False)
        menu.addSeparator()

        for s in [0.75, 1.0, 1.15, 1.25, 1.5, 1.75, 2.0]:
            eff = round(s * active_mult, 2)
            chk = '  ✓' if abs(s - self.controller.speed) < 0.03 else ''
            label = f"Player {s:g}x → Effective {eff:g}x{chk}"
            act = menu.addAction(label)
            act.triggered.connect(lambda _, spd=s, eff_s=eff: (
                self.controller.set_speed(spd),
                self.btn_speed.setText(f"{eff_s:g}x")
            ))

        menu.addSeparator()
        tune_act = menu.addAction("Configure Per-Language Rates in Settings…")
        tune_act.triggered.connect(self._on_open_settings)
        menu.exec(self.btn_speed.mapToGlobal(pos))

    def _toggle_queue_window(self):
        if self.queue_window.isVisible():
            self.queue_window.hide()
            self.btn_queue.setChecked(False)
        else:
            self.queue_window.load_from_disk(force=True)
            self.queue_window.show()
            self.queue_window.raise_()
            self.btn_queue.setChecked(True)

    def _play_queue_item(self, index: int):
        self.queue_window.load_from_disk(force=True)
        if 0 <= index < len(self.queue_window.queue_items):
            item = self.queue_window.queue_items[index]
            self.queue_window.current_index = index
            self.queue_window.is_playing_from_queue = True
            self.queue_window.save_to_disk()
            self.queue_window._rebuild_list()
            text = item.get('text', '')
            if text:
                self.user_dismissed_session = ''
                subprocess.Popen(
                    ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--text', text],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

    def _on_save_mp3(self):
        safe_name = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in self.controller.title).strip() or 'speech'
        default_path = os.path.expanduser(f'~/Downloads/{safe_name[:40]}.mp3')
        dest, _ = QFileDialog.getSaveFileName(
            self,
            'Save Spoken Audio as MP3',
            default_path,
            'MP3 Audio (*.mp3)',
        )
        if dest:
            if not dest.lower().endswith('.mp3'):
                dest += '.mp3'
            self.controller.export_mp3(dest)

    def _toggle_tools_drawer(self):
        vis = not self.tools_box.isVisible()
        self.tools_box.setVisible(vis)
        self.btn_more.setChecked(vis)
        self._user_toggled_drawer = True
        self.adjustSize()

    def _step_speed(self, direction: int):
        cur = self.controller.speed
        idx = 0
        best_diff = 999.0
        for i, s in enumerate(self.SPEED_STEPS):
            if abs(s - cur) < best_diff:
                best_diff = abs(s - cur)
                idx = i
        new_idx = max(0, min(len(self.SPEED_STEPS) - 1, idx + direction))
        next_speed = self.SPEED_STEPS[new_idx]
        self.controller.set_speed(next_speed)
        eff = round(next_speed * getattr(self.controller, 'active_rate_mult', 1.0), 2)
        self.btn_speed.setText(f"{eff:g}x")

    def _toggle_transcript(self):
        vis = not self.transcript_box.isVisible()
        self.transcript_box.setVisible(vis)
        self.btn_transcript.setChecked(vis)
        if vis:
            self._sync_transcript(
                self.controller.session_id,
                self.controller.chunks_meta,
                self.controller.active_segment_index,
            )
        self.adjustSize()

    def _on_bookmark_clicked(self):
        bdir = os.path.expanduser('~/.local/share/speak-aloud')
        os.makedirs(bdir, exist_ok=True)
        bpath = os.path.join(bdir, 'bookmarks.md')

        idx = self.controller.active_segment_index
        text = ''
        if 0 <= idx < len(self.controller.chunks_meta):
            text = str(self.controller.chunks_meta[idx].get('text', '')).strip()
        if not text and hasattr(self, 'full_text') and self.full_text:
            text = self.full_text.strip()
        if not text:
            text = self.title_lbl.text().strip() or 'Audio passage'

        cur_t = format_time(self.controller.current_time)
        tot_t = format_time(self.controller.total_duration)
        now_str = time.strftime('%Y-%m-%d %H:%M:%S')

        entry = (
            f"### {now_str} — [{cur_t} / {tot_t}] (Speed {self.controller.speed:g}x)\n"
            f"> {text}\n\n"
        )
        saved_ok = False
        try:
            with open(bpath, 'a', encoding='utf-8') as f:
                f.write(entry)
            saved_ok = True
        except Exception:
            pass

        # Optional: Sync note to configured Obsidian Vault
        try:
            store = TTSSettingsStore()
            obs_vault = store.get('obsidian_vault', '').strip()
            obs_folder = store.get('obsidian_folder', 'Reading Notes').strip() or 'Reading Notes'
            if obs_vault:
                vault_dir = os.path.expanduser(obs_vault)
                if os.path.isdir(vault_dir):
                    target_dir = os.path.join(vault_dir, obs_folder)
                    os.makedirs(target_dir, exist_ok=True)
                    obs_file = os.path.join(target_dir, 'TTS Bookmarks.md')
                    obs_entry = (
                        f"\n## {now_str} — [{cur_t} / {tot_t}]\n"
                        f"#tts/bookmark #spoken-quote\n\n"
                        f"> [!quote] Spoken Passage\n"
                        f"> {text}\n\n"
                        f"- **Source**: {self.title_lbl.text().strip()}\n"
                        f"- **Timestamp**: `{cur_t} / {tot_t}` (Speed: {self.controller.speed:g}x)\n"
                    )
                    with open(obs_file, 'a', encoding='utf-8') as of:
                        of.write(obs_entry)
                else:
                    import urllib.parse
                    content = f"#tts/bookmark\n\n> {text}\n\n- Time: {cur_t} / {tot_t}\n"
                    params = urllib.parse.urlencode({
                        'vault': obs_vault,
                        'name': f"TTS Note - {time.strftime('%Y-%m-%d %H-%M')}",
                        'content': content,
                    })
                    subprocess.Popen(['xdg-open', f"obsidian://new?{params}"], env=get_sanitized_env())
        except Exception:
            pass

        if saved_ok:
            self.btn_bookmark.setIcon(lucide_icon('check', '#34D399', 14))
            self.btn_bookmark.setToolTip('★ Bookmarked!')
            QTimer.singleShot(1600, lambda: (
                self.btn_bookmark.setIcon(lucide_icon('bookmark', '#9CA3AF', 14)),
                self.btn_bookmark.setToolTip('Bookmark current passage & timestamp (Hotkey: B)')
            ))

    def _on_anki_clicked(self):
        from tts_anki import export_to_anki

        idx = self.controller.active_segment_index
        text = ''
        if 0 <= idx < len(self.controller.chunks_meta):
            text = str(self.controller.chunks_meta[idx].get('text', '')).strip()
        if not text and hasattr(self, 'full_text') and self.full_text:
            text = self.full_text.strip()
        if not text:
            text = self.title_lbl.text().strip() or 'Audio passage'

        pad = f'{idx:03d}'
        mp3_path = os.path.join(WORK_DIR, f'seg_{pad}.mp3')
        if not os.path.isfile(mp3_path):
            mp3_path = None

        res = export_to_anki(front=text, audio_path=mp3_path)
        if res.get('success'):
            method_str = 'Anki' if res.get('method') == 'ankiconnect' else 'TSV'
            self.btn_anki.setIcon(lucide_icon('check', '#34D399', 14))
            self.btn_anki.setToolTip(f'✓ Exported to {method_str}!')
            QTimer.singleShot(1600, lambda: (
                self.btn_anki.setIcon(lucide_icon('layers', '#9CA3AF', 14)),
                self.btn_anki.setToolTip('Export passage to Anki Flashcard (Hotkey: A)')
            ))
        else:
            self.btn_anki.setIcon(lucide_icon('alert-circle', '#F87171', 14))
            self.btn_anki.setToolTip(f"Export failed: {res.get('error', 'Unknown')}")
            QTimer.singleShot(2000, lambda: (
                self.btn_anki.setIcon(lucide_icon('layers', '#9CA3AF', 14)),
                self.btn_anki.setToolTip('Export passage to Anki Flashcard (Hotkey: A)')
            ))

    def _on_slider_hover_preview(self, ratio: float, global_pos: QPoint):
        from PyQt6.QtWidgets import QToolTip
        tot = getattr(self.controller, 'total_duration', 0.0) or 0.0
        target_sec = ratio * tot
        chunks = getattr(self.controller, 'chunks_meta', []) or []

        snippet = ''
        lang = ''
        target_idx = 0
        if chunks:
            if tot > 0:
                accum = 0.0
                for idx, c in enumerate(chunks):
                    dur = float(c.get('est_duration', 2.0))
                    if accum + dur >= target_sec or idx == len(chunks) - 1:
                        snippet = str(c.get('text', '')).strip()
                        lang = str(c.get('lang', 'en')).upper()
                        target_idx = idx
                        break
                    accum += dur
            else:
                idx = min(len(chunks) - 1, max(0, int(ratio * len(chunks))))
                snippet = str(chunks[idx].get('text', '')).strip()
                lang = str(chunks[idx].get('lang', 'en')).upper()
                target_idx = idx

        if snippet:
            if len(snippet) > 85:
                snippet = snippet[:82].rstrip() + '…'
            time_str = f"{format_time(target_sec)} / {format_time(tot)}"
            tip = f"⏱ {time_str} (#{target_idx + 1})\n[{lang}] {snippet}"
        else:
            tip = f"⏱ {format_time(target_sec)} / {format_time(tot)}"

        QToolTip.showText(global_pos, tip, self.slider)

    def _cycle_sleep_timer(self):
        cycle = [0, 15 * 60, 30 * 60, 45 * 60, 60 * 60, -1]
        cur = self.sleep_timer_seconds if getattr(self, 'sleep_timer_mode', 0) != -1 else -1
        try:
            curr_idx = cycle.index(cur)
            next_val = cycle[(curr_idx + 1) % len(cycle)]
        except ValueError:
            next_val = 15 * 60
        self._set_sleep_timer(next_val)

    def _set_sleep_timer(self, seconds: int):
        self.sleep_timer_mode = -1 if seconds == -1 else (1 if seconds > 0 else 0)
        self.sleep_timer_seconds = max(0, seconds)
        self._update_sleep_btn_ui()

    def _show_sleep_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1E212B;
                color: #F3F4F6;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 5px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #6366F1;
                color: #FFFFFF;
            }
        """)
        m_off = menu.addAction("Timer Off")
        m_15 = menu.addAction("15 Minutes")
        m_30 = menu.addAction("30 Minutes")
        m_45 = menu.addAction("45 Minutes")
        m_60 = menu.addAction("60 Minutes")
        m_end = menu.addAction("Stop at End of Passage")
        action = menu.exec(self.btn_sleep.mapToGlobal(pos))
        if action == m_off:
            self._set_sleep_timer(0)
        elif action == m_15:
            self._set_sleep_timer(15 * 60)
        elif action == m_30:
            self._set_sleep_timer(30 * 60)
        elif action == m_45:
            self._set_sleep_timer(45 * 60)
        elif action == m_60:
            self._set_sleep_timer(60 * 60)
        elif action == m_end:
            self._set_sleep_timer(-1)

    def _update_sleep_btn_ui(self):
        color = '#6366F1' if (self.sleep_timer_seconds > 0 or self.sleep_timer_mode == -1) else (
            '#475569' if getattr(self, '_current_theme', '') == 'light' else '#9CA3AF'
        )
        self.btn_sleep.setChecked(self.sleep_timer_seconds > 0 or self.sleep_timer_mode == -1)
        if self.sleep_timer_mode == -1:
            self.btn_sleep.setToolTip('Sleep Timer: Stop at end of current passage (Active)')
            self.btn_sleep.setIcon(lucide_icon('moon', '#6366F1', 14))
        elif self.sleep_timer_seconds > 0:
            m = self.sleep_timer_seconds // 60
            s = self.sleep_timer_seconds % 60
            self.btn_sleep.setToolTip(f'Sleep Timer Active: {m:02d}:{s:02d} remaining (Hotkey: Z)')
            self.btn_sleep.setIcon(lucide_icon('moon', '#6366F1', 14))
        else:
            self.btn_sleep.setToolTip('Sleep / Study Timer (Off | 15m | 30m | 45m | End) (Hotkey: Z)')
            self.btn_sleep.setIcon(lucide_icon('moon', color, 14))

    def _on_ocr_clicked(self):
        subprocess.Popen(
            ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--ocr'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=get_sanitized_env(),
        )

    def _on_open_settings(self):
        if subprocess.run(['pgrep', '-f', 'tts-settings'], capture_output=True).returncode == 0:
            return
        subprocess.Popen(
            ['bash', os.path.join(SCRIPT_DIR, 'tts-settings.sh')],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=get_sanitized_env(),
        )

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key in (Qt.Key.Key_Space, Qt.Key.Key_K):
            self._on_hero_clicked()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_J):
            if hasattr(self, 'btn_back') and self.btn_back.isHidden():
                pass
            else:
                self.controller.jump_backward(10.0 if shift else 5.0)
                event.accept()
                return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_L):
            if hasattr(self, 'btn_fwd') and self.btn_fwd.isHidden():
                pass
            else:
                self.controller.jump_forward(10.0 if shift else 5.0)
                event.accept()
                return
        if key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_Minus):
            if hasattr(self, 'btn_speed') and self.btn_speed.isHidden():
                pass
            else:
                self._step_speed(-1)
                event.accept()
                return
        if key in (Qt.Key.Key_BracketRight, Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            if hasattr(self, 'btn_speed') and self.btn_speed.isHidden():
                pass
            else:
                self._step_speed(1)
                event.accept()
                return
        if key == Qt.Key.Key_T:
            if hasattr(self, 'btn_transcript') and not self.btn_transcript.isHidden():
                self._toggle_transcript()
                event.accept()
                return
        if key == Qt.Key.Key_M:
            if hasattr(self, 'btn_more') and not self.btn_more.isHidden():
                self._toggle_tools_drawer()
                event.accept()
                return
        if key == Qt.Key.Key_Q:
            if hasattr(self, 'btn_queue') and not self.btn_queue.isHidden():
                self._toggle_queue_window()
                event.accept()
                return
        if key == Qt.Key.Key_B:
            if hasattr(self, 'btn_bookmark') and not self.btn_bookmark.isHidden():
                self._on_bookmark_clicked()
                event.accept()
                return
        if key == Qt.Key.Key_A:
            if hasattr(self, 'btn_anki') and not self.btn_anki.isHidden():
                self._on_anki_clicked()
                event.accept()
                return
        if key == Qt.Key.Key_Z:
            if hasattr(self, 'btn_sleep') and not self.btn_sleep.isHidden():
                self._cycle_sleep_timer()
                event.accept()
                return
        if key == Qt.Key.Key_O:
            if hasattr(self, 'btn_ocr') and not self.btn_ocr.isHidden():
                self._on_ocr_clicked()
                event.accept()
                return
        if key == Qt.Key.Key_S:
            if hasattr(self, 'btn_stop') and not self.btn_stop.isHidden():
                self._on_stop_clicked()
                event.accept()
                return
        if key == Qt.Key.Key_Escape:
            self._on_close_clicked()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            child = self.childAt(event.pos())
            if isinstance(child, (QPushButton, QSlider)):
                super().mousePressEvent(event)
                return
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            save_ui_position('floating_player', self.x(), self.y())
            event.accept()
