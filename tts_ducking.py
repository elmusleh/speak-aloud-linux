#!/usr/bin/env python3
"""
Smart PipeWire / PulseAudio Audio Ducking for Speak a Loud Universal.

Automatically lowers background music/video player audio (e.g., Spotify,
Firefox, Chrome) while TTS is actively speaking, and restores original
levels when speech finishes or pauses.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import get_sanitized_env

logger = logging.getLogger('tts-ducking')


class AudioDucker:
    """Manages audio sink input ducking and restoration via pactl."""

    def __init__(self, duck_volume_percent: int = 40):
        self.duck_volume_percent = max(5, min(90, duck_volume_percent))
        self._ducked: Dict[str, str] = {}
        self._is_ducked: bool = False
        self._pactl = shutil.which('pactl')

    @property
    def is_available(self) -> bool:
        return self._pactl is not None

    def get_other_sink_inputs(self) -> List[Dict[str, str]]:
        """Parse `pactl list sink-inputs` to identify active non-TTS streams."""
        if not self._pactl:
            return []
        try:
            res = subprocess.run(
                [self._pactl, 'list', 'sink-inputs'],
                capture_output=True, text=True, timeout=0.6,
                env=get_sanitized_env(),
            )
            if res.returncode != 0:
                return []
        except Exception as e:
            logger.debug('pactl list failed: %s', e)
            return []

        blocks = res.stdout.split('Sink Input #')
        results: List[Dict[str, str]] = []

        for block in blocks[1:]:
            lines = block.splitlines()
            if not lines:
                continue
            input_id = lines[0].split()[0].strip()
            text = '\n'.join(lines)

            # Ignore mpv (our TTS player), speech-dispatcher, or muted inputs
            app_match = re.search(r'application\.name\s*=\s*"([^"]+)"', text)
            binary_match = re.search(r'application\.process\.binary\s*=\s*"([^"]+)"', text)
            media_role = re.search(r'media\.role\s*=\s*"([^"]+)"', text)

            app_name = (app_match.group(1) if app_match else '').lower()
            binary_name = (binary_match.group(1) if binary_match else '').lower()

            if 'mpv' in app_name or 'mpv' in binary_name:
                continue
            if 'speech-dispatcher' in app_name or 'sd_' in binary_name:
                continue
            if 'speak-aloud' in app_name:
                continue

            # Check if muted or corked
            if re.search(r'Mute:\s*yes', text, re.IGNORECASE):
                continue

            # Extract volume (e.g. "front-left: 65536 / 100% / 0.00 dB")
            vol_match = re.search(r'Volume:[^\n]*?(\d+)%', text)
            vol_str = f"{vol_match.group(1)}%" if vol_match else "100%"

            results.append({
                'id': input_id,
                'name': app_name or binary_name or f'stream_{input_id}',
                'volume': vol_str,
            })

        return results

    def duck(self) -> bool:
        """Lower volume of all other active audio streams."""
        if not self._pactl or self._is_ducked:
            return False

        streams = self.get_other_sink_inputs()
        if not streams:
            return False

        ducked_any = False
        target_vol = f'{self.duck_volume_percent}%'

        for s in streams:
            sid = s['id']
            orig_vol = s['volume']
            self._ducked[sid] = orig_vol
            try:
                subprocess.run(
                    [self._pactl, 'set-sink-input-volume', sid, target_vol],
                    capture_output=True, timeout=0.3,
                    env=get_sanitized_env(),
                )
                ducked_any = True
                logger.debug('Ducked sink-input #%s (%s) %s -> %s', sid, s['name'], orig_vol, target_vol)
            except Exception as e:
                logger.debug('Failed to duck #%s: %s', sid, e)

        self._is_ducked = ducked_any
        return ducked_any

    def unduck(self) -> bool:
        """Restore previous volume of ducked audio streams."""
        if not self._pactl or not self._is_ducked or not self._ducked:
            self._is_ducked = False
            self._ducked.clear()
            return False

        restored_any = False
        for sid, orig_vol in list(self._ducked.items()):
            try:
                subprocess.run(
                    [self._pactl, 'set-sink-input-volume', sid, orig_vol],
                    capture_output=True, timeout=0.3,
                    env=get_sanitized_env(),
                )
                restored_any = True
                logger.debug('Restored sink-input #%s to %s', sid, orig_vol)
            except Exception as e:
                logger.debug('Failed to restore #%s: %s', sid, e)

        self._ducked.clear()
        self._is_ducked = False
        return restored_any
