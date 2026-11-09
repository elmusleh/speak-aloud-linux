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
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtDBus import QDBusMessage

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
    get_sanitized_env,
    get_status_path,
    prune_audio_cache,
    prune_runtime_dir,
    read_bool_config,
    record_playback_activity,
)
from tts_synth_worker import start_synth_server_thread
from tts_ducking import AudioDucker
from tts_mpris import MPRISServer

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


def sd_notify(state: str) -> bool:
    """Send a status notification or watchdog ping to systemd via NOTIFY_SOCKET."""
    sock_addr = os.environ.get('NOTIFY_SOCKET')
    if not sock_addr:
        return False
    if sock_addr.startswith('@'):
        sock_addr = '\0' + sock_addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(state.encode('utf-8'), sock_addr)
        return True
    except Exception:
        return False


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


class NativeX11MediaKeyListener:
    """Fallback global media key listener using libX11 when pynput is not installed."""

    def __init__(self, callback):
        self.callback = callback
        self._running = False
        self._thread = None
        self._c_handler = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        import ctypes
        try:
            x11 = ctypes.cdll.LoadLibrary('libX11.so.6')
        except OSError:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(x11,), daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self, x11):
        import ctypes
        try:
            x11.XInitThreads()
        except Exception:
            pass

        # Explicitly declare 64-bit function prototypes to prevent pointer truncation / segfaults
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XRootWindow.restype = ctypes.c_ulong
        x11.XRootWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XGrabKey.restype = ctypes.c_int
        x11.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        x11.XFlush.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XPending.restype = ctypes.c_int
        x11.XPending.argtypes = [ctypes.c_void_p]
        x11.XNextEvent.restype = ctypes.c_int
        x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        disp = x11.XOpenDisplay(None)
        if not disp:
            return

        # Suppress X errors (e.g. BadAccess) if another process grabbed the key
        err_func_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        self._c_handler = err_func_type(lambda dpy, err: 0)
        try:
            x11.XSetErrorHandler(self._c_handler)
        except Exception:
            pass

        try:
            screen = x11.XDefaultScreen(disp)
            root = x11.XRootWindow(disp, screen)
        except Exception:
            try:
                x11.XCloseDisplay(disp)
            except Exception:
                pass
            return

        keysyms = {
            0x1008FF14: 'play_pause',  # XF86AudioPlay
            0x1008FF31: 'play_pause',  # XF86AudioPause
            0x1008FF15: 'stop',        # XF86AudioStop
            0x1008FF17: 'next',        # XF86AudioNext
            0x1008FF16: 'prev',        # XF86AudioPrev
        }
        mod_masks = [0, 2, 16, 18]

        for ksym in keysyms:
            try:
                code = x11.XKeysymToKeycode(disp, ctypes.c_ulong(ksym))
                if code:
                    for mod in mod_masks:
                        x11.XGrabKey(disp, ctypes.c_int(code), ctypes.c_uint(mod), root, True, 1, 1)
            except Exception:
                pass

        try:
            x11.XFlush(disp)
        except Exception:
            pass

        event = (ctypes.c_char * 192)()
        while self._running:
            try:
                if x11.XPending(disp) > 0:
                    x11.XNextEvent(disp, event)
                    type_val = ctypes.c_int.from_buffer_copy(event[:4]).value
                    if type_val == 2:  # KeyPress
                        kc = (
                            ctypes.c_uint.from_buffer_copy(event[84:88]).value
                            if sys.maxsize > 2**32
                            else ctypes.c_uint.from_buffer_copy(event[52:56]).value
                        )
                        for ksym, action in keysyms.items():
                            if x11.XKeysymToKeycode(disp, ctypes.c_ulong(ksym)) == kc:
                                try:
                                    self.callback(action)
                                except Exception:
                                    pass
                                break
                else:
                    time.sleep(0.08)
            except Exception:
                break

        try:
            x11.XCloseDisplay(disp)
        except Exception:
            pass


