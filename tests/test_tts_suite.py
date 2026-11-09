#!/usr/bin/env python3
"""
Automated unit & integration test suite for Speak a Loud Universal.
"""

import json
import os
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

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
    # English technical jargon must stay 'en', followed by 'ar', followed by 'de'
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
        # First chunk must be capped <= 220 chars for sub-second startup
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
        data = json.load(open(qfile, 'r', encoding='utf-8'))
        assert len(data['items']) == 1
        assert 'First queued passage' in data['items'][0]['text']
