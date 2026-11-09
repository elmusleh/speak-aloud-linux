#!/usr/bin/env python3
"""
MPRIS2 D-Bus Media Player Service for Speak a Loud Universal.

Exposes QtTTSDaemon as `org.mpris.MediaPlayer2.SpeakALoud` over session D-Bus.
Enables Linux desktop integration (Cinnamon sound applet, KDE Connect, lock
screen media controls, Bluetooth headsets) to control speech playback, view
current title, seek, and toggle pause/resume.
"""

import logging
import os
import sys
from typing import Any, Dict, List, Optional

try:
    from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtProperty, pyqtSignal, pyqtSlot
    from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusMessage
    PYQT_DBUS_AVAILABLE = True
except ImportError:
    PYQT_DBUS_AVAILABLE = False


logger = logging.getLogger('tts-mpris')


if PYQT_DBUS_AVAILABLE:

    class _MprisRootAdaptor(QDBusAbstractAdaptor):
        """Adapter for org.mpris.MediaPlayer2 root interface."""

        def __init__(self, parent: 'MPRISServer'):
            super().__init__(parent)
            self._server = parent
            self.setAutoRelaySignals(True)

        @pyqtProperty(str)
        def Identity(self) -> str:
            return 'Speak a Loud'

        @pyqtProperty(str)
        def DesktopEntry(self) -> str:
            return 'tts-daemon'

        @pyqtProperty(bool)
        def CanQuit(self) -> bool:
            return True

        @pyqtProperty(bool)
        def CanRaise(self) -> bool:
            return True

        @pyqtProperty(bool)
        def HasTrackList(self) -> bool:
            return False

        @pyqtProperty(list)
        def SupportedUriSchemes(self) -> List[str]:
            return ['file', 'http', 'https']

        @pyqtProperty(list)
        def SupportedMimeTypes(self) -> List[str]:
            return ['audio/mpeg', 'audio/x-wav']

        @pyqtSlot()
        def Quit(self) -> None:
            logger.info('MPRIS: Quit() received')
            self._server._on_quit()

        @pyqtSlot()
        def Raise(self) -> None:
            logger.info('MPRIS: Raise() received')
            self._server._on_raise()

    _MprisRootAdaptor = pyqtClassInfo('D-Bus Interface', 'org.mpris.MediaPlayer2')(_MprisRootAdaptor)

    class _MprisPlayerAdaptor(QDBusAbstractAdaptor):
        """Adapter for org.mpris.MediaPlayer2.Player interface."""

        Seeked = pyqtSignal(int)

        def __init__(self, parent: 'MPRISServer'):
            super().__init__(parent)
            self._server = parent
            self.setAutoRelaySignals(True)

        @pyqtProperty(str)
        def PlaybackStatus(self) -> str:
            state = getattr(self._server.daemon, '_state', 'IDLE')
            if state == 'PLAYING':
                return 'Playing'
            if state == 'PAUSED':
                return 'Paused'
            return 'Stopped'

        @pyqtProperty(str)
        def LoopStatus(self) -> str:
            return 'None'

        @pyqtProperty(float)
        def Rate(self) -> float:
            ctrl = getattr(self._server.daemon, 'controller', None)
            return float(ctrl.speed) if ctrl else 1.0

        @pyqtProperty(bool)
        def Shuffle(self) -> bool:
            return False

        @pyqtProperty(dict)
        def Metadata(self) -> Dict[str, Any]:
            ctrl = getattr(self._server.daemon, 'controller', None)
            hud = getattr(self._server.daemon, 'player_window', None)
            title = 'Speak a Loud'
            if hud and getattr(hud, 'full_text', ''):
                first_line = hud.full_text.strip().splitlines()[0]
                title = first_line[:80] + ('…' if len(first_line) > 80 else '')
            elif ctrl and ctrl.active_segment_index >= 0 and ctrl.active_segment_index < len(ctrl.chunks_meta):
                seg_txt = ctrl.chunks_meta[ctrl.active_segment_index].get('text', '')
                if seg_txt:
                    title = seg_txt[:80] + ('…' if len(seg_txt) > 80 else '')

            total_us = int((ctrl.total_duration if ctrl else 0.0) * 1_000_000)
            return {
                'mpris:trackid': '/org/mpris/MediaPlayer2/CurrentTrack',
                'xesam:title': title,
                'xesam:artist': ['Speak a Loud'],
                'xesam:album': ['Neural Speech Synthesis'],
                'mpris:length': total_us,
            }

        @pyqtProperty(float)
        def Volume(self) -> float:
            return 1.0

        @pyqtProperty(int)
        def Position(self) -> int:
            ctrl = getattr(self._server.daemon, 'controller', None)
            if not ctrl:
                return 0
            return int(getattr(ctrl, 'current_time', 0.0) * 1_000_000)

        @pyqtProperty(float)
        def MinimumRate(self) -> float:
            return 0.25

        @pyqtProperty(float)
        def MaximumRate(self) -> float:
            return 3.0

        @pyqtProperty(bool)
        def CanGoNext(self) -> bool:
            return True

        @pyqtProperty(bool)
        def CanGoPrevious(self) -> bool:
            return True

        @pyqtProperty(bool)
        def CanPlay(self) -> bool:
            return True

        @pyqtProperty(bool)
        def CanPause(self) -> bool:
            return True

        @pyqtProperty(bool)
        def CanSeek(self) -> bool:
            return True

        @pyqtProperty(bool)
        def CanControl(self) -> bool:
            return True

        @pyqtSlot()
        def Next(self) -> None:
            logger.info('MPRIS: Next() received')
            self._server.daemon._on_seek_forward()

        @pyqtSlot()
        def Previous(self) -> None:
            logger.info('MPRIS: Previous() received')
            self._server.daemon._on_seek_back()

        @pyqtSlot()
        def Pause(self) -> None:
            logger.info('MPRIS: Pause() received')
            ctrl = getattr(self._server.daemon, 'controller', None)
            if ctrl and getattr(self._server.daemon, '_state', '') == 'PLAYING':
                ctrl.toggle_pause()

        @pyqtSlot()
        def PlayPause(self) -> None:
            logger.info('MPRIS: PlayPause() received')
            self._server.daemon._on_pause()

        @pyqtSlot()
        def Stop(self) -> None:
            logger.info('MPRIS: Stop() received')
            self._server.daemon._on_stop()

        @pyqtSlot()
        def Play(self) -> None:
            logger.info('MPRIS: Play() received')
            ctrl = getattr(self._server.daemon, 'controller', None)
            if ctrl and getattr(self._server.daemon, '_state', '') == 'PAUSED':
                ctrl.toggle_pause()

        @pyqtSlot(int)
        def Seek(self, offset_us: int) -> None:
            offset_sec = offset_us / 1_000_000.0
            logger.info('MPRIS: Seek(%0.2fs) received', offset_sec)
            ctrl = getattr(self._server.daemon, 'controller', None)
            if ctrl:
                cur = getattr(ctrl, 'current_time', 0.0)
                target = max(0.0, cur + offset_sec)
                ctrl.seek_to(target)
                self.Seeked.emit(int(target * 1_000_000))

        @pyqtSlot(str, int)
        def SetPosition(self, track_id: str, pos_us: int) -> None:
            pos_sec = max(0.0, pos_us / 1_000_000.0)
            logger.info('MPRIS: SetPosition(%s, %0.2fs) received', track_id, pos_sec)
            ctrl = getattr(self._server.daemon, 'controller', None)
            if ctrl:
                ctrl.seek_to(pos_sec)
                self.Seeked.emit(int(pos_sec * 1_000_000))

        @pyqtSlot(str)
        def OpenUri(self, uri: str) -> None:
            logger.info('MPRIS: OpenUri(%s) received', uri)
            ctrl = getattr(self._server.daemon, 'controller', None)
            if ctrl:
                pass

    _MprisPlayerAdaptor = pyqtClassInfo('D-Bus Interface', 'org.mpris.MediaPlayer2.Player')(_MprisPlayerAdaptor)


