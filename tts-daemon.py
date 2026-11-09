#!/usr/bin/env python3
"""
TTS Tray & Floating HUD Daemon — cross-desktop tray icon, floating on-screen
player popup (FloatingPlayerUI), chunked progress monitor (ChunkedProgressUI),
playback queue manager (QueueManagerUI), media keys, and structured logging.

Auto-started on login via ~/.config/autostart/tts-daemon.desktop or by speak.sh.
Single-instance enforced via /tmp/tts-daemon.lock.
"""

import fcntl
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time

# Optional media-key support
try:
    from pynput import keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import (
    ensure_runtime_symlinks,
    get_mpv_socket_path,
    get_runtime_dir,
    get_status_path,
)
from tts_synth_worker import start_synth_server_thread

ensure_runtime_symlinks()
RUNTIME_DIR = get_runtime_dir()
STATUS_FILE = get_status_path()
LOCK_FILE = os.path.join(RUNTIME_DIR, 'daemon.lock')
MPV_SOCKET = get_mpv_socket_path()
LOG_DIR = os.path.expanduser('~/.local/share/speak-aloud')
LOG_FILE = os.path.join(LOG_DIR, 'daemon.log')

_STATE_LABELS = {
    'IDLE':       '○  Ready',
    'GENERATING': '⟳  Generating…',
    'RETRYING':   '⟳  Retrying…',
    'PLAYING':    '♪  Playing',
    'PAUSED':     '⏸  Paused',
}


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger('tts-daemon')
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1_048_576, backupCount=1
        )
        fh.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(fh)
        logger.addHandler(ch)
    return logger


def _acquire_lock(log):
    try:
        fd = open(LOCK_FILE, 'w')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except (IOError, OSError):
        log.error('Already running — exiting')
        sys.exit(0)


# ── PyQt6 Tray + Floating HUD Implementation ──────────────────────────────────

def _make_tray_qicon(state: str):
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

    px = QPixmap(64, 64)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)

    if state in ('GENERATING', 'RETRYING'):
        p.setBrush(QColor(156, 39, 176))
        p.drawEllipse(2, 2, 60, 60)
        pen = QPen(QColor(255, 255, 255), 6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(16, 16, 32, 32), 0, 270 * 16)
    elif state == 'PLAYING':
        p.setBrush(QColor(16, 185, 129))
        p.drawEllipse(2, 2, 60, 60)
        p.setBrush(QColor(255, 255, 255))
        tri = QPolygonF([QPointF(24, 18), QPointF(24, 46), QPointF(46, 32)])
        p.drawPolygon(tri)
    elif state == 'PAUSED':
        p.setBrush(QColor(249, 115, 22))
        p.drawEllipse(2, 2, 60, 60)
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(21, 18, 8, 28), 2, 2)
        p.drawRoundedRect(QRectF(35, 18, 8, 28), 2, 2)
    else:
        p.setBrush(QColor(99, 102, 241))
        p.drawEllipse(2, 2, 60, 60)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(22, 22, 20, 20)

    p.end()
    return QIcon(px)


