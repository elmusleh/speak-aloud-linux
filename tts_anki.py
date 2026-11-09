#!/usr/bin/env python3
"""
Anki Flashcard Export Integration for Speak a Loud.
Exports highlighted text, translation, and synthesized audio directly to Anki via
AnkiConnect (localhost:8765), with automatic fallback to anki_import.tsv + media directory.
"""

import argparse
import base64
import json
import logging
import os
import shutil
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import TTSSettingsStore

log = logging.getLogger('tts_anki')

ANKI_CONNECT_URL = 'http://127.0.0.1:8765'
DEFAULT_DATA_DIR = os.path.expanduser('~/.local/share/speak-aloud/anki')


def _invoke_anki_connect(action: str, **params) -> Any:
    """Send a JSON request to AnkiConnect."""
    payload = json.dumps({'action': action, 'version': 6, 'params': params}).encode('utf-8')
    req = urllib.request.Request(
        ANKI_CONNECT_URL,
        data=payload,
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=2.0) as resp:
        res = json.loads(resp.read().decode('utf-8'))
    if len(res) != 2:
        raise RuntimeError('Response has unexpected number of fields.')
    if 'error' not in res or 'result' not in res:
        raise RuntimeError('Response is missing required error or result field.')
    if res['error'] is not None:
        raise RuntimeError(res['error'])
    return res['result']


def is_anki_connect_running() -> bool:
    """Check if local AnkiConnect HTTP server is responding."""
    try:
        ver = _invoke_anki_connect('version')
        return isinstance(ver, int) and ver >= 6
    except Exception:
        return False


def export_to_anki(
    front: str,
    back: str = '',
    audio_path: Optional[str] = None,
    deck: Optional[str] = None,
    model: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Export note to AnkiConnect. If AnkiConnect is unavailable, append to anki_import.tsv.
    """
    if not front or not front.strip():
        return {'success': False, 'error': 'Front text is required.'}

    settings = TTSSettingsStore().load()
    deck_name = deck or settings.get('anki_deck', 'Default') or 'Default'
    model_name = model or settings.get('anki_model', 'Basic') or 'Basic'
    base_data_dir = data_dir or DEFAULT_DATA_DIR

    media_filename = ''
    media_b64 = ''
    if audio_path and os.path.isfile(audio_path) and os.path.getsize(audio_path) > 0:
        media_filename = os.path.basename(audio_path)
        try:
            with open(audio_path, 'rb') as f:
                media_b64 = base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            log.warning('Could not read audio file %s: %e', audio_path, e)
            media_filename = ''
            media_b64 = ''

    # Attempt 1: Direct AnkiConnect API
    try:
        if is_anki_connect_running():
            sound_tag = f' [sound:{media_filename}]' if media_filename else ''
            if media_filename and media_b64:
                _invoke_anki_connect('storeMediaFile', filename=media_filename, data=media_b64)

            # Auto-create deck if it doesn't already exist
            try:
                _invoke_anki_connect('createDeck', deck=deck_name)
            except Exception:
                pass

            note_params = {
                'note': {
                    'deckName': deck_name,
                    'modelName': model_name,
                    'fields': {
                        'Front': front.strip() + sound_tag,
                        'Back': (back or front).strip(),
                    },
                    'options': {
                        'allowDuplicate': False,
                        'duplicateScope': 'deck',
                    },
                    'tags': ['speak-aloud'],
                }
            }
            note_id = _invoke_anki_connect('addNote', **note_params)
            return {
                'success': True,
                'method': 'ankiconnect',
                'note_id': note_id,
                'deck': deck_name,
                'front': front.strip(),
            }
    except Exception as exc:
        log.info('AnkiConnect export failed (%s), falling back to TSV import file.', exc)

    # Attempt 2: Fallback to TSV + Media Directory
    try:
        os.makedirs(base_data_dir, exist_ok=True)
        tsv_path = os.path.join(base_data_dir, 'anki_import.tsv')
        media_dir = os.path.join(base_data_dir, 'media')
        os.makedirs(media_dir, exist_ok=True)

        copied_audio_path = ''
        sound_tag = ''
        if audio_path and os.path.isfile(audio_path):
            target_media = os.path.join(media_dir, os.path.basename(audio_path))
            shutil.copy2(audio_path, target_media)
            copied_audio_path = target_media
            sound_tag = f' [sound:{os.path.basename(audio_path)}]'

        clean_front = front.strip().replace('\t', ' ').replace('\n', '<br>')
        clean_back = (back or front).strip().replace('\t', ' ').replace('\n', '<br>')
        line = f'{clean_front}{sound_tag}\t{clean_back}\tspeak-aloud\n'

        with open(tsv_path, 'a', encoding='utf-8') as f:
            f.write(line)

        return {
            'success': True,
            'method': 'tsv_fallback',
            'tsv_path': tsv_path,
            'media_path': copied_audio_path,
            'front': front.strip(),
            'deck': deck_name,
        }
    except Exception as exc:
        log.exception('TSV export fallback failed: %s', exc)
        return {'success': False, 'error': f'Failed to export note: {exc}'}


def main():
    parser = argparse.ArgumentParser(description='Export spoken text and audio to Anki flashcard.')
    parser.add_argument('--front', required=True, help='Front card text / question')
    parser.add_argument('--back', default='', help='Back card text / translation / answer')
    parser.add_argument('--audio', default='', help='Path to audio MP3 file')
    parser.add_argument('--deck', default='', help='Target Anki deck name')
    parser.add_argument('--model', default='', help='Anki model name (e.g. Basic)')
    args = parser.parse_args()

    res = export_to_anki(
        front=args.front,
        back=args.back,
        audio_path=args.audio or None,
        deck=args.deck or None,
        model=args.model or None,
    )
    print(json.dumps(res, indent=2))
    if not res.get('success'):
        sys.exit(1)


if __name__ == '__main__':
    main()