class MPRISServer(QObject):
    """Encapsulates the MPRIS2 D-Bus service adapter for QtTTSDaemon."""

    def __init__(self, daemon: Any, service_name: str = 'org.mpris.MediaPlayer2.SpeakALoud'):
        super().__init__()
        self.daemon = daemon
        self.service_name = service_name
        self.registered = False
        self._bus = None

        if not PYQT_DBUS_AVAILABLE:
            logger.warning('PyQt6.QtDBus not available; MPRIS integration disabled')
            return

        self._bus = QDBusConnection.sessionBus()
        if not self._bus.isConnected():
            logger.warning('Cannot connect to D-Bus session bus')
            return

        self._root_adaptor = _MprisRootAdaptor(self)
        self._player_adaptor = _MprisPlayerAdaptor(self)

        obj_ok = self._bus.registerObject('/org/mpris/MediaPlayer2', self)
        svc_ok = self._bus.registerService(self.service_name)

        if svc_ok and obj_ok:
            self.registered = True
            logger.info('MPRIS2 service registered on D-Bus as %s', self.service_name)
        else:
            logger.warning('Failed to register MPRIS2 service (%s, %s)', obj_ok, svc_ok)

    def _on_raise(self) -> None:
        try:
            if hasattr(self.daemon, '_toggle_player'):
                if not self.daemon.player_window.isVisible():
                    self.daemon._toggle_player()
                else:
                    self.daemon.player_window.raise_()
                    self.daemon.player_window.activateWindow()
        except Exception as e:
            logger.error('Error in MPRIS Raise: %s', e)

    def _on_quit(self) -> None:
        try:
            if hasattr(self.daemon, '_on_quit'):
                self.daemon._on_quit()
        except Exception as e:
            logger.error('Error in MPRIS Quit: %s', e)

    def notify_seeked(self, position_sec: float) -> None:
        if self.registered and hasattr(self, '_player_adaptor'):
            self._player_adaptor.Seeked.emit(int(position_sec * 1_000_000))
