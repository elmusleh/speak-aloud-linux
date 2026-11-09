#!/usr/bin/env python3
"""
Automated unit & integration test suite for Speak a Loud Universal.
Covers core text processing, trilingual segmentation, fast first-chunk splitting,
timeline math, queue CLI, per-user 0700 runtime directory, schema-validated atomic
settings store, pronunciation lexicon, async synthesis worker & Piper model discovery,
OCR image extraction, live transcript drawer & HUD hotkeys, and systemd service unit.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import tts_ocr
import tts_synth_worker as sw
import tts_text_processor as tp
from tts_hud import MPVTimelineController, format_time


def test_clean_and_filter_markdown_and_symbols():
    raw = """---
title: Test Note
tags: [tts]
---
# Section 1: Overview
Check [Documentation](https://example.com/docs) at https://raw.url/test.
• Frame size: 320 x 240 >= 100 and <= 500
• **Important**: Latency ≈ 12ms"""

    cleaned = tp.clean_and_filter_text(
        raw,
        filter_markdown=True,
        filter_urls=True,
        filter_code=False,
        replace_symbols=True,
    )
    assert 'title: Test Note' not in cleaned
    assert 'https://example.com' not in cleaned
    assert 'https://raw.url' not in cleaned
    assert 'Documentation' in cleaned
    assert '320 by 240' in cleaned
    assert 'greater than or equal to' in cleaned
    assert 'less than or equal to' in cleaned
    assert 'approximately 12ms' in cleaned


def test_trilingual_segmentation_and_technical_jargon():
    sample = (
        "Single Grayscale Frame: 320 by 240. "
        "Intermediate / Filtering Buffer (e.g., Gaussian blur, Sobel gradient): 150 KB. "
        "مرحبا بكم في النظام الجديد 2026. "
        "Das System läuft äußerst zuverlässig und schnell."
    )
    segs = tp.segment_by_language(sample, source_lang='auto')
    langs = [lang for lang, _ in segs]
    assert langs == ['en', 'ar', 'de']
    assert '2026' in segs[1][1]


def test_fast_first_chunk_and_manifest():
    long_en = " ".join(
        [f"Sentence number {i} explains how progressive neural audio streaming works with minimal latency." for i in range(1, 12)]
    )
    with tempfile.TemporaryDirectory() as d:
        count = tp.process_and_write_segments(
            long_en,
            workdir=d,
            first_chunk_max=220,
            max_segment_chars=480,
        )
        assert count >= 2
        with open(os.path.join(d, 'manifest.json'), 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        assert manifest['total_chunks'] == count
        assert manifest['chunks'][0]['chars'] <= 220
        for c in manifest['chunks'][1:]:
            assert c['chars'] <= 480


def test_timeline_controller_segment_math():
    ctrl = MPVTimelineController()
    ctrl.total_chunks = 3
    ctrl.chunk_durations = [10.0, 20.0, 15.0]
    ctrl.total_duration = 45.0

    seg, off = ctrl._target_segment_and_offset(5.5)
    assert seg == 0 and abs(off - 5.5) < 1e-4

    seg, off = ctrl._target_segment_and_offset(18.0)
    assert seg == 1 and abs(off - 8.0) < 1e-4

    seg, off = ctrl._target_segment_and_offset(35.0)
    assert seg == 2 and abs(off - 5.0) < 1e-4


def test_format_time():
    assert format_time(0) == '0:00'
    assert format_time(65.2) == '1:05'
    assert format_time(125.9) == '2:06'


def test_queue_cli_mode():
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env['TMPDIR'] = tmp
        env['XDG_RUNTIME_DIR'] = tmp
        r = subprocess.run(
            ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--queue', '--text', 'First queued passage for testing.'],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert r.returncode == 0
        qfile = os.path.join(tmp, 'speak-aloud-queue.json')
        assert os.path.exists(qfile)
        with open(qfile, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert len(data['items']) == 1
        assert 'First queued passage' in data['items'][0]['text']


def test_per_user_runtime_dir_and_permissions():
    rdir = tp.get_runtime_dir()
    assert os.path.isdir(rdir)
    assert f'speak-aloud-{os.getuid()}' in rdir
    mode = stat.S_IMODE(os.stat(rdir).st_mode)
    assert mode == 0o700


def test_schema_validation_and_atomic_settings_store():
    with tempfile.TemporaryDirectory() as cdir:
        store = tp.TTSSettingsStore(config_dir=cdir)
        saved = store.save({
            'rate_en': '+999%',
            'rate_ar': '-99%',
            'global_speed': '9.5',
            'pause_punctuation': 'TRUE',
            'skip_code': 'FALSE',
            'pause_delay': '5000',
            'translate_provider': 'invalid_provider',
        })
        assert saved['rate_en'] == '+100%'
        assert saved['rate_ar'] == '-50%'
        assert saved['global_speed'] == '3.0'
        assert saved['pause_punctuation'] == 'yes'
        assert saved['skip_code'] == 'no'
        assert saved['pause_delay'] == '2000'
        assert saved['translate_provider'] == 'google'

        # Verify atomic JSON and flat file creation with 0600 permissions
        json_path = os.path.join(cdir, 'settings.json')
        assert os.path.isfile(json_path)
        assert stat.S_IMODE(os.stat(json_path).st_mode) == 0o600
        loaded = store.load()
        assert loaded['rate_en'] == '+100%'
        assert loaded['pause_punctuation'] == 'yes'


def test_pronunciation_lexicon_replacement():
    with tempfile.TemporaryDirectory() as cdir:
        lex = tp.save_lexicon({'kubectl': 'kube control', 'PyQt6': 'Pie Cute 6', 'nginx': 'engine X'}, config_dir=cdir)
        cleaned = tp.clean_and_filter_text(
            'Run kubectl and nginx with PyQt6 GUI.',
            use_lexicon=True,
            lexicon=lex,
        )
        assert 'kube control' in cleaned
        assert 'engine X' in cleaned
        assert 'Pie Cute 6' in cleaned


def test_async_synth_worker_and_piper_discovery():
    assert sw._ensure_edge_tts_importable() is True
    with tempfile.TemporaryDirectory() as mdir:
        fake_onnx = os.path.join(mdir, 'en_US-lessac-medium.onnx')
        with open(fake_onnx, 'wb') as f:
            f.write(b'ONNX_MODEL_HEADER')
        resolved = sw.resolve_piper_model(lang='en', configured_model=fake_onnx)
        assert resolved == fake_onnx


def test_ocr_image_extraction():
    from PIL import Image, ImageDraw, ImageFont

    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, 'ocr_sample.png')
        img = Image.new('RGB', (640, 140), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 34)
        except Exception:
            font = ImageFont.load_default()
        draw.text((28, 45), 'Speak Aloud OCR 2026', fill=(0, 0, 0), font=font)
        img.save(img_path, 'PNG')

        extracted = tts_ocr.extract_text_from_image(img_path, langs='eng')
        assert 'Speak' in extracted
        assert '2026' in extracted


def test_hud_transcript_and_hotkeys():
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication
    from tts_hud import FloatingPlayerWindow, QueueManagerWindow

    _app = QApplication.instance() or QApplication([])
    ctrl = MPVTimelineController()
    ctrl.chunks_meta = [
        {'id': 0, 'lang': 'en', 'text': 'First sentence in transcript.'},
        {'id': 1, 'lang': 'de', 'text': 'Zweiter Satz im Transkript.'},
    ]
    ctrl.chunk_durations = [4.0, 6.0]
    ctrl.total_duration = 10.0

    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)

    # Toggle transcript via hotkey 'T'
    assert hud.transcript_box.isHidden() is True
    ev_t = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_T, Qt.KeyboardModifier.NoModifier)
    hud.keyPressEvent(ev_t)
    assert hud.transcript_box.isHidden() is False
    assert len(hud._transcript_rows) == 2

    # Step speed up via ']'
    initial_spd = ctrl.speed
    ev_up = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_BracketRight, Qt.KeyboardModifier.NoModifier)
    hud.keyPressEvent(ev_up)
    assert ctrl.speed >= initial_spd


def test_systemd_unit_file_valid():
    unit_path = os.path.join(SCRIPT_DIR, 'speak-aloud-daemon.service')
    assert os.path.isfile(unit_path)
    with open(unit_path, 'r', encoding='utf-8') as f:
        content = f.read()
    assert '[Unit]' in content
    assert '[Service]' in content
    assert 'Restart=on-failure' in content
    assert 'tts-daemon.py' in content


def test_config_newline_safety_and_legacy_sync(tmp_path):
    from tts_text_processor import TTSSettingsStore, _format_rate_str
    store = TTSSettingsStore(config_dir=str(tmp_path))
    saved = store.save({'rate_en': '+45%', 'voice_en': 'en-US-ChristopherNeural', 'translate_from': 'auto'})
    assert saved['rate_en'] == '+45%'

    # Ensure all files end with newline so bash head/cat never concatenates adjacent tokens
    rate_file = tmp_path / 'rate_en'
    assert rate_file.read_bytes().endswith(b'\n')
    assert rate_file.read_text().strip() == '+45%'

    legacy_rate = tmp_path / 'rate'
    assert legacy_rate.read_bytes().endswith(b'\n')
    assert legacy_rate.read_text().strip() == '+45%'

    # Test edge-tts rate string normalization
    assert _format_rate_str('+45%') == '+45%'
    assert _format_rate_str('45') == '+45%'
    assert _format_rate_str('-20%') == '-20%'
    assert _format_rate_str('+0%+50%') in ('+0%', '+50%')  # Recovers to valid edge-tts rate