class QtTTSDaemon:
    def __init__(self):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
        from tts_hud import FloatingPlayerWindow, MPVTimelineController, QueueManagerWindow, save_ui_position

        self.log = _setup_logging()
        self._lock_fd = _acquire_lock(self.log)
        self.log.info('QtTTSDaemon started (PID %d, runtime=%s)', os.getpid(), RUNTIME_DIR)

        # Start persistent async edge_tts synthesis worker thread on synth.sock
        self.synth_thread = start_synth_server_thread()
        self.log.info('AsyncSynthServer started on UNIX socket')

        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName('Speak a Loud')

        self._state = 'IDLE'
        self._listener = None
        self._save_ui_position = save_ui_position

        self._icons = {
            'IDLE':       _make_tray_qicon('IDLE'),
            'GENERATING': _make_tray_qicon('GENERATING'),
            'RETRYING':   _make_tray_qicon('RETRYING'),
            'PLAYING':    _make_tray_qicon('PLAYING'),
            'PAUSED':     _make_tray_qicon('PAUSED'),
        }

        self.controller = MPVTimelineController()
        self.queue_window = QueueManagerWindow()
        self.player_window = FloatingPlayerWindow(self.controller, self.queue_window)

        self._build_tray(QSystemTrayIcon, QMenu)

        # Poll status & mpv IPC every 150ms for smooth timeline scrubber updates
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(150)

        # Handle SIGINT / SIGTERM gracefully
        signal.signal(signal.SIGINT, lambda *_: self._on_quit())
        signal.signal(signal.SIGTERM, lambda *_: self._on_quit())

        if PYNPUT_AVAILABLE:
            self.log.debug('pynput available')
        else:
            self.log.warning('pynput not installed; media keys disabled')

    def _build_tray(self, QSystemTrayIcon, QMenu):
        self.tray = QSystemTrayIcon(self._icons['IDLE'], self.app)
        self.tray.setToolTip('Speak a Loud — ○ Ready')

        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #16181E;
                color: #F3F4F6;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                padding: 5px;
            }
            QMenu::item {
                padding: 6px 18px;
                border-radius: 5px;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #6366F1;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(255, 255, 255, 0.08);
                margin: 4px 8px;
            }
        """)

        act_speak = menu.addAction('Speak Selection (Super+S)')
        act_speak.triggered.connect(lambda: self._run('speak.sh'))

        act_ocr = menu.addAction('Read Screen Region via OCR…')
        act_ocr.triggered.connect(self.player_window._on_ocr_clicked)

        act_queue_add = menu.addAction('Add Selection to Queue')
        act_queue_add.triggered.connect(self.queue_window.enqueue_current_selection)

        menu.addSeparator()

        act_player = menu.addAction('Show / Hide Floating Player')
        act_player.triggered.connect(self._toggle_player)

        act_transcript = menu.addAction('Show / Hide Live Transcript')
        act_transcript.triggered.connect(self._toggle_transcript_from_tray)

        act_queue = menu.addAction('Show / Hide Playback Queue')
        act_queue.triggered.connect(self.player_window._toggle_queue_window)

        act_reset_pos = menu.addAction('Reset Floating Player Position')
        act_reset_pos.triggered.connect(self._reset_player_position)

        menu.addSeparator()

        act_pause = menu.addAction('Play / Pause (Super+P)')
        act_pause.triggered.connect(self._on_pause)

        act_stop = menu.addAction('Stop (Shift+Super+S)')
        act_stop.triggered.connect(self._on_stop)

        act_back = menu.addAction('Seek -10s')
        act_back.triggered.connect(self._on_seek_back)

        act_fwd = menu.addAction('Seek +10s')
        act_fwd.triggered.connect(self._on_seek_forward)

        menu.addSeparator()

        act_settings = menu.addAction('TTS Settings… (Super+T)')
        act_settings.triggered.connect(self._on_show_settings)

        act_quit = menu.addAction('Quit')
        act_quit.triggered.connect(self._on_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        from PyQt6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_player()

    def _toggle_player(self):
        if self.player_window.isVisible():
            self.player_window.hide()
        else:
            self.player_window.user_dismissed_session = ''
            self.player_window.show()
            self.player_window.raise_()

    def _toggle_transcript_from_tray(self):
        if not self.player_window.isVisible():
            self.player_window.user_dismissed_session = ''
            self.player_window.show()
            self.player_window.raise_()
        self.player_window._toggle_transcript()

    def _reset_player_position(self):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = int(avail.right()) - self.player_window.width() - 28
            y = int(avail.bottom()) - 185
            self.player_window.move(x, y)
            self._save_ui_position('floating_player', x, y)
        self.player_window.user_dismissed_session = ''
        self.player_window.show()
        self.player_window.raise_()

    def _tick(self):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                state = f.read().strip()
        except (FileNotFoundError, OSError):
            state = 'IDLE'

        # Self-correction: if status is active but neither speak.sh nor mpv socket is present, force IDLE
        if state in ('PLAYING', 'GENERATING', 'PAUSED', 'RETRYING'):
            if not os.path.exists(MPV_SOCKET):
                try:
                    result = subprocess.run(
                        ['pgrep', '-f', 'speak.sh'],
                        capture_output=True, timeout=0.5
                    )
                    if result.returncode != 0:
                        state = 'IDLE'
                        try:
                            with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                                f.write('IDLE')
                        except OSError:
                            pass
                        self.log.info('Self-correct: speak.sh & mpv gone — forcing IDLE')
                except Exception:
                    pass

        if state != self._state:
            self._state = state
            label = _STATE_LABELS.get(state, '○  Ready')
            self.tray.setToolTip(f'Speak a Loud — {label}')
            self.tray.setIcon(self._icons.get(state, self._icons['IDLE']))
            self.log.info('State: %s — icon updated', state)
            self._sync_media_keys(state)

        # Update Floating Player HUD & Queue Manager
        self.player_window.update_from_state(state)

    def _sync_media_keys(self, state):
        if not PYNPUT_AVAILABLE:
            return
        active = state in ('PLAYING', 'PAUSED')
        if active and self._listener is None:
            self._start_keys()
        elif not active and self._listener is not None:
            self._stop_keys()

    def _start_keys(self):
        try:
            self._listener = keyboard.Listener(on_press=self._on_key)
            self._listener.start()
            self.log.debug('Media keys active')
        except Exception as e:
            self.log.warning('Media keys failed: %s', e)
            self._listener = None

    def _stop_keys(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
            self.log.debug('Media keys released')

    def _on_key(self, key):
        try:
            name = getattr(key, 'name', None)
            if name in ('media_play_pause', 'play_pause'):
                self._on_pause()
            elif name == 'media_stop':
                self._on_stop()
            elif name in ('media_previous', 'previous'):
                self._on_seek_back()
            elif name in ('media_next', 'next'):
                self._on_seek_forward()
        except Exception as e:
            self.log.error('Key handler error: %s', e)

    def _run(self, script):
        path = os.path.join(SCRIPT_DIR, script)
        self.log.info('Run: %s', script)
        try:
            subprocess.Popen(
                ['bash', path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self.log.error('Failed to run %s: %s', script, e)

    def _on_show_settings(self):
        if subprocess.run(['pgrep', '-f', 'tts-settings'], capture_output=True).returncode == 0:
            return
        self._run('tts-settings.sh')

    def _on_pause(self):
        self.controller.toggle_pause()

    def _on_stop(self):
        self.player_window._on_stop_clicked()

    def _on_seek_back(self):
        self.log.info('Seek -10s')
        self.controller.jump_backward(10.0)

    def _on_seek_forward(self):
        self.log.info('Seek +10s')
        self.controller.jump_forward(10.0)

    def _on_quit(self):
        self.log.info('Quit requested')
        self.timer.stop()
        self._stop_keys()
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
            except OSError:
                pass
        self.tray.hide()
        self.app.quit()

    def run(self):
        sys.exit(self.app.exec())


def main():
    QtTTSDaemon().run()


if __name__ == '__main__':
    main()
