#!/usr/bin/env python3
"""
Speech-to-Text Voice Correction & Verification Module (faster-whisper).
Synthesizes target text using edge-tts/Piper, transcribes the synthesized audio
via local faster-whisper, and measures phonetic accuracy and intelligibility.
"""

import argparse
import asyncio
import difflib
import json
import logging
import os
import re
import sys
import tempfile
from typing import Any, Dict, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import get_runtime_dir, TTSSettingsStore
from tts_synth_worker import synthesize_segment_async

log = logging.getLogger('tts_whisper')

_WHISPER_MODEL_CACHE: Dict[str, Any] = {}


def get_cached_whisper_model(model_size: str = 'tiny'):
    """Lazy-load and cache the faster-whisper model instance in memory."""
    global _WHISPER_MODEL_CACHE
    if model_size in _WHISPER_MODEL_CACHE:
        return _WHISPER_MODEL_CACHE[model_size]

    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError("faster_whisper is not installed. Install via: pip install faster-whisper") from e

    # Use CPU with int8 quantization for ultra-fast, lightweight inference
    model = WhisperModel(model_size, device='cpu', compute_type='int8')
    _WHISPER_MODEL_CACHE[model_size] = model
    return model


def clean_text_for_comparison(text: str) -> str:
    """Normalize text into lowercase alphanumeric words for fair string similarity."""
    t = re.sub(r'[^\w\s]', '', text.lower())
    return re.sub(r'\s+', ' ', t).strip()


def verify_pronunciation_with_whisper(
    text: str,
    voice: Optional[str] = None,
    lang: str = 'en',
    model_size: str = 'tiny',
) -> Dict[str, Any]:
    """
    Synthesize text to temporary audio, transcribe using faster-whisper, and return accuracy match.
    """
    if not text or not text.strip():
        return {
            'success': False,
            'error': 'Text is empty.',
            'original': '',
            'transcribed': '',
            'match_score': 0.0,
            'is_match': False,
        }

    raw_text = text.strip()
    settings = TTSSettingsStore().load()
    if not voice:
        vkey = f'voice_{lang}' if lang in ('en', 'ar', 'de') else 'voice_en'
        voice = settings.get(vkey, 'en-US-ChristopherNeural')

    rdir = get_runtime_dir()
    os.makedirs(rdir, exist_ok=True)
    fd, tmp_audio = tempfile.mkstemp(prefix='whisper_test_', suffix='.mp3', dir=rdir)
    os.close(fd)

    try:
        # Step 1: Synthesize audio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success = loop.run_until_complete(
                synthesize_segment_async(
                    text=raw_text,
                    voice=voice,
                    rate=settings.get(f'rate_{lang}', '+0%'),
                    pitch=settings.get('pitch', '+0Hz'),
                    output_path=tmp_audio,
                    lang=lang,
                    retries=2,
                )
            )
        finally:
            try:
                loop.close()
            except Exception:
                pass

        if not success or not os.path.isfile(tmp_audio) or os.path.getsize(tmp_audio) < 128:
            return {
                'success': False,
                'error': f'Failed to synthesize test audio for voice {voice}.',
                'original': raw_text,
                'transcribed': '',
                'match_score': 0.0,
                'is_match': False,
            }

        # Step 2: Transcribe synthesized audio
        model = get_cached_whisper_model(model_size=model_size)
        whisper_lang = lang if lang in ('en', 'de', 'ar') else None
        segments_gen, info = model.transcribe(
            tmp_audio,
            language=whisper_lang,
            beam_size=5,
            vad_filter=True,
        )

        transcribed_parts = [seg.text for seg in segments_gen]
        transcribed_text = ' '.join(transcribed_parts).strip()

        # Step 3: Evaluate phonetic / word match
        c_orig = clean_text_for_comparison(raw_text)
        c_trans = clean_text_for_comparison(transcribed_text)

        if not c_orig and not c_trans:
            score = 100.0
        elif not c_orig or not c_trans:
            score = 0.0
        else:
            matcher = difflib.SequenceMatcher(None, c_orig, c_trans)
            score = round(matcher.ratio() * 100.0, 1)

        is_match = score >= 80.0

        return {
            'success': True,
            'original': raw_text,
            'transcribed': transcribed_text,
            'match_score': score,
            'is_match': is_match,
            'detected_language': info.language,
            'detected_probability': round(info.language_probability, 2),
            'voice': voice,
            'audio_path': tmp_audio,
        }

    except Exception as exc:
        log.exception('Whisper verification error: %s', exc)
        return {
            'success': False,
            'error': str(exc),
            'original': raw_text,
            'transcribed': '',
            'match_score': 0.0,
            'is_match': False,
        }


def main():
    parser = argparse.ArgumentParser(description='Verify speech pronunciation via faster-whisper STT.')
    parser.add_argument('--text', required=True, help='Text or word to verify')
    parser.add_argument('--voice', default='', help='TTS voice to use')
    parser.add_argument('--lang', default='en', help='Language code (en, de, ar)')
    parser.add_argument('--model-size', default='tiny', help='Whisper model size (tiny, base, small)')
    args = parser.parse_args()

    result = verify_pronunciation_with_whisper(
        text=args.text,
        voice=args.voice or None,
        lang=args.lang,
        model_size=args.model_size,
    )
    print(json.dumps(result, indent=2))
    if not result.get('success'):
        sys.exit(1)


if __name__ == '__main__':
    main()