class ThemeWatcher(QObject):
    """Watches FreeDesktop Settings portal signals for instant theme changes."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    @pyqtSlot(QDBusMessage)
    def on_setting_changed(self, msg: QDBusMessage):
        try:
            from tts_text_processor import invalidate_theme_cache
            invalidate_theme_cache()
            args = msg.arguments()
            ns = str(args[0]).lower() if len(args) > 0 else ''
            key = str(args[1]).lower() if len(args) > 1 else ''
            if 'interface' in ns or key in ('color-scheme', 'gtk-theme'):
                self.callback()
        except Exception:
            self.callback()


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

        # Smart PipeWire / PulseAudio background audio ducking & MPRIS2 D-Bus service
        self.ducker = AudioDucker()
        self.mpris = MPRISServer(self)

        self._build_tray(QSystemTrayIcon, QMenu)

        # Poll status & mpv IPC every 150ms for smooth timeline scrubber updates
        self._last_watchdog_time = 0.0
        self._last_scroll_idx = -1
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(150)
        sd_notify('READY=1\nSTATUS=QtTTSDaemon active')

        # Handle SIGINT / SIGTERM gracefully
        signal.signal(signal.SIGINT, lambda *_: self._on_quit())
        signal.signal(signal.SIGTERM, lambda *_: self._on_quit())

        # Real-time desktop theme change listener (org.freedesktop.portal.Settings)
        try:
            from PyQt6.QtDBus import QDBusConnection
            from tts_text_processor import invalidate_theme_cache
            self._theme_watcher = ThemeWatcher(lambda: (
                invalidate_theme_cache(),
                self.player_window.apply_theme(),
                getattr(self.player_window, 'queue_window', None) and self.player_window.queue_window.apply_theme()
            ))
            ok = QDBusConnection.sessionBus().connect(
                'org.freedesktop.portal.Desktop',
                '/org/freedesktop/portal/desktop',
                'org.freedesktop.portal.Settings',
                'SettingChanged',
                self._theme_watcher.on_setting_changed,
            )
            if ok:
                self.log.debug('Connected to portal SettingChanged D-Bus signal')
        except Exception as e:
            self.log.debug('Portal D-Bus theme listener unavailable: %s', e)

        # Initial garbage collection of old cache and runtime tmpfs artifacts
        self._last_prune_time = time.time()
        try:
            prune_audio_cache(max_size_mb=500, max_age_days=14)
            prune_runtime_dir(max_age_hours=24)
        except Exception:
            pass

        # Reading Analytics activity accumulator & Bluetooth disconnect monitor
        self._playing_accum_sec = 0.0
        self._prev_sink_is_bt = None
        self._paused_by_bluetooth = False
        self._last_bt_check = 0.0

        if PYNPUT_AVAILABLE:
            self.log.debug('pynput available for media keys')
            self._x11_listener = None
        else:
            self.log.info('Using native X11 fallback for global media keys')
            self._x11_listener = NativeX11MediaKeyListener(self._on_key_action)

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
            self.player_window.manually_opened = False
            self.player_window.hide()
        else:
            self.player_window.manually_opened = True
            self.player_window.user_dismissed_session = ''
            self.player_window.show()
            self.player_window.raise_()

    def _toggle_transcript_from_tray(self):
        if not self.player_window.isVisible():
            self.player_window.manually_opened = True
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
            self._tick_internal()
        except Exception as exc:
            self.log.error('Unexpected error in daemon _tick: %s', exc, exc_info=True)

    def _tick_internal(self):
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

        prev_state = self._state
        if state != self._state:
            self._state = state
            label = _STATE_LABELS.get(state, '○  Ready')
            self.tray.setToolTip(f'Speak a Loud — {label}')
            self.tray.setIcon(self._icons.get(state, self._icons['IDLE']))
            self.log.info('State: %s — icon updated', state)
            self._sync_media_keys(state)

            # Smart PipeWire / PulseAudio background audio ducking
            if state == 'PLAYING':
                if read_bool_config('duck_background_audio', False):
                    self.ducker.duck()
            elif prev_state == 'PLAYING' and state in ('IDLE', 'PAUSED'):
                self.ducker.unduck()

        # Reading Analytics activity tracking
        if state == 'PLAYING':
            self._playing_accum_sec += 0.15
            if self._playing_accum_sec >= 10.0:
                spd = float(getattr(self.controller, 'speed', 1.0) or 1.0)
                words = int(self._playing_accum_sec * (230.0 / 60.0) * spd)
                record_playback_activity(self._playing_accum_sec, words, spd)
                self._playing_accum_sec = 0.0
        elif prev_state == 'PLAYING' and self._playing_accum_sec > 0.0:
            spd = float(getattr(self.controller, 'speed', 1.0) or 1.0)
            words = int(self._playing_accum_sec * (230.0 / 60.0) * spd)
            record_playback_activity(self._playing_accum_sec, words, spd)
            self._playing_accum_sec = 0.0

        now = time.time()

        # Systemd watchdog heartbeat ping (every 10 seconds)
        if now - getattr(self, '_last_watchdog_time', 0.0) >= 10.0:
            self._last_watchdog_time = now
            sd_notify('WATCHDOG=1')

        # Speech-driven auto-scrolling in active window
        if state == 'PLAYING':
            cur_seg = getattr(self.controller, 'active_segment_index', 0)
            if cur_seg != getattr(self, '_last_scroll_idx', -1):
                if getattr(self, '_last_scroll_idx', -1) >= 0 and cur_seg > self._last_scroll_idx:
                    self._check_auto_scroll()
                self._last_scroll_idx = cur_seg
        else:
            self._last_scroll_idx = -1

        # Bluetooth headphone monitoring (every 1 second)
        if now - getattr(self, '_last_bt_check', 0.0) >= 1.0:
            self._last_bt_check = now
            self._check_bluetooth_audio()

        # Update Floating Player HUD & Queue Manager
        self.player_window.update_from_state(state)

        # Periodic cache & tmpfs garbage collection (every 6 hours)
        if now - getattr(self, '_last_prune_time', 0.0) > 21600:
            self._last_prune_time = now
            try:
                prune_audio_cache(max_size_mb=500, max_age_days=14)
                prune_runtime_dir(max_age_hours=24)
            except Exception:
                pass

    def _check_auto_scroll(self):
        if not read_bool_config('auto_scroll_window', False):
            return
        xdotool = shutil.which('xdotool')
        if not xdotool:
            return
        try:
            # Send 2 gentle Down arrow keypresses to smoothly scroll active reader window
            subprocess.Popen(
                [xdotool, 'key', '--clearmodifiers', 'Down', 'Down'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self.log.debug('Auto-scroll keystroke failed: %s', e)

    def _check_bluetooth_audio(self):
        if not read_bool_config('auto_pause_bluetooth', True):
            return
        pactl = shutil.which('pactl')
        if not pactl:
            return
        try:
            res = subprocess.run([pactl, 'get-default-sink'], capture_output=True, text=True, timeout=0.4)
            if res.returncode != 0:
                return
            cur_sink = res.stdout.strip().lower()
            is_bt = 'bluez' in cur_sink

            if self._prev_sink_is_bt is None:
                self._prev_sink_is_bt = is_bt
                return

            if self._prev_sink_is_bt and not is_bt:
                # Bluetooth disconnected while playing
                if self._state == 'PLAYING':
                    self.log.info('Bluetooth audio disconnected — auto-pausing playback')
                    self._paused_by_bluetooth = True
                    self._on_pause()
                    subprocess.run(
                        ['notify-send', '-a', 'Speak a Loud', '-u', 'normal', 'Headphones Disconnected', 'Speech playback paused automatically.'],
                        timeout=1.0, check=False
                    )
            elif not self._prev_sink_is_bt and is_bt:
                # Bluetooth reconnected
                if getattr(self, '_paused_by_bluetooth', False):
                    self.log.info('Bluetooth audio reconnected — rewinding 3s and resuming')
                    self._paused_by_bluetooth = False
                    self.controller.seek_relative(-3.0)
                    self._on_pause()
                    subprocess.run(
                        ['notify-send', '-a', 'Speak a Loud', '-u', 'normal', 'Headphones Reconnected', 'Resuming playback (rewound 3 seconds).'],
                        timeout=1.0, check=False
                    )
            self._prev_sink_is_bt = is_bt
        except Exception as exc:
            self.log.debug('Bluetooth sink monitor error: %s', exc)

    def _on_portal_setting_changed(self, namespace, key, value):
        try:
            ns = str(namespace).lower()
            k = str(key).lower()
            if 'interface' in ns or k in ('color-scheme', 'gtk-theme'):
                self.log.info('Desktop theme signal received (%s.%s) — updating HUD', ns, k)
                from tts_text_processor import invalidate_theme_cache
                invalidate_theme_cache()
                self.player_window.apply_theme()
                if hasattr(self.player_window, 'queue_window'):
                    self.player_window.queue_window.apply_theme()
        except Exception as e:
            self.log.debug('Theme change handling error: %s', e)

    def _sync_media_keys(self, state):
        active = state in ('PLAYING', 'PAUSED')
        if PYNPUT_AVAILABLE:
            if active and self._listener is None:
                self._start_keys()
            elif not active and self._listener is not None:
                self._stop_keys()
        elif getattr(self, '_x11_listener', None) is not None:
            if active:
                self._x11_listener.start()
            else:
                self._x11_listener.stop()

    def _on_key_action(self, action: str):
        self.log.debug('Native X11 media key pressed: %s', action)
        if action == 'play_pause':
            self._on_pause()
        elif action == 'stop':
            self._on_stop()
        elif action == 'prev':
            self._on_seek_back()
        elif action == 'next':
            self._on_seek_forward()

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
                env=get_sanitized_env(),
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
        if getattr(self, '_playing_accum_sec', 0.0) > 0.0:
            try:
                spd = float(getattr(self.controller, 'speed', 1.0) or 1.0)
                words = int(self._playing_accum_sec * (230.0 / 60.0) * spd)
                record_playback_activity(self._playing_accum_sec, words, spd)
                self._playing_accum_sec = 0.0
            except Exception:
                pass
        self.timer.stop()
        self._stop_keys()
        if getattr(self, '_x11_listener', None) is not None:
            self._x11_listener.stop()
        if hasattr(self, 'ducker'):
            self.ducker.unduck()
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
